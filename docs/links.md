# The surface directory — every link, how to reach it

Every clickable surface the platform serves, verified against the compose
stacks. Two deployments run side by side locally: the **main stack** (port
**8010**, Tunis seed data, all the platform tooling) and the **demo unit**
(port **8011**, the lightweight customer-facing trio that mirrors the
hosted VPS deployment — see
[decision record 0004](decisions/0004-live-public-demo.md)). One
deployment = one city ([region packs](architecture.md) drive the seed, the
map and the routing graph).

## Main stack — the clickable surfaces

All of these are **served by the API itself** on `http://localhost:8010` —
no separate web server (`make api` or `docker compose up -d api`):

| Surface | URL | Sign-in |
|---|---|---|
| Demos hub (the index) | `/storefront/demos.html` | — |
| Customer storefront (quote → coupon → order → pay → track) | `/storefront/ordering-demo.html` | `ops/ops` to order |
| Restaurant portal (menu CRUD, live incoming orders) | `/storefront/restaurant-portal.html` | `ops/ops` |
| Onboarding wizard (map preview → import → staff → checklist) | `/storefront/onboarding-wizard.html` | `ops/ops` |
| Swagger UI (every endpoint, click-to-try) | `/docs` | token for writes |
| ReDoc (the same contract, reading view) | `/redoc` | — |
| Streamlit dashboard (KPIs) | `http://localhost:8501` | in-page |
| Dashboard → Copilot chat | `http://localhost:8501/Copilot` | `ops/ops` |

Demo credentials are the documented dev trio — `admin/admin`, `ops/ops`,
`view/view` (a viewer is correctly rejected on every write). Dev-only, as
the banners say.

## Main stack — the platform tooling (tools profile)

`docker compose --profile tools up -d`:

| Tool | URL | Sign-in | What lives there |
|---|---|---|---|
| MLflow | `http://localhost:5100` | — | every benchmark run, compare-runs, report artifacts |
| Airflow | `http://localhost:8081` | `admin/admin` | the DAGs: warehouse build, forecast serving, retrain+benchmark, knowledge sync, copilot evals |
| Grafana | `http://localhost:3000` | `admin/admin` | the operational dashboards |
| Prometheus | `http://localhost:9090` | — | the metrics browser |
| Alertmanager | `http://localhost:9093` | — | alert routing |

Data planes: Postgres on `localhost:5434` (ops) — the warehouse DB runs in
the same container; Kafka on `localhost:9094`.

## Demo unit — the side-by-side (`docker-compose.demo.yml`)

```bash
CITY=madrid OSRM_EXTRACT_ID=madrid OSRM_DATA_DIR=./data/osrm/madrid \
DEMO_API_PORT=8011 DEMO_PG_PORT=5435 \
docker compose -p madrid-demo -f docker-compose.demo.yml up -d --build
```

The unit runs exactly what the customer touches — postgres, api, osrm, the
migrate one-shot — sized for a ~2 GB box. No Airflow, MLflow, Kafka, dbt,
Grafana or Prometheus by design; demand forecasts are seeded as rows so
live surge derivation works without the ML pipeline.

| Surface | URL |
|---|---|
| Onboarding wizard | `http://localhost:8011/storefront/onboarding-wizard.html` |
| Customer storefront | `http://localhost:8011/storefront/ordering-demo.html` |
| Restaurant portal | `http://localhost:8011/storefront/restaurant-portal.html` |
| Swagger UI | `http://localhost:8011/docs` |

Stop it with
`docker compose -p madrid-demo -f docker-compose.demo.yml down`. The port
shift is what lets both deployments run side by side.

## Endpoint quick reference

The full contract lives in [`api.md`](api.md) and live at `/docs`; the
ones a demo usually touches:

| Endpoint | What it does |
|---|---|
| `GET /health` | liveness + database check |
| `GET /config` | the deployment's city manifest (label, timezone, map view) |
| `POST /pricing/quote` | fee breakdown — accepts a typed address OR coordinates |
| `POST /orders` | create an order (accepts `dropoff_address`, coupons, channel `source`) |
| `POST /orders/{id}/pay` | settle the fee (sandbox `sbx_` or Stripe test-mode `pi_…`) |
| `POST /import/{entity}` (+ `/preview`) | bulk import; the preview geocodes with nothing committed |
| `GET /import/{entity}/template` | a one-row CSV template per entity |
| `POST /onboarding/staff` · `GET /onboarding/readiness` | staff issuance · the readiness checklist |
| `POST /copilot/chat` (+ `/chat/stream`) | the copilot (SSE streaming) |
| `GET /analytics/summary` · `/metrics` | KPIs · Prometheus metrics |

## Keeping this file honest

This directory is part of the repository's standing representation set
(see [AGENTS.md](../AGENTS.md) rule 8): when a round adds, renames or
re-ports a surface, this file updates in the same commit as the work.
