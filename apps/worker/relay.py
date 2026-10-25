"""Outbox relay: tails the immutable ``order_events`` log and publishes each
row to a Kafka topic for downstream consumers (stream processing, warehouse
ingestion, dashboards).

Pattern: transactional outbox. The API commits domain state AND its event
rows in one database transaction; this worker never touches the API path —
it only reads the log and copies entries onto the bus. Delivery is
at-least-once: the offset row is written after a successful producer flush,
so a crash in between replays a short tail and consumers dedupe by event_id.

Run inside the compose stack:

    docker compose up -d worker        # command: python -m apps.worker.relay

Host tools reach the broker on localhost:9094 (compose port mapping);
containers reach it on kafka:19092.
"""

import json
import logging
import time
from typing import Protocol

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from core.config import Settings, get_settings
from domain.models import Base
from domain.models.order_event import OrderEvent
from domain.models.relay_state import RelayState

logger = logging.getLogger("worker.relay")

BATCH_SIZE = 100
POLL_SECONDS = 2.0
FLUSH_TIMEOUT_SECONDS = 30.0


class EventPublisher(Protocol):
    """Minimal publisher interface — swappable for tests and future brokers."""

    def produce(self, topic: str, key: str, value: str) -> None: ...

    def flush(self) -> None: ...


class KafkaEventPublisher:
    """confluent-kafka producer with idempotence enabled (acks=all, no dupes
    within a producer session)."""

    def __init__(self, bootstrap_servers: str) -> None:
        from confluent_kafka import Producer

        self._producer = Producer(
            {"bootstrap.servers": bootstrap_servers, "enable.idempotence": True}
        )

    def produce(self, topic: str, key: str, value: str) -> None:
        self._producer.produce(topic, key=key.encode("utf-8"), value=value.encode("utf-8"))

    def flush(self) -> None:
        remaining = self._producer.flush(FLUSH_TIMEOUT_SECONDS)
        if remaining:
            raise RuntimeError(f"{remaining} message(s) failed to flush within timeout")


def event_record(event: OrderEvent) -> dict:
    """Wire format for the orders.events topic."""
    return {
        "event_id": event.id,
        "order_id": event.order_id,
        "event_type": event.event_type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def relay_once(
    session: Session, publisher: EventPublisher, topic: str, batch_size: int = BATCH_SIZE
) -> int:
    """Publish the next unpublished batch of events; returns how many were sent."""
    state = session.get(RelayState, 1)
    last_id = state.last_relayed_event_id if state else 0

    events = (
        session.execute(
            select(OrderEvent)
            .where(OrderEvent.id > last_id)
            .order_by(OrderEvent.id)
            .limit(batch_size)
        )
        .scalars()
        .all()
    )
    if not events:
        return 0

    for event in events:
        publisher.produce(
            topic=topic,
            key=event.order_id,  # per-order ordering inside a partition
            value=json.dumps(event_record(event)),
        )
    publisher.flush()

    newest = events[-1].id
    if state is None:
        session.add(RelayState(id=1, last_relayed_event_id=newest))
    else:
        state.last_relayed_event_id = newest
    session.commit()
    return len(events)


def main(settings: Settings | None = None) -> None:  # pragma: no cover - long-running loop
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = settings or get_settings()
    publisher = KafkaEventPublisher(s.kafka_bootstrap_servers)
    engine = create_engine(s.database_url)
    # Idempotent — removes the startup race with the API when a new table
    # (e.g. relay_state) ships in the same deployment.
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    logger.info(
        "outbox relay started: topic=%s brokers=%s batch=%d",
        s.kafka_orders_topic,
        s.kafka_bootstrap_servers,
        BATCH_SIZE,
    )
    while True:
        with session_factory() as session:
            relayed = relay_once(session, publisher, s.kafka_orders_topic)
        if relayed:
            logger.info("relayed %d event(s)", relayed)
            continue  # drain the backlog before sleeping
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
