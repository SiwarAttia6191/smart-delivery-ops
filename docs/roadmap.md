# Roadmap

## Phase 1 — Foundation & MVP ✅

**Goal:** a working delivery backend with clean architecture.

- [x] Customer / driver / restaurant models with geolocation
- [x] Order creation and lifecycle state machine (event-logged)
- [x] Nearest-available-driver dispatch with vehicle filter
- [x] Distance pricing with surge tiers + minimum fee
- [x] ETA heuristic
- [x] FastAPI REST API with Swagger/Redoc
- [x] SQLite default + PostgreSQL-ready via `DATABASE_URL`
- [x] Unit + integration test suite
- [x] CI (lint + tests), Docker Compose, Prometheus metrics endpoint
- [x] Docs: architecture, data model, API, ML design

## Phase 2 — Data engineering layer 🔜

**Goal:** make the system analytics-ready.

- [ ] Event bus (Kafka or RabbitMQ) + `apps/worker` consumer
- [ ] Streaming ingestion into `data/ingestion`
- [ ] Warehouse tables (`data/warehouse/schema`) + curated queries
- [ ] ETL with validation / quality checks (`data/pipeline`)
- [ ] Raw event archive (`data/raw`)
- [ ] Optional Airflow / dbt orchestration

## Phase 3 — AI / ML intelligence 🔜

**Goal:** prediction and optimization over the event history.

- [ ] Feature engineering pipeline (`ml/features`)
- [ ] Hourly demand forecasting by zone (Prophet vs LightGBM benchmark)
- [ ] ETA regression model replacing the heuristic
- [ ] Demand-aware dynamic pricing (forecast → `demand_ratio`)
- [ ] Load-aware driver allocation
- [ ] Model registry + evaluation + drift monitoring

## Phase 4 — Dashboard & BI 🔜

**Goal:** show operational value.

- [ ] Streamlit dashboard (`apps/dashboard`)
- [ ] KPIs: order volume, fulfillment rate, avg delivery time, driver
      utilization, revenue per zone, demand by hour/region
- [ ] Drill-down views and forecasting screens

## Phase 5 — Production readiness 🟡

**Goal:** deployable, observable, secure.

- [x] Docker + Compose (API + PostgreSQL)
- [x] GitHub Actions CI
- [x] `/analytics/metrics` Prometheus endpoint
- [x] Dependabot
- [ ] Authentication + authorization (JWT, roles)
- [ ] Redis cache / availability state
- [ ] Alembic migrations
- [ ] OpenTelemetry tracing, structured logs to a sink
- [ ] Grafana dashboards + alert rules (`infra/monitoring`)
- [ ] Kubernetes manifests (`infra/kubernetes`)
- [ ] Release workflow (image publish)
