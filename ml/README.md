# ML / AI Layer (Phase 3)

The ML layer runs as **one-shot batch containers** (`ml-bench`, built from
`infra/docker/Dockerfile.ml`) — training and serving-side inference are
jobs, never long-running services. Everything is benchmarked
challenger-vs-champion and tracked in **MLflow** (`localhost:5100` when the
tools profile is up, backed by Postgres).

```
ml/
├── training/
│   ├── benchmark_demand.py   # seasonal-naive vs LightGBM vs Prophet (demand)
│   └── benchmark_eta.py      # heuristic vs OSRM free-flow vs LightGBM (ETA)
├── pipelines/
│   └── forecast_demand.py    # serving job: zone-hour forecasts → ops DB
└── artifacts/                # gitignored (registry artifacts live in the ml-artifacts volume)
```

## The two benchmarks (challenger-vs-champion)

| | demand forecasting | ETA |
|---|---|---|
| ground truth | warehouse hourly order counts | our realized deliveries (created → delivered) |
| champion (the bar) | seasonal-naive | the 20 km/h heuristic (`eta_service`) |
| challengers | LightGBM, Prophet | OSRM free-flow + prep + buffer, LightGBM |
| split | time-based, newest 20% holdout | time-based, newest 20% holdout |
| run | `make ml-benchmark` | `make eta-benchmark` |
| promotion | beats the champion on the holdout → registered in the MLflow model registry (Production) | same |

Every run logs params/metrics + the dataset snapshot (`max(event_id)` + git
SHA) to MLflow; the markdown report is committed under `data/benchmarks/`.
**A challenger ships only if it beats the incumbent** — the registry stage
records the verdict.

## Serving (the last hop)

- **Demand** (`make ml-forecast`): pooled LightGBM (one model across all
  zone cells — lag + zone + calendar features) writes the next 24h of
  zone-hour forecasts into the ops DB's `demand_forecasts`. The table is a
  **snapshot of the current window** (pruned per run) and must be refreshed
  regularly — the API treats a missing/stale hour as neutral surge, never
  as a spike. The API derives `demand_ratio` live from it
  (`domain/services/demand_service.py`).
- **ETA** (`ETA_MODEL_SOURCE=registry`, set in compose): the API loads
  `models:/eta_model/Production` at startup — the **native LightGBM
  booster** (`model.lgb`), deliberately not a pickle — and answers order
  ETAs. Any failure (registry down, load error, prediction exception) falls
  back to the heuristic, loudly logged; `sdo_eta_model_serving` on
  `/analytics/metrics` says which one is answering.

## Gotchas baked in (learned the hard way)

- mlflow 2.19's backend needs **psycopg2** (psycopg3 breaks it) while our
  own code uses psycopg3 — both drivers, one image.
- sqlalchemy ≥2.1 removed a symbol mlflow 2.19 imports → the ml image pins
  `<2.1`.
- `mlflow db upgrade` fails on a **fresh** database → the compose entrypoint
  only upgrades when `alembic_version` exists; the server initializes fresh
  DBs itself.
- Local business hours come from **`BUSINESS_TIMEZONE`** (IANA name) through
  the tz database — never fixed offsets (both off-by-one directions have
  bitten: parse already-local timestamps as local; convert via
  `tz_localize(tz).tz_convert("UTC")`, not arithmetic).
