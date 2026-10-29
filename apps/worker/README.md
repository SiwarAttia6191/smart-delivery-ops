# Event Worker (Phase 2)

The outbox relay tails two immutable transactional outboxes:
`order_events` → `orders.events` and `driver_events` → `drivers.events`.
Driver events record availability transitions (registration, heartbeat,
assignment, delivery, cancellation, stale_sweep) with the driver-state change
in the same database transaction, plus **hysteresis location_update events**
(#60): a heartbeat that moved the driver >500 m
(`DRIVER_LOCATION_UPDATE_THRESHOLD_M`) emits its own event so supply follows
the driver across zones. Continuous GPS pings remain out of scope.

**The worker loop does three things per cycle** (entrypoint
`python -m apps.worker.relay`): relay the order outbox, relay the driver
outbox, and run the **stale-driver sweep** — available drivers whose last
event is older than `DRIVER_STALE_AFTER_MINUTES` (120) get a forced
`availability` event (`reason: stale_sweep`) and flip offline, so silent
drivers stop counting as supply instead of being carried forever.

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
  combined order/driver backlog and quarantine counts are gauges on `/analytics/metrics`
  (`sdo_outbox_unpublished`, `sdo_outbox_quarantined`,
  `sdo_outbox_last_relay_age_seconds`). Driver relay failures are preserved in
  `driver_relay_dead_letters`.
- **Resilience:** transient failures (broker down, DB restart) trigger
  exponential backoff (cap 60s) instead of a crash-restart loop; `pool_pre_ping`
  survives Postgres restarts; SIGTERM finishes the current batch before exit.
  The ingest consumer re-runs its idempotent bootstrap after 5 consecutive
  failures (a dropped warehouse database self-heals).

## Recovery story (why nothing here is ever lost)

- **Kafka is a buffer, not the archive**: `KAFKA_LOG_RETENTION_HOURS=168`
  (7 days) is explicit. The archives are `order_events` and `driver_events`
  in the operational database.
- **Full replay from the source of truth** (e.g. after a warehouse wipe):
  clear `published_at` and `quarantined_at` on both outboxes — each stream
  replays to its own raw table, where `event_id` dedupes surviving rows.
- **Quarantined events** (dead letters) are the one category replay skips:
  they failed deterministically, so fix the cause, then clear
  `quarantined_at` (and delete the dead-letter row) to re-queue them.
- **Wire formats:** `event_record()` includes `order_id`; `driver_event_record()`
  includes `driver_id`. Both carry `schema_version`, `event_id`, `event_type`,
  `payload`, and ISO-8601 `created_at`.
- **Run:** `docker compose up -d worker` (entrypoint `python -m apps.worker.relay`).
  Hosts reach the broker on `localhost:9094`, containers on `kafka:19092`.

The warehouse consumer subscribes to both topics (group `warehouse-ingest`,
`enable.auto.commit=false`) and lands them into `raw.order_events` and
`raw.driver_events` in the **separate warehouse database**. Each table has its
own `event_id` primary key, so at-least-once replays dedupe without collisions
between independent outbox ids. Kafka offsets commit only after the rows from
both streams have been stored. dbt builds `stg_driver_events` and the hourly
driver-supply/demand-supply marts from these immutable records.

```
apps/worker/
├── relay.py           # shipped — outbox → Kafka
└── ingest.py          # shipped — both Kafka topics → per-stream raw tables
```
