# 🚚 Smart Delivery Ops

**AI-Powered Smart Delivery Operations Platform** — an open-source, production-inspired
backend that models a food-delivery / local-logistics startup: order lifecycle,
geographic driver dispatch, dynamic pricing, demand forecasting, operational
analytics — and an **AI copilot** (LangGraph agent + RAG) that answers ops questions
in plain language and narrates incidents when something breaks.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00?logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-8A2BE2.svg)](LICENSE)
[![CI](https://github.com/SiwarAttia6191/smart-delivery-ops/actions/workflows/ci.yml/badge.svg)](https://github.com/SiwarAttia6191/smart-delivery-ops/actions/workflows/ci.yml)

> From order to doorstep, optimized by AI — a portfolio project that connects
> backend engineering, data engineering, and machine learning in one realistic
> startup scenario.

---

**Landing page:** https://siwarattia6191.github.io/smart-delivery-ops/ (GitHub Pages, served from `docs/`)

## ✨ Features

| Domain | What it does |
|---|---|
| 📦 **Order management** | Create orders, track the full lifecycle (`pending → assigned → in_transit → delivered / cancelled`), immutable event log per order |
| 📡 **Event streaming** | Transactional outboxes → Kafka (`orders.events` and `drivers.events`) → warehouse raw layer with per-incarnation primary-key dedupe (a rebuilt ops database can never collide with an older incarnation's ids) — at-least-once end to end |
| 📍 **Driver dispatch** | Geolocation-aware nearest-driver matching with vehicle filters and live availability heartbeats |
| 💸 **Dynamic pricing** | Distance-based fees with demand-surge tiers, minimum-fee floor, full price breakdown for auditability; per-tenant **coupon codes** (`POST /coupons`) apply as an auditable discount line on the quote (`discount`, `final_total`) |
| ⏱️ **ETA estimation** | Served by the MLflow-registered LightGBM model (beat the heuristic 5.2 vs 17.8 min MAE on realized deliveries); calibrated heuristic as automatic fallback |
| 📊 **Operational analytics** | KPI summary (status mix, revenue, average delivery time) + Prometheus metrics endpoint + per-tenant breakdown (`/analytics/by-tenant`) |
| 🏢 **Multi-tenancy** | JWTs carry the issuing user's tenant binding; Postgres **Row-Level Security** (forced on all seven tenant tables — the six business tables plus coupons) isolates tenants at the database engine — reads, writes, and even foreign-key references; per-tenant pricing overrides (`PUT /pricing/config`); the tenant rides inside the event envelope to the warehouse ([decision record](docs/decisions/0001-multi-tenancy.md)) |
| 🤖 **AI copilot** | `POST /copilot/chat` — a LangGraph supervisor whose read-only tools run on the request's session, so Row-Level Security scopes every answer to the caller's tenant; pgvector RAG over the runbook and decision records; guardrails (injection screening, question caps), tool-call + token budgets, and a flagship→mini fallback chain ([issue #120](https://github.com/SiwarAttia6191/smart-delivery-ops/issues/120)) |
| 🚨 **Incident narrator** | When a pipeline task fails, the alert arrives already explaining itself — the narrator correlates the failure with the platform's own metrics and the latest MLflow benchmark run, and fails open per source (#122) |
| 🧪 **Eval harness** | A versioned golden set (question → expected tools → answer shape) runs in CI — a wrong tool call or a broken answer shape fails the build — and a scheduled Airflow eval scores the configured provider into an MLflow experiment (#123) |
| 📝 **Obsidian bridge** | Your own notes join the copilot's knowledge: `make vault-ingest` syncs an Obsidian vault into the pgvector store (changed notes replaced, deleted notes tombstoned), and `search_docs` scopes questions to your notes vs. the shipped docs |
| 📚 **OpenAPI / Swagger** | Typed Pydantic contracts, interactive docs at `/docs` and `/redoc` out of the box |

## 🧱 Tech stack

- **API** — Python · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Swagger/OpenAPI
- **Database** — PostgreSQL 16 (Docker, default) · SQLite fallback for quick experiments
- **Streaming & transforms** — Apache Kafka (KRaft, Docker) · confluent-kafka · warehouse ingestion consumer · **dbt** (medallion: raw → staging → marts, official Docker image) · **Airflow** (LocalExecutor, scheduled dbt builds + freshness)
- **Geo layer (Phase 3)** — pluggable `DistanceProvider` (haversine / circuity / self-hosted **OSRM**) · shadow-mode comparison · challenger-vs-champion benchmark harness with snapshot-reproducible reports
- **ML layer (Phase 3)** — **MLflow** tracking (Docker, Postgres-backed) · demand-forecast benchmark (seasonal-naive vs LightGBM vs Prophet, report committed per run) · **forecast serving loop**: pooled LightGBM writes zone-hour forecasts, the pricing engine derives `demand_ratio` from them live
- **Route preview & per-order geometry** — saved Tunis OSRM GeoJSON demo (`GET /routes/demo`), plus real orders capture their road path once per pickup→dropoff pair (`GET /orders/{id}/route`, `application/geo+json`) — tracking UI, delivery proof, and free benchmark pairs
- **Testing** — pytest + TestClient, **85% coverage floor**, run on **Python 3.11/3.12/3.13**
- **Quality gates** — Ruff (lint + format) · **mypy** (full static typing) · **pip-audit** (dependency CVEs) · Docker image build — all enforced as required CI checks behind a protected `main` branch
- **AI layer** — **LangGraph** supervisor graph · **pgvector** knowledge store (RAG over the runbook) · OpenAI-compatible provider behind a pluggable chain · deterministic scripted provider so the whole agent path is CI-provable keyless
- **Load & observability** — **k6** scenarios (order flow / reads / spike / breaking-point probe) · Prometheus at `/analytics/metrics` · **Grafana** (provisioned dashboards, anonymous view) · **Alertmanager** (routed alerts, RelayStale inhibits OutboxBacklog)
- **Roadmap** — dbt warehouse + Airflow, Prophet/XGBoost forecasting, Streamlit dashboard, OpenTelemetry + Grafana (see [docs/roadmap.md](docs/roadmap.md))

## 🚀 Quickstart

```bash
git clone https://github.com/SiwarAttia6191/smart-delivery-ops.git
cd smart-delivery-ops

# 1) install (Windows: make setup · Linux/macOS: ./scripts/bootstrap.sh)
make setup

# 2) start PostgreSQL (production database) + migrations + seed + demo API users
make db-up
make migrate                            # Alembic chain — the schema's owner (#28)
make seed
python scripts/create_user.py --demo   # admin/admin · ops/ops · view/view (dev-only)

# 3) run the API
make api
```

Open **http://127.0.0.1:8010/docs** for the interactive Swagger UI, then try:

```bash
# get a token (every MUTATING endpoint requires one — GETs and /pricing/quote stay open)
TOKEN=$(curl -s -X POST localhost:8010/auth/token -d "username=ops&password=ops" | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

# quote a delivery (open — a pure read)
curl -X POST localhost:8010/pricing/quote -H "Content-Type: application/json" \
  -d '{"restaurant_id": 1, "dropoff_latitude": 36.8381, "dropoff_longitude": 10.2385, "demand_ratio": 1.3}'

# place + dispatch + deliver an order (Bearer required)
curl -X POST localhost:8010/orders -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "restaurant_id": 1, "dropoff_latitude": 36.8381, "dropoff_longitude": 10.2385}'
curl -X POST localhost:8010/orders/<ORDER_ID>/assign
curl -X POST localhost:8010/orders/<ORDER_ID>/status -H "Content-Type: application/json" -d '{"status": "in_transit"}'
curl -X POST localhost:8010/orders/<ORDER_ID>/status -H "Content-Type: application/json" -d '{"status": "delivered"}'

# KPIs
curl localhost:8010/analytics/summary
```

Or run the full end-to-end scenario: `make smoke`.

## 🌍 Run it in another city

The platform is region-parameterized (#78): **one deployment = one city**, driven by a
manifest in [`config/cities/`](config/cities) — seed coordinates, business timezone
(IANA, DST-safe), benchmark grid, OSRM extract, demo map view. Tunis ships as the
default; **Paris** runs end-to-end as the second verified region (Europe/Paris grains
cross the October DST boundary, the Île-de-France extract routes its orders).

Bring up a second city **side-by-side** (fresh compose project, shifted ports, the
Tunis demo untouched):

```bash
python scripts/fetch_osrm_extract.py --region paris    # ~300 MB Geofabrik extract (host)
docker compose --env-file deploy/cities/paris.env -p paris up -d \
    postgres api worker ingest kafka kafka-init osrm mlflow
docker compose --env-file deploy/cities/paris.env -p paris run --rm osrm-prepare

# seed + backfill against the Paris stack (host port 5435), manifest zone enforced
DATABASE_URL=postgresql+psycopg://smartops:smartops@localhost:5435/smart_delivery_ops \
BUSINESS_TIMEZONE=Europe/Paris python scripts/seed_demo_data.py --region paris
DATABASE_URL=postgresql+psycopg://smartops:smartops@localhost:5435/smart_delivery_ops \
BUSINESS_TIMEZONE=Europe/Paris python scripts/backfill_order_history.py --region paris
```

The timezone guards itself: seed/backfill **refuse to run** when `BUSINESS_TIMEZONE`
disagrees with the manifest — no city's history can be mis-grained by a stale env.
`GET /config` serves the active manifest (label, timezone, map view) so the demo page
follows the city. Adding a new city = one JSON file; swapping the default deployment's
map region = `OSRM_EXTRACT_ID` + `OSRM_DATA_DIR` (see `.env.example`).

## 🎮 Try it live — Swagger walkthrough

The fastest way to feel the whole lifecycle is the interactive docs at
**http://127.0.0.1:8010/docs** (start the API first: `make api`). Every step
below was executed against the real API with PostgreSQL behind it — the
captures are genuine responses, not mockups.

**0 · Authorize** — mutating endpoints need a JWT (#28): click the green
**Authorize** button, log in with the demo `ops` / `ops` credentials
(OAuth2 password flow), and every subsequent "Try it out" rides the token.
The captures below are exactly what you see afterwards.

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

## 📊 Dashboard (Phase 4, #27)

The Streamlit dashboard is two pages: the KPI one-pager over the marts, and the
**Copilot chat** — sign in (demo: ops/ops) and ask about your operation in plain
language. The response metadata (provider, tool calls, degraded reason) is shown,
not hidden: when the live model is unfunded, the chat says exactly why.

A one-page **Streamlit** app over the dbt marts — order volume, fulfillment
rate, revenue, demand-vs-supply by zone, driver utilization, the ML
forecast vs actual demand, and the per-tenant breakdown the isolation layer
(#36) feeds via `mart_tenant_daily`. Read-only by construction: the
dashboard holds no write path to anything. It ships in the stack:

```bash
docker compose up -d dashboard      # http://localhost:8501
```

![Operational dashboard](docs/images/dashboard.png)

The sidebar filters scope every card and chart: a sliding time window and a
tenant selector. The forecast-vs-actual chart is the one sanctioned read
outside the marts — the serving DAG writes predictions to the ops DB.

## 🗄️ Database

The default database is **PostgreSQL 16**, running as a Docker container
(`make db-up`) with credentials from `docker-compose.yml`:

| | |
|---|---|
| Host | `localhost:5434` (5432 in-container; host port avoids conflicts with a local PG install) |
| Database | `smart_delivery_ops` |
| User / password | `smartops` / `smartops` (admin — migrations and ad-hoc work) |
| Runtime role | `sdo_app` (same password in dev) — what the api/worker containers connect as: a least-privilege, non-superuser role the RLS policies apply to (#36). A superuser bypasses row security by definition. |
| Connection string | `postgresql+psycopg://smartops:smartops@localhost:5434/smart_delivery_ops` |

### Schema ownership: Alembic migrations (#28)

The ops schema is owned by a **versioned Alembic chain** (`migrations/`):
the compose `migrate` one-shot service applies it before api/worker start
(`make migrate`), and the API's lifespan re-applies idempotently on boot.
`create_all` survives only for throwaway test schemas — and
`tests/unit/test_migrations_parity.py` fails the build the moment the
chain drifts from the models. The pre-Alembic demo database was **stamped**
to head (never hand-altered); every schema change after that is a real
revision:

```bash
alembic revision --autogenerate -m "add tenants"   # diff models vs DB
make migrate                                        # apply
```

The warehouse's raw layer is deliberately NOT migrated: it is the ingest
worker's stream store, rebuilt from Kafka replay by design.

### 🔐 Secrets (#28)

Credentials live in **gitignored files** (`secrets/`), mounted as Docker
secrets at `/run/secrets/...` — they no longer travel as plaintext values
in compose config or `docker inspect` output:

| secret file | consumed by | how |
|---|---|---|
| `secrets/db_password.txt` | postgres, api, worker, ingest, migrate, mlflow, airflow, dbt | native `POSTGRES_PASSWORD_FILE`; settings `DB_PASSWORD_FILE` (overrides the URL's password); bash `$(cat ...)` |
| `secrets/jwt_secret.txt` | api | settings `JWT_SECRET_FILE` (random per deployment — rotations invalidate tokens) |
| `secrets/airflow_conn.txt` | airflow | `SQL_ALCHEMY_CONN_CMD` (Airflow runs the command and uses stdout) |

`make secrets` (also part of `make setup` / `make db-up` /
`scripts/bootstrap.sh`) generates the dev defaults idempotently.
**Production**: mount vault-injected files at the same paths — no code
change. **Rotation**: update `secrets/db_password.txt`, `ALTER USER` in
Postgres, restart the consumers; the compose files never change.

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
  the driver row. Location moves beyond a hysteresis threshold (500 m,
  config) emit their own `location_update` event (#60), so the supply mart
  tracks a driver **across zones** while continuous GPS pings stay out of
  scope. Heartbeats that go silent past a stale window (120 min, config)
  are swept offline by the worker (`stale_sweep`) - silent drivers stop
  counting as supply instead of being carried forever. The supply metric
  converges toward current-location supply as update frequency rises.
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
and seven marts answer the business questions (`docker compose run --rm dbt build`, or `make dbt`; runs on the
official dbt-postgres image since dbt needs Python ≤ 3.12):

| Mart (schema `analytics`) | Answers |
|---|---|
| `mart_daily_order_summary` | Daily funnel: created vs delivered, revenue, avg delivery minutes |
| `mart_demand_by_zone_hour` | Demand heatmap: orders per ~1 km dropoff zone per hour (Phase 3 forecasting input) |
| `mart_driver_utilization` | Per-courier workload: assignments, completions, match distance |
| `mart_driver_supply_by_zone_hour` | Average available drivers per ~1 km zone during each Africa/Tunis local hour |
| `mart_demand_supply_by_zone_hour` | Pickup demand vs available supply joined by same zone/date/hour |
| `mart_pricing_history` | Every quote: distance, fee, surge, fee-per-km (Phase 3 pricing-model feed) |
| `mart_demand_timeseries` | Time-ordered demand per zone × Africa/Tunis local hour — the forecasting training input (not an average shape) |

Every build also runs the data tests: unique/not-null keys on the source and
marts, accepted-values checks on event types and `schema_version`.

### 🛡️ Data quality — three lines of defense

`make dbt` runs **49 nodes** per build: 9 models + **40 data tests**
(12 source-declared, 24 column/schema tests, 4 singular business-rule tests + 1 event-type contract
including grain-uniqueness and the delivered-orders-have-assignments
referential rule). Three lines of defense:

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
dataset snapshot (`max(event_id)` + git SHA). **Shadow mode is live**: the
compose stack runs the OSRM challenger alongside every haversine decision
and logs the delta — real traffic is accumulating the comparison evidence
right now. First benchmark run (`data/benchmarks/`):

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
- **The ETA benchmark has run** (`make eta-benchmark`, harness in
  `ml/training/benchmark_eta.py`): ground truth is our own realized
  deliveries (created → delivered spans), scored on the newest 20% of
  orders. Result on 1,431 realized deliveries: the heuristic champion MAE
  **17.8 min**, OSRM free-flow + prep + buffer **6.4**, LightGBM regression
  **5.2** — the challenger **beat the champion** and is registered in the
  MLflow model registry as `eta_model` (Production stage, verdict recorded).
  The heuristic's systematic under-estimate is a finding: it quotes
  prep + travel + buffer, while the realized span also includes assignment
  and pickup wait. **Serving has swapped**: with `ETA_MODEL_SOURCE=registry`
  (default in compose) the API loads `models:/eta_model/Production` at
  startup — the native LightGBM booster from the registered artifact — and
  order ETAs come from the model (a 12 km order now quotes **22 min** where
  the heuristic said 54). Failure is a first-class citizen: if the registry
  is unreachable or prediction fails, the heuristic answers, loudly logged,
  with `sdo_eta_model_serving 0` on the metrics endpoint.
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

### 🔮 Demand forecasting: trained, benchmarked, and serving (#25)

The forecast loop is end-to-end: warehouse → training → benchmark → serving
table → pricing.

- **Training input**: `mart_demand_timeseries` — every zone's hourly demand
  as a time-ordered series (the demo backfill,
  `scripts/backfill_order_history.py`, pushes 180 days of structured Tunis
  traffic through the *actual* pipeline — outbox → Kafka → ingest → dbt —
  with drift, weekend/holiday spikes, weather dips, outage hours, per-zone
  heterogeneity and overdispersed counts, so seasonality has something real
  to learn and every layer exercises production behavior).
- **Benchmark** (`make ml-benchmark`, one-shot container): seasonal-naive
  baseline vs LightGBM vs Prophet on a time-based holdout (never random —
  the future must not leak into training). Every run logs params, metrics
  and the dataset snapshot (`max(event_id)` + git SHA) to **MLflow**
  (UI `localhost:5100`), and the comparison report is committed under
  `data/benchmarks/`. First run: **both challengers beat the baseline**
  (MAE 1.16 / 1.15 vs 2.39) — the harness's promotion rule is "ship only if
  it beats the incumbent", same as the geo layer.
- **Serving** (`make ml-forecast`): a pooled LightGBM — one model across all
  zone cells with lag + zone + calendar features, because ~27 zone cells are
  far too sparse for a univariate fit per zone — writes the next 24 hours of
  zone-hour forecasts into the ops database (`demand_forecasts`).
- **Pricing consumes it live**: quote/order requests without an explicit
  `demand_ratio` derive one from the forecast table — observed orders in the
  current zone-hour ÷ the forecast's expectation for the elapsed part of the
  hour, floored at 0.5, capped at 2.5 — and the existing surge tiers do the
  rest. Missing forecast ⇒ neutral 1.0: absent data can never spike a fee.

### ⏰ Orchestration (Airflow)

The warehouse routine AND the ML loop are scheduled: **Airflow 2.10
(LocalExecutor)** runs three DAGs — UI at `localhost:8081` (admin/admin in
dev):

| DAG | schedule | what it does |
|---|---|---|
| `warehouse_daily_build` | 03:00 UTC daily | `dbt build` (all 49 checks) + `dbt source freshness` |
| `ml_forecast_serving` | hourly | retrains the pooled LightGBM and refreshes the next-24h `demand_forecasts` the surge loop reads — the loop can no longer go dormant because nobody ran `make ml-forecast` |
| `ml_retrain_benchmark` | 04:15 UTC daily (after the warehouse build) | re-runs both benchmarks on the accumulated history; the ETA benchmark's promotion stays champion-gated — a model that can't beat the heuristic never reaches Production |

CLI shortcuts: `make airflow-trigger` / `airflow-state`,
`make ml-serving-trigger` / `ml-retrain-trigger` / `ml-state`.

- The ML tasks run **in-process** (the same fat-worker pattern as the dbt
  tasks): the Airflow image carries mlflow/lightgbm/prophet, the `ml/`
  code is bind-mounted live, and every run stamps its exact `GIT_SHA` from
  the read-only `.git` mount. The documented production upgrade path is
  `KubernetesPodOperator` to isolate training from the scheduler.
- `max_active_runs=1` on the serving DAG — two concurrent forecast
  snapshots would race their prune windows.
- dbt lives in an **isolated venv** inside the Airflow image — dbt-core and
  apache-airflow have conflicting transitive pins; the venv sidesteps them.
- Tasks are **idempotent by construction** (marts fully rebuilt per run,
  forecast tables are per-run snapshots), so Airflow's re-run/backfill
  machinery is safe to lean on.
- The Airflow metadata database lives on the same Postgres instance and is
  self-provisioned on first boot, like everything else in this stack.

## 🤖 The AI copilot (Phase 5)

```bash
TOKEN=$(curl -s -X POST localhost:8010/auth/token -d 'username=ops&password=ops' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST localhost:8010/copilot/chat -H "Authorization: Bearer $TOKEN"   -H 'Content-Type: application/json'   -d '{"question":"How many orders were delivered this week and what was the average delivery time?"}'
```

The answer is not a text prompt over raw rows — it is a **LangGraph supervisor**
calling the platform's own governed tools, with five reliability layers around it:

- **Tenant-scoped by construction** — the graph's read-only tools execute on the
  request's session, so Postgres RLS scopes every answer to the caller's tenant.
- **Guardrails first** — instruction-injection screening and question caps reject
  before any model call (a probe answers 422, never the system prompt).
- **Budgets, not vibes** — a tool-call budget and a vendor-reported token budget
  both end the loop gracefully; LangGraph's recursion limit backs them.
- **A fallback chain, measured** — flagship model → mini model → canned degraded
  answer; the response names which rung served and *why* earlier ones fell
  (`degraded_reason`), so an unfunded key degrades loudly instead of mysteriously.
- **Everything traces** — one MLflow run per conversation (provider, rung, tool
  calls, degraded reason, token total, the full message log), and a **golden eval
  set** enforced in CI plus a scheduled Airflow eval scoring the live provider.

The **incident narrator** rides the alert path: when a pipeline task fails, the
alert carries a narrated summary (what broke, the current metrics, the first
checks) — deterministic template when the model is unavailable, live model when
funded, failing open per evidence source either way.

Your **Obsidian vault** joins the same store (`make vault-ingest`) — the copilot cites your own notes beside the runbook.

Phase issues: [#120](https://github.com/SiwarAttia6191/smart-delivery-ops/issues/120)
(core + RAG + tracing) · [#121](https://github.com/SiwarAttia6191/smart-delivery-ops/issues/121)
(live chain + budgets) · [#122](https://github.com/SiwarAttia6191/smart-delivery-ops/issues/122)
(narrator) · [#123](https://github.com/SiwarAttia6191/smart-delivery-ops/issues/123) (evals).

## 📊 Load & monitoring (measured, not claimed)

The k6 suite (`load/`, `make load`) runs four shapes against the live stack:
the order-flow write path (50 concurrent writers, p95 930 ms), the read profile
(50 req/s, p95 777 ms), a spike (5→300 VUs, degradation not availability loss),
and the committed **breaking-point probe** (300 concurrent readers — the
saturation shape that drove the three-worker api and the pool sizing). The
stack ships with **Prometheus** (rules for outbox backlog, relay staleness,
ETA-model fallback), **Grafana** (provisioned ops dashboard, anonymous view),
and **Alertmanager** (routed alerts with RelayStale inhibiting OutboxBacklog) —
see [`docs/runbook.md`](docs/runbook.md), "When something pages".

## 🗂️ Project structure

```
smart-delivery-ops/
├── apps/
│   ├── api/            # FastAPI app: routes, Pydantic schemas, deps
│   ├── dashboard/      # Phase 4 — Streamlit operations dashboard
│   └── worker/         # Phase 2 — event bus consumers (outbox relay, ingestion)
├── core/               # config, database, logging, exceptions
├── config/cities/      # region pack (#78): one manifest per city (tunis, paris)
├── data/transform/     # dbt project: staging + warehouse marts (Phase 2)
├── domain/
│   ├── models/         # SQLAlchemy entities (Customer, Driver, Restaurant, Order, OrderEvent)
│   ├── rules/          # pure business rules (geo, pricing tiers, driver eligibility)
│   └── services/       # use-cases (order lifecycle, dispatch, pricing, ETA)
├── data/               # transform/ (dbt) · osrm/ (map data, gitignored) · benchmarks/ (reports) — see data/README.md
├── ml/                 # Phase 3 — training (benchmark_demand), pipelines (forecast serving)
├── infra/              # docker, kubernetes, monitoring
├── deploy/cities/      # side-by-side second-city deployment envs (paris.env)
├── tests/              # unit + integration
├── scripts/            # seed, smoke test, bootstrap
└── docs/               # architecture, data model, API, ML design, roadmap
```

## 🧭 Roadmap

| Phase | Goal | Status |
|---|---|---|
| 1 | Foundation & MVP — orders, dispatch, pricing, API, tests | ✅ **done** |
| 2 | Data engineering — event ingestion, warehouse, ETL | ✅ **done** — dual Kafka outboxes (orders + drivers), warehouse ingestion with per-stream dedupe, dbt medallion marts under 46 data-quality checks, Airflow-scheduled builds |
| 3 | AI/ML — geo benchmarking, demand forecasting, ETA model | ✅ **done** — geo benchmarked with live shadow mode; demand forecasting serving surge pricing; ETA model serving from the MLflow registry with heuristic fallback; drift watch; multi-region region pack (Paris verified side-by-side) |
| 4 | Dashboard & BI — KPIs, trends, zone maps, drill-downs | ✅ **done** — Streamlit ops dashboard over the marts, per-tenant filter, live screenshot walkthrough |
| 5 | Production readiness — auth, migrations, secrets, tenancy | ✅ **done** — JWT wall on every mutating endpoint, Alembic migrations + Docker secrets, Postgres RLS multi-tenancy, load-tested (k6) and monitored (Prometheus/Grafana/Alertmanager) |
| 6 | AI copilot — agent, live chain, narrator, evals | ✅ **done** — LangGraph supervisor with RLS-scoped tools and pgvector RAG; flagship→mini fallback chain with tool+token budgets; the incident narrator on every alert; a golden-set eval harness enforced in CI and scheduled against the live provider |

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

- **[Product page](docs/product.html)** — what the platform does for a delivery operator, and the three tiers (core operations / intelligence / AI copilot) — the sales story, no code links needed
- [`docs/runbook.md`](docs/runbook.md) — recovery story (incarnation tokens), the supply-overcount monitoring note, the ETA hot-reload semantics, the compose memory budget, and the alert paging flow (Airflow narrated alerts + Alertmanager)
- [`docs/decisions/`](docs/decisions/) — decision records: multi-tenancy (0001), open-dataset evaluation (0002), hosting migration when the repo goes private (0003)

- [Architecture overview](docs/architecture.md) — system diagram and design decisions
- [API reference](docs/api.md) — endpoint catalogue (also live at `/docs`)
- [Data model](docs/data_model.md) — tables, relationships, order state machine
- [ML design](docs/ml_design.md) — forecasting and ETA modelling plan

## 📄 License

[MIT](LICENSE) © 2026 Siwar Attia

---

## 🤝 Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR — it defines the
branch/PR discipline, the verification gates (explicit exit codes, `dbt build`
before dbt merges, Postgres-path checks for worker changes), and the
live-verification expectations. Coding agents: the same file is your
briefing — read it before writing code.

*This project is an independent, from-scratch implementation inspired by common
delivery-startup system-design exercises; it reuses no code from any third-party
repository.*
