"""Outbox relay: tails the immutable ``order_events`` log and publishes each
row to a Kafka topic for downstream consumers (stream processing, warehouse
ingestion, dashboards).

Pattern: transactional outbox. The API commits domain state AND its event
rows in one database transaction; this worker never touches the API path —
it only reads the log and copies entries onto the bus.

Selection is claim-based, not offset-based: due rows are ``published_at IS
NULL`` and are claimed with ``FOR UPDATE SKIP LOCKED``. Why that matters:

* Postgres assigns sequence ids at INSERT time but they become visible at
  COMMIT time. An offset cursor (``id > last_relayed``) can therefore observe
  id 51 committed while id 50 is still in flight, advance past it, and lose
  event 50 forever when it commits later. Claiming *rows that exist* is
  immune to commit-order gaps — and to values burned by rolled-back inserts,
  which would stall a "stop at the first gap" cursor permanently.
* ``SKIP LOCKED`` makes concurrent relay instances safe: a row locked by one
  worker is invisible to the others' claim query.
* Delivery is at-least-once: rows are marked ``published_at`` only after the
  producer's flush confirms the broker acknowledged every message; a crash
  before that replays the batch and consumers dedupe by ``event_id``.

Run inside the compose stack:

    docker compose up -d worker        # command: python -m apps.worker.relay

Host tools reach the broker on localhost:9094 (compose port mapping);
containers reach it on kafka:19092.
"""

import json
import logging
import signal
import time
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from core.config import Settings, get_settings
from domain.models import Base
from domain.models.order_event import OrderEvent
from domain.models.relay_state import RelayState

logger = logging.getLogger("worker.relay")

FLUSH_TIMEOUT_SECONDS = 10.0
SCHEMA_VERSION = 1


class EventPublisher(Protocol):
    """Minimal publisher interface — swappable for tests and future brokers."""

    def produce(
        self,
        topic: str,
        key: str,
        value: str,
        headers: list[tuple[str, str | bytes | None]] | None = None,
    ) -> None: ...

    def flush(self) -> None: ...


class KafkaEventPublisher:
    """confluent-kafka producer with idempotence enabled (acks=all, no dupes
    within a producer session).

    Delivery reports are essential, not decorative: ``flush()`` only counts
    messages still *queued*. A message that failed permanently (oversized
    payload, auth error after retries) is removed from the queue and reported
    solely via the callback — without it the batch would look clean, the
    offset would advance, and the event would be silently gone.
    """

    def __init__(self, bootstrap_servers: str) -> None:
        from confluent_kafka import Producer

        self._producer = Producer(
            {"bootstrap.servers": bootstrap_servers, "enable.idempotence": True}
        )
        self._failures = 0

    def _on_delivery(self, err, msg) -> None:  # noqa: ANN001 - confluent callback signature
        if err is not None:
            self._failures += 1
            logger.error("kafka delivery failed: %s (topic=%s)", err, msg.topic() if msg else "?")

    def produce(
        self,
        topic: str,
        key: str,
        value: str,
        headers: list[tuple[str, str | bytes | None]] | None = None,
    ) -> None:
        self._producer.produce(
            topic,
            key=key.encode("utf-8"),
            value=value.encode("utf-8"),
            headers=headers,
            on_delivery=self._on_delivery,
        )

    def flush(self) -> None:
        self._failures = 0
        remaining = self._producer.flush(FLUSH_TIMEOUT_SECONDS)
        if remaining or self._failures:
            raise RuntimeError(
                f"delivery not confirmed: {self._failures} failure(s), {remaining} unflushed"
            )


def event_record(event: OrderEvent) -> dict:
    """Wire format for the orders.events topic. Bump SCHEMA_VERSION on any
    breaking change so downstream consumers can branch on it."""
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event.id,
        "order_id": event.order_id,
        "event_type": event.event_type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def relay_once(
    session: Session, publisher: EventPublisher, topic: str, batch_size: int = 100
) -> int:
    """Claim, publish, and mark one batch of due events; returns how many were sent.

    The claim transaction spans produce+flush on purpose: the row locks are
    the claim (SKIP LOCKED hides them from other workers), and rollback on
    failure releases everything cleanly. Flush timeout is bounded so the
    open transaction — and its vacuum pin — cannot outlive a broker outage.
    """
    events = (
        session.execute(
            select(OrderEvent)
            .where(OrderEvent.published_at.is_(None))
            .order_by(OrderEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    if not events:
        return 0

    try:
        for event in events:
            publisher.produce(
                topic=topic,
                key=event.order_id,  # per-order ordering inside a partition
                value=json.dumps(event_record(event)),
                headers=[("event_type", event.event_type.encode("utf-8"))],
            )
        publisher.flush()  # raises unless the broker acknowledged every message
    except Exception:
        session.rollback()  # release the claim; the batch stays due and will replay
        raise

    delivered_at = datetime.now(UTC)
    for event in events:
        event.published_at = delivered_at

    # Observability only — selection no longer depends on this row.
    state = session.get(RelayState, 1, with_for_update=True)
    newest = events[-1].id
    if state is None:
        session.add(RelayState(id=1, last_relayed_event_id=newest))
    else:
        state.last_relayed_event_id = newest
    session.commit()
    return len(events)


def migrate_legacy_deployment(engine) -> None:
    """One-time shim for databases created before the claim column existed
    (Alembic migrations arrive in Phase 5; until then compose deployments
    self-heal here). Only adds missing columns.

    Deliberately NO backfill: marking old rows published from the legacy
    offset counter would institutionalize that counter's sequence-gap bug
    (it could advance past an uncommitted event). Replay is free - the
    warehouse dedupes by event_id - so leaving legacy rows NULL and
    re-relaying them is strictly safer than trusting the old state."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE order_events ADD COLUMN IF NOT EXISTS published_at timestamptz")
        )


def main(settings: Settings | None = None) -> None:  # pragma: no cover - long-running loop
    global _shutdown
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = settings or get_settings()
    publisher = KafkaEventPublisher(s.kafka_bootstrap_servers)
    # pool_pre_ping: recycle stale pooled connections after a Postgres restart
    # instead of crashing the worker with an OperationalError.
    engine = create_engine(s.database_url, pool_pre_ping=True)
    # Idempotent — removes the startup race with the API when a new table
    # (e.g. relay_state) ships in the same deployment.
    Base.metadata.create_all(engine)
    migrate_legacy_deployment(engine)
    session_factory = sessionmaker(bind=engine)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _request_shutdown)  # docker stop → finish batch, exit cleanly

    logger.info(
        "outbox relay started: topic=%s brokers=%s batch=%d",
        s.kafka_orders_topic,
        s.kafka_bootstrap_servers,
        s.relay_batch_size,
    )
    backoff = s.relay_poll_seconds
    while not _shutdown:
        try:
            with session_factory() as session:
                relayed = relay_once(session, publisher, s.kafka_orders_topic, s.relay_batch_size)
        except Exception:
            # Broker down, DB hiccup, ...: back off and retry instead of the
            # crash-restart cycle a bare exception would cause.
            logger.exception("relay cycle failed; backing off %.1fs", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, s.relay_backoff_seconds)
        else:
            if relayed:
                logger.info("relayed %d event(s)", relayed)
                backoff = s.relay_poll_seconds
                continue  # drain the backlog before sleeping
            time.sleep(s.relay_poll_seconds)
    logger.info("relay stopped cleanly")


_shutdown = False


def _request_shutdown(signum, frame) -> None:  # noqa: ANN001 - signal handler signature
    global _shutdown
    _shutdown = True
    logger.info("shutdown signal received (sig=%s); finishing current cycle", signum)


if __name__ == "__main__":
    main()
