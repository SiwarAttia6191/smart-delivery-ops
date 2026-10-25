# Event Worker (Phase 2)

Consumes the order/driver/pricing event stream (Kafka or RabbitMQ) and feeds the
ingestion pipeline (`data/ingestion`) and the analytics warehouse.

Planned structure:

```
apps/worker/
├── consumer.py        # bus subscriber loop
├── tasks.py           # scheduled jobs (aggregations, reprocessing)
└── event_handlers.py  # per-event-type handlers (order.created, order.assigned…)
```

Until the bus lands, `order_events` rows written by the API are the raw feed —
see domain/models/order_event.py.
