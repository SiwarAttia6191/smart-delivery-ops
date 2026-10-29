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

## Phase 2 — Data engineering layer ✅

**Goal:** make the system analytics-ready.

- [x] Event bus (Kafka, KRaft single-node) + outbox relay + warehouse ingest (`apps/worker`)
- [x] Warehouse raw layer with per-event dedupe (`raw.order_events`, `raw.driver_events`)
- [x] dbt medallion marts (raw → staging → `analytics.*`)
- [x] Data-quality gates + source freshness (49 dbt nodes)
- [x] Airflow orchestration (`warehouse_daily_build`, 03:00 UTC)
- [x] CI data-plane smoke (relay → Kafka → ingest → raw roundtrip + dbt build)

## Phase 3 — AI / ML intelligence ✅

**Goal:** prediction and optimization over the event history.

- [x] Geo layer: pluggable distance providers benchmarked against OSRM, live shadow mode
- [x] Driver-supply events (availability outbox) + supply marts
- [x] Demand forecasting benchmarked in MLflow (seasonal-naive vs LightGBM vs Prophet)
- [x] Forecast serving: zone-hour forecasts → live `demand_ratio` → surge tiers
- [x] ETA model benchmarked against the heuristic (LightGBM won, registered `eta_model`)
- [x] ETA serving from the MLflow registry with heuristic fallback
- [x] Per-order route geometry (OSRM pair cache, GeoJSON endpoint)
- [x] Location-update hysteresis + stale-driver sweep (supply follows drivers)
- [ ] Load-aware driver allocation (next up — needs the demand/supply marts it now has)

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
