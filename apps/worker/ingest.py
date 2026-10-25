"""Warehouse ingestion consumer: lands ``orders.events`` into the raw layer.

The last leg of the event pipeline:

    API ──► order_events (outbox) ──► relay ──► Kafka orders.events
                                                        │
                                        THIS consumer ──▼
                              warehouse DB · raw.order_events (dedupe by event_id)

Design contracts:

* **Separate warehouse database** — analytics never reads or loads into the
  operational tables the API serves (dbt will build marts on top of this raw
  layer in the next step). On Postgres the raw layer lives in a ``raw``
  schema; the database itself is created on first boot so existing compose
  volumes self-heal without manual steps.
* **Dedupe is owned here.** The relay is at-least-once, so replays are
  expected: ``event_id`` is the primary key and inserts use ON CONFLICT DO
  NOTHING — a replayed event is a no-op, not a duplicate row.
* **Offsets commit only after the database commit** (``enable.auto.commit``
  is off) — the at-least-once chain holds end to end: crash anywhere in
  between and the group re-reads its uncommitted offsets.
* Malformed messages are logged with a preview and skipped (a dead-letter
  topic is the production answer, deferred); they must not poison the batch.

Run inside the compose stack:

    docker compose up -d ingest        # command: python -m apps.worker.ingest
"""

import json
import logging
import signal
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from core.config import Settings, get_settings

logger = logging.getLogger("worker.ingest")

_REQUIRED_FIELDS = {"event_id", "order_id", "event_type", "schema_version", "payload"}

_PG_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    event_id BIGINT PRIMARY KEY,
    order_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(30) NOT NULL,
    schema_version INTEGER NOT NULL,
    payload JSONB,
    source_created_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL,
    kafka_partition SMALLINT,
    kafka_offset BIGINT
)
"""

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    event_id INTEGER PRIMARY KEY,
    order_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(30) NOT NULL,
    schema_version INTEGER NOT NULL,
    payload TEXT,
    source_created_at VARCHAR(64),
    ingested_at TIMESTAMP NOT NULL,
    kafka_partition INTEGER,
    kafka_offset BIGINT
)
"""


def parse_message(value: bytes | str | None) -> dict[str, Any] | None:
    """Validate and normalize one Kafka message into a raw-layer record.

    Returns None (with a warning log) for anything malformed — the raw layer
    only ever accepts events that match the wire contract.
    """
    try:
        raw = json.loads(value) if value is not None else None
    except (TypeError, ValueError):
        logger.warning("dropping malformed event (not JSON): %r", str(value)[:120])
        return None
    if not isinstance(raw, dict):
        logger.warning("dropping malformed event (not an object): %r", str(value)[:120])
        return None
    missing = _REQUIRED_FIELDS - set(raw)
    if missing:
        logger.warning("dropping malformed event (missing %s): %r", sorted(missing), str(raw)[:120])
        return None
    if isinstance(raw["event_id"], bool) or not isinstance(raw["event_id"], int):
        logger.warning("dropping malformed event (event_id not an int): %r", str(raw)[:120])
        return None
    if not isinstance(raw["order_id"], str) or not isinstance(raw["event_type"], str):
        logger.warning(
            "dropping malformed event (order_id/event_type not strings): %r", str(raw)[:120]
        )
        return None
    return {
        "event_id": raw["event_id"],
        "order_id": raw["order_id"],
        "event_type": raw["event_type"],
        "schema_version": int(raw["schema_version"]),
        "payload": json.dumps(raw["payload"]),
        "source_created_at": raw.get("created_at"),
        # Kafka lineage - filled in by the consumer loop from the message
        "kafka_partition": None,
        "kafka_offset": None,
    }


class RawEventStore:
    """Dedupe-by-primary-key store for the warehouse raw layer."""

    def __init__(
        self,
        database_url: str,
        schema: str | None = "raw",
        table: str = "order_events",
        engine: Engine | None = None,
    ) -> None:
        self._schema = schema
        self._table = table
        self._engine = engine or create_engine(database_url, pool_pre_ping=True)

    def _qualified(self) -> str:
        return f"{self._schema}.{self._table}" if self._schema else self._table

    def ensure(self) -> None:
        """Idempotent bootstrap: warehouse database (Postgres only — existing
        volumes predate it), raw schema, and the raw events table."""
        if self._engine.dialect.name == "postgresql":
            self._ensure_database()
        ddl = _PG_DDL if self._engine.dialect.name == "postgresql" else _SQLITE_DDL
        with self._engine.begin() as conn:
            if self._schema:
                conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {self._schema}"))
            conn.execute(text(ddl.format(table=self._qualified())))
            if self._engine.dialect.name == "postgresql":
                # Tables created before a column existed (Alembic lands in
                # Phase 5): additively self-heal instead of failing inserts.
                for col in ("kafka_partition SMALLINT", "kafka_offset BIGINT"):
                    conn.execute(
                        text(f"ALTER TABLE {self._qualified()} ADD COLUMN IF NOT EXISTS {col}")
                    )

    def _ensure_database(self) -> None:
        url = make_url(self._engine.url)
        dbname = url.database
        admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        try:
            with admin_engine.connect() as conn:
                exists = conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": dbname}
                ).scalar()
                if not exists and dbname:
                    conn.execute(text(f'CREATE DATABASE "{dbname}"'))
                    logger.info("created warehouse database %s", dbname)
        finally:
            admin_engine.dispose()

    def store(self, records: Sequence[dict[str, Any]]) -> int:
        """Insert a batch, skipping event_ids already present. Returns the
        number of NEW rows — replays dedupe to zero by primary key."""
        ingested_at = datetime.now(UTC)
        inserted = 0
        with self._engine.begin() as conn:
            for record in records:
                if self._engine.dialect.name == "sqlite":
                    stmt = text(
                        f"INSERT OR IGNORE INTO {self._qualified()} "
                        "(event_id, order_id, event_type, schema_version, payload, "
                        "source_created_at, ingested_at) "
                        "VALUES (:event_id, :order_id, :event_type, :schema_version, "
                        ":payload, :source_created_at, :ingested_at)"
                    )
                else:
                    stmt = text(
                        f"INSERT INTO {self._qualified()} "
                        "(event_id, order_id, event_type, schema_version, payload, "
                        "source_created_at, ingested_at, kafka_partition, kafka_offset) "
                        "VALUES (:event_id, :order_id, :event_type, :schema_version, "
                        ":payload, :source_created_at, :ingested_at, "
                        ":kafka_partition, :kafka_offset) "
                        "ON CONFLICT (event_id) DO NOTHING"
                    )
                result = conn.execute(stmt, record | {"ingested_at": ingested_at})
                inserted += max(result.rowcount, 0)
        return inserted


def main(settings: Settings | None = None) -> None:  # pragma: no cover - long-running loop
    global _shutdown
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = settings or get_settings()
    store = RawEventStore(s.warehouse_database_url)
    store.ensure()

    from confluent_kafka import Consumer

    consumer = Consumer(
        {
            "bootstrap.servers": s.kafka_bootstrap_servers,
            "group.id": s.kafka_group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,  # offsets move only after the DB commit
        }
    )
    consumer.subscribe([s.kafka_orders_topic])
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _request_shutdown)
    logger.info(
        "ingest consumer started: topic=%s group=%s -> warehouse raw layer",
        s.kafka_orders_topic,
        s.kafka_group_id,
    )

    while not _shutdown:
        batch = consumer.consume(num_messages=s.ingest_batch_size, timeout=s.relay_poll_seconds)
        if not batch:
            continue
        for msg in batch:
            if msg.error():
                logger.warning("kafka message error: %s", msg.error())
        records = []
        for msg in batch:
            record = parse_message(msg.value())
            if record is not None:
                record["kafka_partition"] = msg.partition()
                record["kafka_offset"] = msg.offset()
                records.append(record)
        try:
            if records:
                inserted = store.store(records)
                logger.info(
                    "ingested %d new event(s) — %d received, %d deduped or malformed",
                    inserted,
                    len(batch),
                    len(batch) - inserted,
                )
            consumer.commit(asynchronous=False)
        except Exception:
            # Offsets were not committed: the group re-reads this batch —
            # the primary key turns the replay into no-ops.
            logger.exception("ingest cycle failed; will re-consume after backoff")
            time.sleep(s.relay_poll_seconds * 2)

    consumer.close()
    logger.info("ingest consumer stopped cleanly")


_shutdown = False


def _request_shutdown(signum, frame) -> None:  # noqa: ANN001 - signal handler signature
    global _shutdown
    _shutdown = True
    logger.info("shutdown signal received (sig=%s); closing consumer", signum)


if __name__ == "__main__":
    main()
