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

the configured business timezone (BUSINESS_TIMEZONE, #78) — resolved through
the tz database (zoneinfo), never a fixed offset: DST-safe by construction.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.config import get_settings

logger = logging.getLogger(__name__)

# Module state: None model = heuristic fallback. `loaded` drives the gauge.
_model: Any | None = None
_loaded: bool = False
_load_error: str | None = None


def is_serving() -> bool:
    return _loaded


def load_from_registry() -> None:
    """Load the promoted model once at startup. Never raises.

    We deliberately load the NATIVE LightGBM booster from the logged
    artifact instead of mlflow's pyfunc wrapper: the wrapper broke in the
    serving image ('super' object has no attribute 'get_params'), and the
    booster is the actual artifact the benchmark logged. Registry stage
    resolution + artifact download go through the mlflow client."""
    global _model, _loaded, _load_error
    settings = get_settings()
    if settings.eta_model_source != "registry":
        return
    try:
        import lightgbm as lgb
        import mlflow
        from mlflow.tracking import MlflowClient

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        client = MlflowClient()
        versions = client.get_latest_versions(
            settings.eta_model_name, stages=[settings.eta_model_stage]
        )
        source = versions[0].source
        local_dir = mlflow.artifacts.download_artifacts(
            artifact_uri=source,
            tracking_uri=settings.mlflow_tracking_uri,
        )
        artifact_dir = Path(local_dir)
        booster_file = artifact_dir / "model.lgb"  # mlflow ≥2.14 native booster name
        if not booster_file.exists():
            booster_file = artifact_dir / "model.txt"  # older native name
        _model = lgb.Booster(model_file=str(booster_file))
        _loaded = True
        logger.info(
            "ETA model loaded from registry: %s (version %s)",
            source,
            versions[0].version,
        )
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
    tz = ZoneInfo(get_settings().business_timezone)
    local = (now if now.tzinfo else now.replace(tzinfo=UTC)).astimezone(tz)
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
