# ML Design (Phase 3 — implemented)

The ML layer upgrades two Phase-1 heuristics into learned models. Each one is
**benchmarked challenger-vs-champion before it ships**: a model earns its
place by beating the incumbent on a time-based holdout, and the verdict is
recorded in the MLflow model registry. Implementation lives in `ml/` (see
`ml/README.md` for the run commands); this document records the design
decisions behind it.

## 1. Demand forecasting (SHIPPED — `ml/training/benchmark_demand.py`, `ml/pipelines/forecast_demand.py`)

- **Task:** predict hourly order counts per dropoff zone (~1 km cells) for
  the next 24 hours.
- **Champion (the bar):** seasonal-naive (hour h = hour h−24h). Challengers:
  LightGBM (lag-1/2/24 + hour + day-of-week) and Prophet (trend + daily
  seasonality, ≥48 h history required — refuses honestly on less).
- **Result:** both challengers beat the baseline (LightGBM 1.16 / Prophet
  1.15 vs 2.39 MAE on the first holdout); reports in `data/benchmarks/`.
- **Serving:** a **pooled** LightGBM (one model across all zone cells —
  lag + zone coordinates + calendar features; per-zone univariate fits are
  hopeless at ~27 sparse cells) writes the next 24h of zone-hour forecasts
  into the **ops database** (`demand_forecasts` — the API has no warehouse
  connection). The window anchors at wall-clock now and the table is
  pruned to a per-run snapshot.
- **The loop:** the API derives `demand_ratio` per quote from the forecast
  table (observed ÷ expected-so-far, floored 0.5 / capped 2.5, neutral 1.0
  on missing data) and the existing surge tiers do the rest.
- **Deliberately deferred:** weather stubs and active-driver-supply
  covariates (the supply mart now exists — a future challenger can add it).

## 2. ETA prediction (SHIPPED — `ml/training/benchmark_eta.py`, `domain/services/eta_model_service.py`)

- **Task:** regress the realized delivery span (created → delivered) from
  distance, Africa/Tunis local hour, day-of-week, and pickup/dropoff zone
  cells. The realized span includes assignment + pickup wait — the heuristic
  structurally cannot know those, which is why it lost.
- **Champion:** the Phase 1 heuristic (prep + travel + buffer, ceil) —
  kept as the serving fallback for every failure mode.
- **Challengers:** OSRM free-flow duration + prep + buffer (6.43 MAE — the
  road graph "knows" the city), and LightGBM (**5.13 MAE**, the winner,
  registered as `eta_model` Production in the MLflow registry).
- **Serving:** the API loads the registered **native LightGBM booster**
  (`model.lgb` — deliberately not a pickle) at startup behind
  `ETA_MODEL_SOURCE=registry`; any failure falls back to the heuristic with
  `sdo_eta_model_serving 0` on the metrics endpoint.
- **Drift baseline:** every benchmark/serving run logs input distributions
  (distance/hour mean + std) and prediction-residual stats — the per-run
  baseline drift monitoring compares against.

## 3. Smart allocation (NOT BUILT — next up)

- **Task:** upgrade nearest-driver matching to load- and efficiency-aware
  assignment, using the demand and supply marts that Phase 3 now produces
  (`mart_demand_supply_by_zone_hour.drivers_per_order`) plus the demand
  forecasts.
- Same discipline: challenger-vs-champion on dispatch quality, shadow
  evaluation before any switch.

## Model operations

- **Registry:** the MLflow model registry (Postgres-backed server,
  `localhost:5100` in the tools profile) — versions live as registry model
  versions with the dataset snapshot in their metadata; stages record the
  harness verdict (Production only after beating the champion).
- **Evaluation:** committed markdown reports per benchmark run under
  `data/benchmarks/` (MAE / RMSE overall, per distance bucket, per local
  hour block), every run tagged with `max(event_id)` + git SHA.
- **Monitoring:** input-distribution and residual baselines logged per run
  (the drift-monitoring baseline); alerting wires into the Phase 5
  observability stack.

## 4. The AI copilot (SHIPPED — `apps/copilot/`, `dags/copilot_evals.py`)

Beyond the numeric models sits the LLM layer: a LangGraph agent over the
governed API (pgvector RAG, guardrails, budgets, fallback chain), an incident
narrator on the alert path, and a golden-set eval harness enforced in CI. Its
"model operations" story is MLflow-native: one traced run per conversation and
one scored run per eval batch. Design + backlog: [ai.md](ai.md).
