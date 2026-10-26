# 🚚 Smart Delivery Ops

**AI-Powered Smart Delivery Operations Platform** — an open-source, production-inspired
backend that models a food-delivery / local-logistics startup: order lifecycle,
geographic driver dispatch, dynamic pricing, demand forecasting, and operational
analytics in one system.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00?logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-8A2BE2.svg)](LICENSE)
[![CI](https://github.com/SiwarAttia6191/smart-delivery-ops/actions/workflows/ci.yml/badge.svg)](https://github.com/SiwarAttia6191/smart-delivery-ops/actions/workflows/ci.yml)

> From order to doorstep, optimized by AI — a portfolio project that connects
> backend engineering, data engineering, and machine learning in one realistic
> startup scenario.

---

## ✨ Features

| Domain | What it does |
|---|---|
| 📦 **Order management** | Create orders, track the full lifecycle (`pending → assigned → in_transit → delivered / cancelled`), immutable event log per order |
| 📡 **Event streaming** | Transactional outboxes → Kafka (`orders.events` and `drivers.events`) → warehouse raw layer with per-stream primary-key dedupe — at-least-once end to end |
| 📍 **Driver dispatch** | Geolocation-aware nearest-driver matching with vehicle filters and live availability heartbeats |
| 💸 **Dynamic pricing** | Distance-based fees with demand-surge tiers, minimum-fee floor, full price breakdown for auditability |
| ⏱️ **ETA estimation** | Prep + travel + buffer heuristic (Phase 1), upgrade path to an ML regression model |
| 📊 **Operational analytics** | KPI summary (status mix, revenue, average delivery time) + Prometheus metrics endpoint |
| 📚 **OpenAPI / Swagger** | Typed Pydantic contracts, interactive docs at `/docs` and `/redoc` out of the box |

## 🧱 Tech stack

- **API** — Python · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Swagger/OpenAPI
- **Database** — PostgreSQL 16 (Docker, default) · SQLite fallback for quick experiments
- **Streaming & transforms** — Apache Kafka (KRaft, Docker) · confluent-kafka · warehouse ingestion consumer · **dbt** (medallion: raw → staging → marts, official Docker image) · **Airflow** (LocalExecutor, scheduled dbt builds + freshness)
- **Geo layer (Phase 3)** — pluggable `DistanceProvider` (haversine / circuity / self-hosted **OSRM**) · shadow-mode comparison · challenger-vs-champion benchmark harness with snapshot-reproducible reports
- **Route preview** — saved Tunis OSRM GeoJSON, served by `GET /routes/demo` and visualized on the project demo page
- **Testing** — pytest + TestClient, **85% coverage floor**, run on **Python 3.11/3.12/3.13**
- **Quality gates** — Ruff (lint + format) · **mypy** (full static typing) · **pip-audit** (dependency CVEs) · Docker image build — all enforced as required CI checks behind a protected `main` branch
- **Observability** — Prometheus metrics at `/analytics/metrics`
- **Roadmap** — dbt warehouse + Airflow, Prophet/XGBoost forecasting, Streamlit dashboard, OpenTelemetry + Grafana (see [docs/roadmap.md](docs/roadmap.md))

## 🚀 Quickstart

```bash
git clone https://github.com/SiwarAttia6191/smart-delivery-ops.git
cd smart-delivery-ops

# 1) install (Windows: make setup · Linux/macOS: ./scripts/bootstrap.sh)
make setup

# 2) start PostgreSQL (production database) + seed demo data
make db-up
make seed

# 3) run the API
make api
```

Open **http://127.0.0.1:8010/docs** for the interactive Swagger UI, then try:

```bash
# quote a delivery
curl -X POST localhost:8010/pricing/quote -H "Content-Type: application/json" \
  -d '{"restaurant_id": 1, "dropoff_latitude": 36.8381, "dropoff_longitude": 10.2385, "demand_ratio": 1.3}'

# place + dispatch + deliver an order
curl -X POST localhost:8010/orders -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "restaurant_id": 1, "dropoff_latitude": 36.8381, "dropoff_longitude": 10.2385}'
curl -X POST localhost:8010/orders/<ORDER_ID>/assign
curl -X POST localhost:8010/orders/<ORDER_ID>/status -H "Content-Type: application/json" -d '{"status": "in_transit"}'
curl -X POST localhost:8010/orders/<ORDER_ID>/status -H "Content-Type: application/json" -d '{"status": "delivered"}'

# KPIs
curl localhost:8010/analytics/summary
```

Or run the full end-to-end scenario: `make smoke`.

## 🎮 Try it live — Swagger walkthrough

The fastest way to feel the whole lifecycle is the interactive docs at
**http://127.0.0.1:8010/docs** (start the API first: `make api`). Every step
below was executed against the real API with PostgreSQL behind it — the
captures are genuine responses, not mockups.

**1 · Open the docs** — all endpoint groups are listed by tag (health,
customers, restaurants, drivers, orders, pricing, analytics):

![Swagger UI overview](docs/images/swagger-1-overview.png)

**2 · Create an order** — expand `POST /orders`, press **Try it out**, set the
body (a customer, a restaurant, a drop-off point, and a demand ratio of 1.3 to
trigger surge pricing), then **Execute**. The response returns the order in
`pending` with the computed distance, fee — surged to ×1.25 — and ETA:

![Create order](docs/images/swagger-2-create-order.png)

**3 · Assign the nearest driver** — copy the order `id` from the response,
open `POST /orders/{order_id}/assign`, paste it, **Execute**. The dispatcher
picks the closest available driver and the order moves to `assigned` (the
driver becomes busy):

![Assign driver](docs/images/swagger-3-assign-driver.png)

**4 · Advance the lifecycle** — in `POST /orders/{order_id}/status`, execute
first with `{"status": "in_transit"}` …:

![Order in transit](docs/images/swagger-4-in-transit.png)

… then with `{"status": "delivered"}`. The response now carries a
`delivered_at` timestamp, `delivery_minutes`, and the full event history
(`created → assigned → status → status`); the driver is automatically freed:

![Order delivered](docs/images/swagger-5-delivered.png)

**5 · Watch the analytics update** — execute `GET /analytics/summary` and see
the platform totals reflect the order you just delivered (status mix, revenue,
average delivery time, fleet availability):

![Analytics summary](docs/images/swagger-6-analytics.png)

> Invalid moves are rejected by the state machine — e.g. delivering a
> `pending` order returns `409 invalid_transition`, and going straight to
> `assigned` via the status endpoint is blocked at the schema level (use
> `/assign`).

The local port is configurable: `make api API_PORT=9090` (default **8010**, chosen to avoid the crowded 8000).

**Docker (PostgreSQL profile):** `docker compose up --build` → API on **http://127.0.0.1:8010**, Postgres on :5434.

## 🗄️ Database

The default database is **PostgreSQL 16**, running as a Docker container
(`make db-up`) with credentials from `docker-compose.yml`:

| | |
|---|---|
| Host | `localhost:5434` (5432 in-container; host port avoids conflicts with a local PG install) |
| Database | `smart_delivery_ops` |
| User / password | `smartops` / `smartops` |
| Connection string | `postgresql+psycopg://smartops:smartops@localhost:5434/smart_delivery_ops` |

Connect with any client — `make db-shell` (psql inside the container), pgAdmin,
DBeaver, TablePlus — or run SQL directly:

```bash
docker compose exec postgres psql -U smartops -d smart_delivery_ops \
  -c "SELECT status, COUNT(*) FROM orders GROUP BY status;"
```

Data persists in the `pgdata` Docker volume across restarts. A zero-config
SQLite fallback remains available via `DATABASE_URL=sqlite:///./local.db`
(tests always run on isolated in-memory SQLite).

## 📡 Event pipeline

Order lifecycle and driver availability events flow onto separate Kafka topics
using the **transactional outbox** pattern. Each API operation commits its
business state and event together; the relay and warehouse consumer run off
the request path:

```
                          ┌──────────────────────── the platform ────────────────────────┐
orders ──► order_events ──► orders.events ──┐
                                             ├──► raw warehouse ──► dbt staging/marts
drivers ─► driver_events ─► drivers.events ─┘


Start the broker and the relay (the API comes up as before):

```bash
docker compose up -d
```

Watch either stream — order transitions and driver availability changes are
keyed by their order or driver id:

```bash
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:19092 --topic orders.events --from-beginning
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:19092 --topic drivers.events --from-beginning
```

Design notes:

- **Delivery guarantee: at-least-once.** Due rows are *claimed*
  (`published_at IS NULL`, `FOR UPDATE SKIP LOCKED`) and marked published only
  after the producer's delivery reports confirm the broker acknowledged every
  message — a crash anywhere before that replays the batch, and consumers
  dedupe by `event_id`. Claiming rows instead of tracking an id offset also
  removes the classic sequence-gap race (ids are assigned at INSERT but become
  visible at COMMIT) and makes concurrent relay instances safe.
- **Ordering:** order messages are keyed by order id and driver messages by
  driver id, preserving per-entity order in each separately provisioned
  three-partition topic.
- **The relay is restart-safe and retroactive** — it tails both outboxes
  continuously (idle poll 2 s) and treats broker/database outages as expected
  events: exponential backoff up to a 60 s cap, then resume. Topic data
  persists in the `kafkadata` volume. Full replay from the source of truth is
  done by clearing `published_at` and `quarantined_at` on both outboxes (the
  separate raw tables dedupe each stream by `event_id`).
- **Wire format:** versioned JSON (`schema_version: 1`) with `event_type` as a
  Kafka header.
- **Driver supply:** registration, dispatch, delivery/cancellation, and
  heartbeat availability transitions are committed to `driver_events` with
  the driver row. Location is snapshotted only when availability changes;
  continuous GPS pings are explicitly out of scope. The resulting supply
  metric is **last-evented-location supply** - a driver stays counted in the
  zone of their last transition (issue #60 carries the location-update
  design that converges it toward current-location supply).
- **Warehouse ingestion:** one `ingest` consumer (group `warehouse-ingest`)
  subscribes to both topics and lands them into `raw.order_events` and
  `raw.driver_events` in the **separate warehouse database**. It commits Kafka
  offsets only after both applicable stores have committed; replayed events
  dedupe by each table's `event_id` primary key.
- Host tooling (kcat, local scripts) reaches the broker on `localhost:9094`;
  containers on `kafka:19092`.

### 🏭 Warehouse transformations (dbt) — medallion architecture

The warehouse is layered exactly like the medallion pattern: **bronze** =
`raw.order_events` and `raw.driver_events` (append-only landings), **silver** =
typed, flattened staging views, **gold** = `analytics.*` marts
(business answers). `data/transform/` is a dbt project over the raw layer,
and six marts answer the business questions (`docker compose run --rm dbt build`, or `make dbt`; runs on the
official dbt-postgres image since dbt needs Python ≤ 3.12):

| Mart (schema `analytics`) | Answers |
|---|---|
| `mart_daily_order_summary` | Daily funnel: created vs delivered, revenue, avg delivery minutes |
| `mart_demand_by_zone_hour` | Demand heatmap: orders per ~1 km dropoff zone per hour (Phase 3 forecasting input) |
| `mart_driver_utilization` | Per-courier workload: assignments, completions, match distance |
| `mart_driver_supply_by_zone_hour` | Average available drivers per ~1 km zone during each Africa/Tunis local hour |
| `mart_demand_supply_by_zone_hour` | Pickup demand vs available supply joined by same zone/date/hour |
| `mart_pricing_history` | Every quote: distance, fee, surge, fee-per-km (Phase 3 pricing-model feed) |

Every build also runs the data tests: unique/not-null keys on the source and
marts, accepted-values checks on event types and `schema_version`.

### 🛡️ Data quality — three lines of defense

`make dbt` runs **45 checks** (the live-build total: 6 source + 4 staging +
27 mart-column tests with the generic macros instantiated per column, plus 3
singular business-rule tests). Three lines of defense:

1. **Source gates** — unique/not-null `event_id`, accepted-values on
   `event_type` and `schema_version`. Source-level failures *block* the build:
   bad data never reaches the marts.
2. **Mart guards** — reusable `non_negative` / `between` macros: revenue and
   counts can't go negative, `order_hour` is bounded 0–23, and the surge
   multiplier is bounded to the engine's legal band **1.0–2.0** (the tier-3
   cap). Singular business-rule tests: daily revenue must reconcile between
   the funnel and pricing marts, delivered orders must have a prior
   assignment, delivered can never exceed created.
3. **Freshness** — `dbt source freshness` on `max(ingested_at)` (warn 24 h /
   error 72 h — bounds set for a low-traffic demo; the per-partition
   heartbeat is the documented production upgrade) turns a silently-stalled
   pipeline into a loud incident. `make dbt` runs build + freshness.

Every failure mode below was demonstrated live by injecting bad rows (the
PASS/ERROR/SKIP totals are from those runs, not the clean build):

```text
# 1 · a corrupt event_type halts the build before any model runs
FAIL 1 source_accepted_values_raw_order_events_event_type__created__assigned__status
Done. PASS=5  WARN=0  ERROR=1  SKIP=39  TOTAL=45

# 2 · a "created" event with a negative fee and an out-of-band surge
FAIL 1 between_mart_pricing_history_surge_multiplier__2_0__1_0
FAIL 1 non_negative_mart_pricing_history_delivery_fee
FAIL 1 non_negative_mart_pricing_history_fee_per_km
Done. PASS=42  WARN=0  ERROR=3  SKIP=0  TOTAL=45

# 3 · a stalled pipeline (no fresh rows in 25h) — captured under the
#    original 12h/24h bounds; today's policy warns at 24h, errors at 72h
ERROR STALE freshness of raw.order_events
```

## 🧭 Phase 3 — learning layer (started)

### 📐 Geo benchmarking: distance is an interface, not a decision

The distance behind every quote and dispatch is pluggable
(`domain/rules/geo.py`): a `DistanceProvider` protocol with three
implementations - **haversine** (the champion), **circuity ×1.3** (offline
corrector), and **OSRM road routing** (self-hosted Docker container, no API
key). Services take a provider and default to the configured champion; a
*shadow* challenger can be computed alongside every call and logged without
ever influencing business decisions.

The challenger-vs-champion harness (`scripts/benchmark_geo.py`) scores them
against OSRM ground truth per distance bucket, and every report records the
dataset snapshot (`max(event_id)` + git SHA). First live run
(`data/benchmarks/`):

| bucket | haversine bias | circuity ×1.3 bias |
|---|---|---|
| <2 km | −21% | +2.5% |
| 2–5 km | −37% | −18% |
| >10 km | −27% | −5% |

Haversine systematically under-estimates; circuity corrects most of the bias
offline. The champion stays haversine **until shadow-mode evidence over real
traffic says otherwise** - migration by measurement, not faith.

Run it yourself: `docker compose run --rm osrm-prepare` (once), `docker
compose up -d osrm`, then `PYTHONPATH=. python scripts/benchmark_geo.py`.
(Map data under `data/osrm/` - source, license and regeneration are
documented in [`data/README.md`](data/README.md).)

#### How distance and speed are computed

**Distance — three definitions of "how far":**

| provider | what it computes | cost | used as |
|---|---|---|---|
| `haversine` | great-circle ("as the crow flies") arc on a sphere - a closed formula over the two lat/lon pairs. Ignores roads, one-way streets, rivers. | instant, offline | **champion** (business decisions) |
| `circuity ×1.3` | haversine × an urban circuity factor: real road paths are consistently ~1.3× longer than straight lines (detours, one-ways). The factor is the benchmark's median correction. | instant, offline | offline challenger |
| `osrm` | true shortest path over the OpenStreetMap road graph (self-hosted container, Tunisia extract) - road distance *and* free-flow travel time per vehicle profile. | ~10 ms/call, self-hosted | benchmark ground truth + shadow challenger |

**Speed — how a distance becomes an ETA.** The Phase 1 heuristic is
deliberately legible:

```
ETA = kitchen prep (12 min) + travel (distance ÷ 20 km/h) + handoff buffer (5 min)
```

- **20 km/h** is the *effective urban courier speed* - not the vehicle's
  top speed but door-to-door city reality (stops, traffic, parking), matching
  the observed 17–21 km/h courier band. It is config (`ETA_AVG_SPEED_KMH`),
  not a magic number.
- Worked example, the README's own 6 km quote: `12 + (6.17 ÷ 20 × 60) + 5`
  = `12 + 18.5 + 5` ≈ **36 min** - consistent with observed city averages.
- The Phase 3 ETA model (#26) must **beat this heuristic in the benchmark
  harness** to replace it - same challenger-vs-champion discipline as
  distance.
- **On traffic:** self-hosted OSRM has no live-traffic feed - its durations
  are free-flow estimates from static OSM tags. Distances are
  traffic-independent (solid ground truth); ETAs are evaluated against the
  only grounded source there is: **our own realized deliveries**
  (created -> delivered timestamps in the event log), with the ETA model
  learning real congestion patterns from time-of-day features. See issue
  #26 for the full evaluation design.

**Where each is used:** distance feeds the delivery fee (`per_km`), the
nearest-driver dispatch, and the ETA; speed only feeds the ETA. All of it
lives in `domain/rules/` as pure functions over `core/config.py` settings -
no hidden constants.

### ⏰ Orchestration (Airflow)

The whole warehouse routine is scheduled: **Airflow 2.10 (LocalExecutor)**
runs the `warehouse_daily_build` DAG daily at 03:00 — `dbt build` (all 45
checks) followed by `dbt source freshness`, with retries and exponential
backoff. UI at `localhost:8081` (admin/admin in dev); CLI shortcuts:
`make airflow-trigger`, `make airflow-state`.

- dbt lives in an **isolated venv** inside the Airflow image — dbt-core and
  apache-airflow have conflicting transitive pins; the venv sidesteps them.
- Tasks are **idempotent by construction** (marts fully rebuilt per run), so
  Airflow's re-run/backfill machinery is safe to lean on.
- The Airflow metadata database lives on the same Postgres instance and is
  self-provisioned on first boot, like everything else in this stack.

## 🗂️ Project structure

```
smart-delivery-ops/
├── apps/
│   ├── api/            # FastAPI app: routes, Pydantic schemas, deps
│   ├── dashboard/      # Phase 4 — Streamlit operations dashboard
│   └── worker/         # Phase 2 — event bus consumers (outbox relay, ingestion)
├── core/               # config, database, logging, exceptions
├── data/transform/     # dbt project: staging + warehouse marts (Phase 2)
├── domain/
│   ├── models/         # SQLAlchemy entities (Customer, Driver, Restaurant, Order, OrderEvent)
│   ├── rules/          # pure business rules (geo, pricing tiers, driver eligibility)
│   └── services/       # use-cases (order lifecycle, dispatch, pricing, ETA)
├── data/               # transform/ (dbt) · osrm/ (map data, gitignored) · benchmarks/ (reports) — see data/README.md
├── ml/                 # Phase 3 — features, training, model registry
├── infra/              # docker, kubernetes, monitoring
├── tests/              # unit + integration
├── scripts/            # seed, smoke test, bootstrap
└── docs/               # architecture, data model, API, ML design, roadmap
```

## 🧭 Roadmap

| Phase | Goal | Status |
|---|---|---|
| 1 | Foundation & MVP — orders, dispatch, pricing, API, tests | ✅ **done** |
| 2 | Data engineering — event ingestion, warehouse, ETL | 🔜 |
| 3 | AI/ML — demand forecasting, ETA model, smart allocation | 🔜 |
| 4 | Dashboard & BI — KPIs, trends, drill-downs | 🔜 |
| 5 | Production readiness — Docker, CI/CD, observability, auth | 🟡 partially (CI, Docker, metrics already in) |

Details: [docs/roadmap.md](docs/roadmap.md) · Architecture: [docs/architecture.md](docs/architecture.md)

## 🏗️ How this repo was built

The git history is organized as one task per commit — each stage below maps to
one commit and explains what landed and why.

1. **`chore: initialize repository foundation`** — repo tooling first: `.gitignore`/`.gitattributes`, MIT `LICENSE`, `pyproject.toml` (pytest + ruff configuration), dependency manifests, a `Makefile` as the single developer entrypoint, and `.env.example` establishing the env-driven configuration convention.
2. **`feat(core)`** — the support layer: pydantic-settings configuration (pricing, ETA, and database all tunable via environment), the SQLAlchemy engine + session factory, structured logging, and typed domain exceptions (`NotFound`, `InvalidTransition`, `NoDriverAvailable`) that the API maps to consistent HTTP error envelopes.
3. **`feat(domain)`** — the business heart, framework-free: SQLAlchemy 2.0 entities including an immutable `OrderEvent` log (the raw feed for the Phase 2 pipeline, present from day one), *pure* business rules (haversine geo, surge tiers, driver eligibility — side-effect-free so they're exhaustively testable and swappable for ML later), and services orchestrating the order state machine, nearest-driver dispatch, quoting, and ETA.
4. **`feat(api)`** — the FastAPI surface over the domain: 19 endpoints, Pydantic v2 request/response contracts, Swagger/Redoc out of the box, CORS, and a Prometheus text metrics endpoint.
5. **`test`** — unit tests for the pure rules and state machine plus integration tests driving the full HTTP flow (create → assign → deliver → analytics) against an isolated in-memory database — 30 tests, hermetic by design.
6. **`feat(scripts)`** — demo seed data (Tunis metro area) and a scripted smoke test that exercises a *live* server end-to-end.
7. **`build(infra)`** — Docker packaging: the API image, a PostgreSQL 16 service with a persistent volume and healthcheck, and placeholders for the Phase 5 Kubernetes/monitoring assets.
8. **`ci`** — GitHub Actions running ruff + pytest on every push and PR, plus weekly Dependabot updates.
9. **`chore: scaffold future-phase layers`** — directory structure and guidance for the Phase 2 data layer, Phase 3 ML layer, event worker, and dashboard, so the roadmap has a concrete home.
10. **`docs`** — architecture (Mermaid system diagram), data model (ER + order state machine), API catalogue, ML design, and the phased roadmap.
11. **`fix: run API on port 8010`** — port 8000 was occupied by another local project; the port became configurable (`API_PORT`) across the Makefile, scripts, and compose.
12. **`feat(db): switch default database to PostgreSQL`** — production stack by default: the Postgres container, the psycopg driver in core deps, `make db-up/db-shell` targets; tests pinned to throwaway SQLite so CI stays hermetic.
13. **`fix(db): move PostgreSQL host port to 5434`** — 5433 was taken by another Docker project; also documents that the compose-internal URL stays on `postgres:5432` (host mappings only apply outside Docker's network).
14. **`fix(db): healthcheck probes the real database`** — `pg_isready -d smart_delivery_ops` stops a harmless but noisy `FATAL` in the logs every 5 seconds.
15. **`docs: README + live Swagger walkthrough`** — this README, including the step-by-step "Try it out" guide with genuine captures of executed requests.

## 📖 Documentation

- [Architecture overview](docs/architecture.md) — system diagram and design decisions
- [API reference](docs/api.md) — endpoint catalogue (also live at `/docs`)
- [Data model](docs/data_model.md) — tables, relationships, order state machine
- [ML design](docs/ml_design.md) — forecasting and ETA modelling plan

## 📄 License

[MIT](LICENSE) © 2026 Siwar Attia

---

*This project is an independent, from-scratch implementation inspired by common
delivery-startup system-design exercises; it reuses no code from any third-party
repository.*
