# Event Worker (Phase 2)

The outbox relay is live: `relay.py` tails the immutable `order_events` log
(transactional-outbox pattern — the API's database commit stays the source of
truth) and publishes every event to the `orders.events` Kafka topic, keyed by
order id so per-order ordering is preserved inside a partition.

- **Delivery guarantee:** at-least-once. Due rows are claimed
  (`published_at IS NULL` + `FOR UPDATE SKIP LOCKED`) and marked only after
  delivery reports confirm every message reached the broker — a crash replays
  the batch, and downstream consumers dedupe by `event_id`. Claim-based
  selection is immune to the sequence-gap race that makes id-offset cursors
  lose events under concurrent commits, and it keeps multiple relay instances
  from double-claiming.
- **Resilience:** transient failures (broker down, DB restart) trigger
  exponential backoff (cap 60s) instead of a crash-restart loop; `pool_pre_ping`
  survives Postgres restarts; SIGTERM finishes the current batch before exit.
- **Wire format:** `event_record()` in `relay.py` — `event_id`, `order_id`,
  `event_type`, `payload`, `created_at` (ISO-8601) as JSON.
- **Run:** `docker compose up -d worker` (entrypoint `python -m apps.worker.relay`).
  Hosts reach the broker on `localhost:9094`, containers on `kafka:19092`.

Planned next:

```
apps/worker/
├── relay.py           # shipped — outbox → Kafka
├── consumer.py        # warehouse ingestion consumer (Phase 2)
├── tasks.py           # scheduled jobs (aggregations, reprocessing)
└── event_handlers.py  # per-event-type handlers (order.created, order.assigned…)
```
