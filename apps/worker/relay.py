"""Outbox relay: tails the immutable order and driver event logs and publishes
them to separate Kafka topics for downstream consumers.

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
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from core.config import Settings, get_settings
from domain.models import DriverEvent, DriverRelayDeadLetter
from domain.models.order_event import OrderEvent
from domain.models.relay_dead_letter import RelayDeadLetter
from domain.models.relay_state import RelayState
from domain.services.driver_event_service import sweep_stale_drivers

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

    def take_delivery_failures(self) -> list[int]:
        """Event ids whose delivery permanently failed (per delivery reports),
        consumed by the relay's quarantine path. Empty after a broker outage
        (a timeout leaves messages queued, not failed)."""


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
        self._failed_event_ids: list[int] = []

    def _on_delivery(self, err, msg) -> None:  # noqa: ANN001 - confluent callback signature
        if err is not None:
            self._failures += 1
            event_id = None
            for hdr_key, hdr_val in msg.headers() or []:
                if hdr_key == "event_id":
                    event_id = int(hdr_val.decode())
            self._failed_event_ids.append(event_id) if event_id else None
            logger.error(
                "kafka delivery failed: %s (topic=%s, event_id=%s)",
                err,
                msg.topic() if msg else "?",
                event_id,
            )

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
        self._failed_event_ids = []
        remaining = self._producer.flush(FLUSH_TIMEOUT_SECONDS)
        if remaining or self._failures:
            raise RuntimeError(
                f"delivery not confirmed: {self._failures} failure(s), {remaining} unflushed"
            )

    def take_delivery_failures(self) -> list[int]:
        failed = self._failed_event_ids
        self._failed_event_ids = []
        return failed


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


def driver_event_record(event: DriverEvent) -> dict:
    """Wire format for the drivers.events topic."""
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event.id,
        "driver_id": event.driver_id,
        "event_type": event.event_type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _relay_once(
    session: Session,
    publisher: EventPublisher,
    topic: str,
    batch_size: int,
    event_model: type[OrderEvent] | type[DriverEvent],
    key_field: str,
    record_encoder: Callable[[Any], dict],
    update_state: bool,
) -> int:
    """Claim, publish, and mark one batch of due events; returns how many were sent.

    The claim transaction spans produce+flush on purpose: the row locks are
    the claim (SKIP LOCKED hides them from other workers), and rollback on
    failure releases everything cleanly. Flush timeout is bounded so the
    open transaction — and its vacuum pin — cannot outlive a broker outage.
    """
    events = cast(
        list[OrderEvent | DriverEvent],
        session.scalars(
            select(event_model)
            .where(event_model.published_at.is_(None))
            .where(event_model.quarantined_at.is_(None))
            .order_by(event_model.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all(),
    )
    if not events:
        return 0

    def _quarantine(event, error: Exception) -> None:  # noqa: ANN001 - ORM model is selected above
        """Give up on one poison event - visible, searchable, never silent."""
        now = datetime.now(UTC)
        event.quarantined_at = now
        if event_model is OrderEvent:
            dead_letter: RelayDeadLetter | DriverRelayDeadLetter = RelayDeadLetter(
                event_id=event.id,
                order_id=event.order_id,
                event_type=event.event_type,
                error=str(error)[:500],
                created_at=now,
            )
        else:
            dead_letter = DriverRelayDeadLetter(
                event_id=event.id,
                driver_id=event.driver_id,
                event_type=event.event_type,
                error=str(error)[:500],
                created_at=now,
            )
        session.add(dead_letter)
        logger.error("quarantined event %s (%s): %s", event.id, event.event_type, error)

    # Produce every event, isolating produce-time failures (serialization,
    # oversized payloads) to their row instead of failing the batch.
    deliverable = []
    for event in events:
        try:
            publisher.produce(
                topic=topic,
                key=str(getattr(event, key_field)),
                value=json.dumps(record_encoder(event)),
                headers=[
                    ("event_type", event.event_type.encode("utf-8")),
                    ("event_id", str(event.id).encode("utf-8")),
                ],
            )
            deliverable.append(event)
        except Exception as exc:  # noqa: BLE001 - quarantine is the point
            _quarantine(event, exc)

    if deliverable:
        try:
            publisher.flush()  # raises unless the broker acknowledged delivery
        except Exception:
            failed_ids = set(publisher.take_delivery_failures())
            if not failed_ids:
                session.rollback()
                raise
            for event in deliverable:
                if event.id in failed_ids:
                    _quarantine(event, RuntimeError("delivery report: permanent failure"))
            publisher.flush()

    delivered_at = datetime.now(UTC)
    quarantined_ids = {event.id for event in events if event.quarantined_at is not None}
    for event in events:
        if event.id not in quarantined_ids:
            event.published_at = delivered_at

    if update_state:
        state = session.get(RelayState, 1, with_for_update=True)
        newest = events[-1].id
        if state is None:
            session.add(RelayState(id=1, last_relayed_event_id=newest))
        else:
            state.last_relayed_event_id = newest
    session.commit()
    return len(events) - len(quarantined_ids)


def relay_once(
    session: Session, publisher: EventPublisher, topic: str, batch_size: int = 100
) -> int:
    """Relay one order-event batch to its Kafka topic."""
    return _relay_once(
        session, publisher, topic, batch_size, OrderEvent, "order_id", event_record, True
    )


def relay_driver_events_once(
    session: Session, publisher: EventPublisher, topic: str, batch_size: int = 100
) -> int:
    """Relay one driver-event batch to its dedicated Kafka topic."""
    return _relay_once(
        session, publisher, topic, batch_size, DriverEvent, "driver_id", driver_event_record, False
    )


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
        for ddl in (
            "ALTER TABLE order_events ADD COLUMN IF NOT EXISTS published_at timestamptz",
            "ALTER TABLE order_events ADD COLUMN IF NOT EXISTS quarantined_at timestamptz",
        ):
            try:
                conn.execute(text(ddl))
            except ProgrammingError as exc:
                # The runtime role (sdo_app, #36) is deliberately not the
                # schema owner: ALTER TABLE needs ownership even as an
                # IF NOT EXISTS no-op. Since #84 the Alembic chain owns
                # these columns anyway — the shim only matters for
                # pre-Alembic databases, which connect as the admin role.
                if "must be owner" in str(exc):
                    logging.info("legacy shim skipped: schema owned by the Alembic chain")
                    return
                raise


def main(settings: Settings | None = None) -> None:  # pragma: no cover - long-running loop
    global _shutdown
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = settings or get_settings()
    publisher = KafkaEventPublisher(s.kafka_bootstrap_servers)
    # pool_pre_ping: recycle stale pooled connections after a Postgres restart
    # instead of crashing the worker with an OperationalError.
    engine = create_engine(s.database_url, pool_pre_ping=True)
    # The relay publishes EVERY tenant's outbox (#36): mark the explicit
    # cross-tenant GUC — forced RLS would otherwise hide all but the
    # default tenant's events from this engine's sessions.
    import core.tenancy

    core.tenancy.mark_tenant_bypass(engine)
    # The schema is owned by the Alembic chain (#28): compose starts this
    # service only after the one-shot `migrate` service reached head, which
    # removes the old startup race (create_all could not cover ALTERs
    # anyway - only migrations can evolve an existing table).
    migrate_legacy_deployment(engine)
    session_factory = sessionmaker(bind=engine)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _request_shutdown)  # docker stop → finish batch, exit cleanly

    logger.info(
        "outbox relay started: topics=%s,%s brokers=%s batch=%d",
        s.kafka_orders_topic,
        s.kafka_drivers_topic,
        s.kafka_bootstrap_servers,
        s.relay_batch_size,
    )
    backoff = s.relay_poll_seconds
    while not _shutdown:
        try:
            with session_factory() as session:
                relayed = relay_once(session, publisher, s.kafka_orders_topic, s.relay_batch_size)
                relayed += relay_driver_events_once(
                    session, publisher, s.kafka_drivers_topic, s.relay_batch_size
                )
                # #60 stale-driver sweep: silent heartbeats stop counting as
                # supply. Emits availability events (reason stale_sweep) that
                # ride the same outbox → relay → warehouse path.
                sweep_stale_drivers(session)
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
