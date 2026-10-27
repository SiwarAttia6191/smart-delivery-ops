"""Model-backed ETA serving — the registered challenger, on the quote path.

Loads `eta_model` from the MLflow model registry at startup when
`ETA_MODEL_SOURCE=registry` (the stage the #26 benchmark promoted it to) and
answers order-creation ETAs with it. Feature order and zone rounding MUST
match the benchmark's training features (ml/training/benchmark_eta.py).

Serving rules (same discipline as the distance shadow provider):
- ML being down must never break quoting: a failed load or failed prediction
  means the heuristic answers instead, loudly logged, gauge at 0.
- The heuristic stays the champion in code (`eta_service`); this module only
  overrides it when a model is actually loaded.

Africa/Tunis is fixed UTC+1 (no DST) — the same offset the forecast job and
the warehouse staging models bake in.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from core.config import get_settings

logger = logging.getLogger(__name__)

TUNIS_UTC_OFFSET = timedelta(hours=1)

# Module state: None model = heuristic fallback. `loaded` drives the gauge.
_model: Any | None = None
_loaded: bool = False
_load_error: str | None = None


def is_serving() -> bool:
    return _loaded


def load_from_registry() -> None:
    """Load the promoted model once at startup. Never raises."""
    global _model, _loaded, _load_error
    settings = get_settings()
    if settings.eta_model_source != "registry":
        return
    try:
        import mlflow

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        uri = f"models:/{settings.eta_model_name}/{settings.eta_model_stage}"
        _model = mlflow.lightgbm.load_model(uri)
        _loaded = True
        logger.info("ETA model loaded from registry: %s", uri)
    except Exception as exc:  # noqa: BLE001 - serving must survive registry outages
        _model, _loaded, _load_error = None, False, str(exc)[:300]
        logger.error("ETA model unavailable (%s); serving the heuristic fallback", _load_error)


def reset_state() -> None:
    """Test hook: clear the loaded model between tests."""
    global _model, _loaded, _load_error
    _model, _loaded, _load_error = None, False, None


def build_features(
    distance_km: float,
    pickup_lat: float,
    pickup_lon: float,
    dropoff_lat: float,
    dropoff_lon: float,
    now: datetime,
) -> dict[str, float]:
    """Feature row exactly as the benchmark's FEATURES define it. Zone cells
    round to 2 decimals — the same cells the warehouse marts grain on."""
    local = (now if now.tzinfo else now.replace(tzinfo=UTC)) + TUNIS_UTC_OFFSET
    return {
        "distance_km": distance_km,
        "hour_local": local.hour,
        "dow": local.weekday(),
        "pickup_zone": round(pickup_lat, 2),
        "dropoff_zone": round(dropoff_lat, 2),
        "dropoff_zone_lon": round(dropoff_lon, 2),
    }


def estimate_eta_minutes(
    distance_km: float,
    pickup_lat: float,
    pickup_lon: float,
    dropoff_lat: float,
    dropoff_lon: float,
    now: datetime | None = None,
) -> int | None:
    """Model-backed ETA; None means 'fall back to the heuristic'."""
    if not _loaded or _model is None:
        return None
    now = now or datetime.now(UTC)
    features = build_features(distance_km, pickup_lat, pickup_lon, dropoff_lat, dropoff_lon, now)
    try:
        import pandas as pd

        prediction = _model.predict(pd.DataFrame([features]))
        return max(1, int(round(float(prediction[0]))))
    except Exception as exc:  # noqa: BLE001 - a broken model falls back, loudly
        logger.error("ETA model prediction failed (%s); serving the heuristic", exc)
        return None
