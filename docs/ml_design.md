# ML Design (Phase 3)

The ML layer upgrades three Phase-1 heuristics into learned models — each swaps
in behind the existing service interface, so the API contract never changes.

## 1. Demand forecasting (`ml/models/demand_model.py`)

- **Task:** predict hourly order count per zone (region × hour-of-week).
- **Candidates:** Prophet (seasonality baseline) vs LightGBM (lag + calendar
  features) — benchmarked in `ml/training/train_demand_model.py`.
- **Features (`ml/features/demand_features.py`):** lagged order counts,
  hour-of-day / day-of-week encodings, zone order density, weather stubs,
  active-driver supply.
- **Serving:** the forecasting service writes `demand_forecast` rows
  (warehouse) and feeds `demand_ratio` into the pricing engine — closing the
  loop from prediction to dynamic pricing.

## 2. ETA prediction (`ml/models/eta_model.py`)

- **Task:** regress delivery minutes from distance, pickup/dropoff zones,
  hour-of-day, and driver vehicle type.
- **Baseline:** current heuristic (prep + distance/speed + buffer) is kept as
  the fallback and benchmark to beat.
- **Model:** gradient boosting with calibrated intervals; monitored for drift
  against realized delivery times.

## 3. Smart allocation (`ml/pipelines/inference_pipeline.py`)

- **Task:** upgrade nearest-driver matching to load- and efficiency-aware
  assignment (predicted demand, driver utilization history).
- **Approach:** scoring function over eligible drivers; A/B-ready behind the
  dispatch endpoint.

## Model operations

- **Registry:** `ml/models/model_registry.py` versions artifacts under
  `ml/artifacts/` with training metadata (dataset hash, metrics, params).
- **Evaluation:** `ml/training/evaluate_model.py` produces holdout metrics
  (MAE / RMSE / MAPE) per model version.
- **Monitoring:** forecast-vs-actual drift checks on the warehouse tables;
  alerting wired into the Phase 5 observability stack.
