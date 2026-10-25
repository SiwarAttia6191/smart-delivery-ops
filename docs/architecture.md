# Architecture

## Target architecture (full roadmap)

```mermaid
flowchart TD
    U[Customer / Driver / Ops Dashboard] --> API[FastAPI API Gateway]
    API --> AUTH[Authentication & Authorization]
    API --> ORD[Order Service]
    API --> DRV[Driver Service]
    API --> PRC[Pricing Service]
    API --> FRC[Forecasting Service]
    API --> ETA[ETA Prediction Service]
    API --> EVT[Event Producer]

    ORD --> PG[(PostgreSQL)]
    DRV --> PG
    PRC --> PG
    FRC --> PG

    DRV --> REDIS[(Redis — cache / availability)]
    ORD --> REDIS

    EVT --> BUS[(Kafka / RabbitMQ)]
    BUS --> INGEST[Streaming Ingestion]
    INGEST --> WH[(Analytics Warehouse)]
    INGEST --> RAW[(Raw Event Store)]

    FRC --> ML[Prophet / XGBoost / LightGBM]
    ETA --> ML
    ML --> REG[Model Registry / Artifacts]

    WH --> DASH[Dashboards / KPIs — Streamlit]
    API --> OTEL[OpenTelemetry]
    OTEL --> PROM[Prometheus]
    PROM --> GRAF[Grafana]

    CI[GitHub Actions] --> DEPLOY[Docker / Compose / Kubernetes]
```

## Phase 1 — what is implemented today

A **modular monolith** with clean internal boundaries, so each service can be
extracted later without rewriting the domain:

```
apps/api (FastAPI)
   ├── routes/            HTTP layer: validation, status codes, serialization
   ├── schemas/           Pydantic contracts (OpenAPI)
   └── deps.py            DI: database session
domain/
   ├── models/            SQLAlchemy 2.0 entities + order event log
   ├── rules/             pure functions: haversine, pricing tiers, eligibility
   └── services/          use-cases: order lifecycle, dispatch, quote, ETA
core/                     config (env), engine/session, logging, exceptions
```

### Design decisions

- **Rules are pure, services orchestrate.** Pricing/surge and driver eligibility
  are side-effect-free functions — exhaustively unit-testable and trivially
  replaceable when Phase 3 swaps in ML-driven versions.
- **Event log from day one.** Every order mutation appends an `OrderEvent` row.
  This is the raw feed the Phase 2 ingestion pipeline consumes — no retrofit
  needed when the event bus arrives.
- **Database-portable ORM.** SQLAlchemy 2.0 typed models run on SQLite (local,
  zero-config) and PostgreSQL (Docker) unchanged via `DATABASE_URL`.
- **Errors as domain exceptions.** Business rules raise typed exceptions
  (`NotFoundError`, `InvalidTransitionError`, `NoDriverAvailableError`) mapped
  to consistent HTTP error envelopes by one handler.
- **Metrics endpoint early.** `/analytics/metrics` already exposes Prometheus
  text format so observability lands with the product, not after it.
