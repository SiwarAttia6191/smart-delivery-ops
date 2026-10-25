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
| 📡 **Event streaming** | Transactional-outbox relay → Kafka (`orders.events`, keyed by order id) → warehouse raw layer with primary-key dedupe — at-least-once end to end, offsets commit only after the DB commit |
| 📍 **Driver dispatch** | Geolocation-aware nearest-driver matching with vehicle filters and live availability heartbeats |
| 💸 **Dynamic pricing** | Distance-based fees with demand-surge tiers, minimum-fee floor, full price breakdown for auditability |
| ⏱️ **ETA estimation** | Prep + travel + buffer heuristic (Phase 1), upgrade path to an ML regression model |
| 📊 **Operational analytics** | KPI summary (status mix, revenue, average delivery time) + Prometheus metrics endpoint |
| 📚 **OpenAPI / Swagger** | Typed Pydantic contracts, interactive docs at `/docs` and `/redoc` out of the box |

## 🧱 Tech stack

- **API** — Python · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Swagger/OpenAPI
- **Database** — PostgreSQL 16 (Docker, default) · SQLite fallback for quick experiments
- **Streaming** — Apache Kafka (KRaft, Docker) · confluent-kafka producer · warehouse ingestion consumer
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

## 📡 Event pipeline (Phase 2)

Order lifecycle events now flow onto a Kafka bus using the **transactional
outbox** pattern — the API commits domain state and its event rows in one
database transaction (source of truth), and a relay worker copies the log onto
the topic without ever touching the API's hot path:

```
POST /orders ──► PostgreSQL (orders + order_events) ──► Kafka orders.events ──► warehouse raw layer
                     (transactional outbox)    worker    (keyed by order id)  ingest consumer
                                                                                    (dedupe by event_id)
```

Start the broker and the relay (the API comes up as before):

```bash
docker compose up -d
```

Watch the live stream — every order transition appears as a JSON event:

```bash
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:19092 --topic orders.events --from-beginning
```

Design notes:

- **Delivery guarantee: at-least-once.** Due rows are *claimed*
  (`published_at IS NULL`, `FOR UPDATE SKIP LOCKED`) and marked published only
  after the producer's delivery reports confirm the broker acknowledged every
  message — a crash anywhere before that replays the batch, and consumers
  dedupe by `event_id`. Claiming rows instead of tracking an id offset also
  removes the classic sequence-gap race (ids are assigned at INSERT but become
  visible at COMMIT) and makes concurrent relay instances safe.
- **Ordering:** messages are keyed by order id, so all events of one order land
  in the same partition, in order (`orders.events` is provisioned with 3
  partitions by the one-shot `kafka-init` service; auto-create is off).
- **The relay is restart-safe and retroactive** — it tails `order_events`
  continuously (idle poll 2 s) and treats broker/database outages as expected
  events: exponential backoff up to a 60 s cap, then resume. Topic data
  persists in the `kafkadata` volume, and full replay from the source of truth
  is always one `UPDATE relay_state SET last_relayed_event_id = 0` +
  `UPDATE order_events SET published_at = NULL` away.
- **Wire format:** versioned JSON (`schema_version: 1`) with `event_type` as a
  Kafka header.
- **Warehouse ingestion:** an `ingest` consumer (group `warehouse-ingest`)
  lands every event into `raw.order_events` in a **separate warehouse
  database** — analytics never reads or writes the operational tables. Kafka
  offsets commit only after the database commit, and the raw table's
  `event_id` primary key turns replays into no-ops: at-least-once end to end,
  zero duplicates. dbt builds its marts on this raw layer (next).
- Host tooling (kcat, local scripts) reaches the broker on `localhost:9094`;
  containers on `kafka:19092`.

## 🗂️ Project structure

```
smart-delivery-ops/
├── apps/
│   ├── api/            # FastAPI app: routes, Pydantic schemas, deps
│   ├── dashboard/      # Phase 4 — Streamlit operations dashboard
│   └── worker/         # Phase 2 — event bus consumers
├── core/               # config, database, logging, exceptions
├── domain/
│   ├── models/         # SQLAlchemy entities (Customer, Driver, Restaurant, Order, OrderEvent)
│   ├── rules/          # pure business rules (geo, pricing tiers, driver eligibility)
│   └── services/       # use-cases (order lifecycle, dispatch, pricing, ETA)
├── data/               # Phase 2 — ingestion, ETL, warehouse
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
