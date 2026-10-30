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
- [x] Authentication + authorization (JWT, roles) — OAuth2 password flow
      (`POST /auth/token`), bcrypt hashes, `ops`/`admin` writer roles via
      FastAPI dependencies on every mutating endpoint; GETs + quote stay
      open as the documented demo posture; demo users via `create_user.py`
- [ ] Redis cache / availability state
- [x] Alembic migrations — the ops schema is owned by a versioned chain
      (`migrations/`): compose `migrate` one-shot applies it before
      api/worker, the pre-Alembic demo DB was stamped to head (never
      hand-altered), `test_migrations_parity.py` fails on drift, and CI's
      data-plane smoke runs the chain against real Postgres every PR
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
| Auth + multi-tenant SaaS | ✅ **closed** — JWT auth wall (#28) whose tokens carry the issuing user's tenant binding; the request pins it into Postgres **Row-Level Security** policies forced on all six business tables (migration 003), so isolation is enforced by the database engine; runtime services connect as a least-privilege role (a superuser would bypass row security by definition); per-tenant pricing overrides + `/analytics/by-tenant`; tenant rides in the event envelope to the warehouse | per-tenant warehouses/schemas stay a documented graduation path (docs/decisions/0001-multi-tenancy.md) — the shared-schema+RLS model is the right starting point at this scale. |
| Versioned migrations, secrets vault | ✅ **closed** — Alembic owns the ops schema (compose `migrate` one-shot, parity-tested, CI-applied); credentials travel as Docker secret files (`/run/secrets/...`), absent from compose config and `docker inspect` env | production swaps in vault-injected files at the same paths — zero code change. |
| Scheduled ML retraining | ✅ **closed** — `ml_forecast_serving` (@hourly) refreshes the forecast table the surge loop reads; `ml_retrain_benchmark` (04:15 UTC daily, after the warehouse build) re-runs both benchmarks, promotion stays champion-gated | the operational half of M1 is scheduled: the ML loop now decays honestly on a clock instead of waiting for a manual `make ml-benchmark`. |
| Load-tested, HA, on-call | single Kafka broker, single node, log-only alerts | ✅ the #76 hardening backlog is closed (incarnation-token recovery, drift guard, ETA hot reload, memory budget, runbook — docs/runbook.md); **remaining: k6 load numbers + Grafana alert rules**. HA/multi-broker is a deployment-topology concern — documented, not simulated. |

**Priority order (portfolio value × product value per unit of effort):**

1. ~~**Scheduled retraining**~~ ✅ **done** — Airflow now runs the ML loop
   (hourly serving, daily benchmark/promotion); Airflow and the registry
   existed, the DAG just wires them together.
2. ~~**Auth + tenancy foundation**~~ ✅ **done** — the auth wall (#83),
   the tenancy dimension (#35, migration 002), and the isolation layer
   (#36): JWT tenant claims + forced Postgres RLS + per-tenant pricing,
   proven live with a second onboarded tenant.
3. ~~**Alembic + secrets**~~ ✅ **done** — the migration chain owns the
   ops schema (and unblocked tenancy); credentials are Docker secret
   files now, with vault injection as the documented production path.
4. **Load test + observability** (#76) — replace "it works" with measured
   latencies and alert rules.
5. **Data depth** — 180-day backfill with more realistic noise, open
   delivery datasets where licensing allows.

Deliberately out of scope for a solo portfolio project: multi-broker HA,
on-call rotation, per-region Kubernetes fleets — documented decisions, not
oversights.
