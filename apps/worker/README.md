# Event Worker (Phase 2)

The outbox relay is live: `relay.py` tails the immutable `order_events` log
(transactional-outbox pattern — the API's database commit stays the source of
truth) and publishes every event to the `orders.events` Kafka topic, keyed by
order id so per-order ordering is preserved inside a partition.

- **Delivery guarantee:** at-least-once. Due rows are claimed
  (`published_at IS NULL`, `quarantined_at IS NULL` + `FOR UPDATE SKIP LOCKED`)
  and marked only after delivery reports confirm every message reached the
  broker — a crash replays the batch, and downstream consumers dedupe by
  `event_id`. Claim-based selection is immune to the sequence-gap race that
  makes id-offset cursors lose events under concurrent commits, and it keeps
  multiple relay instances from double-claiming.
- **Poison pills are quarantined, never blocking:** an event that permanently
  fails to produce or deliver gets `quarantined_at` + a `relay_dead_letters`
  row (reason preserved) and the outbox drains around it. The backlog and
  quarantine counts are gauges on `/analytics/metrics`
  (`sdo_outbox_unpublished`, `sdo_outbox_quarantined`,
  `sdo_outbox_last_relay_age_seconds`).
- **Resilience:** transient failures (broker down, DB restart) trigger
  exponential backoff (cap 60s) instead of a crash-restart loop; `pool_pre_ping`
  survives Postgres restarts; SIGTERM finishes the current batch before exit.
  The ingest consumer re-runs its idempotent bootstrap after 5 consecutive
  failures (a dropped warehouse database self-heals).

## Recovery story (why nothing here is ever lost)

- **Kafka is a buffer, not the archive**: `KAFKA_LOG_RETENTION_HOURS=168`
  (7 days) is explicit. The archive is the outbox itself — `order_events` in
  the operational database.
- **Full replay from the source of truth** (e.g. after a warehouse wipe):
  `UPDATE order_events SET published_at = NULL, quarantined_at = NULL;` — the
  relay drains the entire history back onto the bus, and the warehouse's
  `event_id` primary key makes re-ingestion a no-op for anything that
  survived. No event can be lost this way; at worst it is delivered twice
  (deduped downstream).
- **Quarantined events** (dead letters) are the one category replay skips:
  they failed deterministically, so fix the cause, then clear
  `quarantined_at` (and delete the dead-letter row) to re-queue them.
- **Wire format:** `event_record()` in `relay.py` — `event_id`, `order_id`,
  `event_type`, `payload`, `created_at` (ISO-8601) as JSON.
- **Run:** `docker compose up -d worker` (entrypoint `python -m apps.worker.relay`).
  Hosts reach the broker on `localhost:9094`, containers on `kafka:19092`.

The warehouse consumer is also live: `ingest.py` subscribes to `orders.events`
(group `warehouse-ingest`, `enable.auto.commit=false`) and lands events into
`raw.order_events` in the **separate warehouse database**. Dedupe contract:
`event_id` is the primary key with ON CONFLICT DO NOTHING, so the relay's
at-least-once replays are no-ops; Kafka offsets commit only after the database
commit. Malformed messages are logged with a preview and skipped (dead-letter
topic deferred). dbt builds its marts on this raw layer.

```
apps/worker/
├── relay.py           # shipped — outbox → Kafka
├── ingest.py          # shipped — Kafka → warehouse raw layer (dedupe by event_id)
├── tasks.py           # scheduled jobs (aggregations, reprocessing)
└── event_handlers.py  # per-event-type handlers (order.created, order.assigned…)
```
