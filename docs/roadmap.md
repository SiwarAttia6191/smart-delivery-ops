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
- [x] Multi-region region pack (#78): city manifests, OSRM extract swap,
      `GET /config`, Paris verified side-by-side (DST boundary grained in real marts)
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

## 📉 The honest gap to a real-life system

Every claim in this repo is traceable to a benchmark or a live endpoint —
the same honesty applies to what is *missing*. Measured against a
production delivery platform, the gaps are:

| real-life system | this repo today | the gap, and what closes it |
|---|---|---|
| Millions of orders, years of history | ~1,500 orders / 28 days, backfilled through the real pipeline | models learn from thin data (the benchmark reports say so themselves — "a young deployment"). Only traffic fixes the volume; a longer, noisier backfill + open delivery datasets can widen the window meanwhile. |
| Auth + multi-tenant SaaS | open API, one deployment = one city (#78) | #28/#35/#36 — the actual **client-onboarding blocker**: JWT + roles, a tenant dimension on the domain tables, per-tenant warehouses. |
| Versioned migrations, secrets vault | `create_all` schema bootstrap, plaintext env vars | Alembic revision chain + Docker secrets — table stakes for operating, not just demoing. |
| Scheduled ML retraining | benchmarks + serving run on demand (`docker compose run --rm ml-bench`) | the operational half of M1: an Airflow DAG triggers retrain → benchmark → registry promotion on schedule, so the champion decays honestly instead of silently. |
| Load-tested, HA, on-call | single Kafka broker, single node, log-only alerts | #76 backlog (route-cache pruning, supply overcount while worker down…) + k6 load numbers + Grafana alert rules. HA/multi-broker is a deployment-topology concern — documented, not simulated. |

**Priority order (portfolio value × product value per unit of effort):**

1. **Scheduled retraining** — small, closes the biggest "it's a demo ML
   loop" criticism; Airflow and the registry already exist, the DAG just
   wires them together.
2. **Auth + tenancy foundation** (#28/#35/#36) — turns "impressive demo"
   into "onboarding a client is conceivable"; biggest product gap.
3. **Alembic + secrets** — production credibility baseline; unblocks
   schema evolution the dashboard phase will want anyway.
4. **Load test + observability** (#76) — replace "it works" with measured
   latencies and alert rules.
5. **Data depth** — 180-day backfill with more realistic noise, open
   delivery datasets where licensing allows.

Deliberately out of scope for a solo portfolio project: multi-broker HA,
on-call rotation, per-region Kubernetes fleets — documented decisions, not
oversights.
