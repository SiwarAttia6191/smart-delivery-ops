# Event Worker (Phase 2)

The outbox relay is live: `relay.py` tails the immutable `order_events` log
(transactional-outbox pattern — the API's database commit stays the source of
truth) and publishes every event to the `orders.events` Kafka topic, keyed by
order id so per-order ordering is preserved inside a partition.

- **Delivery guarantee:** at-least-once. The offset (`relay_state`, single row)
  is written only after a successful producer flush, so a crash replays a short
  tail — downstream consumers dedupe by `event_id`.
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
