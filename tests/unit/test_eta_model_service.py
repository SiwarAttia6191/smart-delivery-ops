"""eta_model_service — the registry-served ETA with heuristic fallback (#69)."""

from datetime import UTC, datetime

from domain.services import eta_model_service, eta_service


class FakeModel:
    def __init__(self, value: float = 25.4):
        self.value = value
        self.fail = False

    def predict(self, frame):
        if self.fail:
            raise RuntimeError("model exploded")
        return [self.value]


def test_build_features_matches_benchmark_convention():
    # 12:45 UTC → 13:45 Africa/Tunis (fixed UTC+1); zones round to 2 decimals.
    now = datetime(2026, 10, 27, 12, 45, tzinfo=UTC)  # a Tuesday
    row = eta_model_service.build_features(6.17, 36.806522, 10.181621, 36.838100, 10.238500, now)
    assert row == {
        "distance_km": 6.17,
        "hour_local": 13,
        "dow": 1,
        "pickup_zone": 36.81,
        "dropoff_zone": 36.84,
        "dropoff_zone_lon": 10.24,
    }


def test_not_loaded_returns_none():
    eta_model_service.reset_state()
    assert eta_model_service.estimate_eta_minutes(6.0, 36.8, 10.18, 36.83, 10.24) is None
    assert eta_model_service.is_serving() is False


def test_prediction_failure_falls_back_to_none():
    eta_model_service.reset_state()
    eta_model_service._model = FakeModel()
    eta_model_service._model.fail = True
    eta_model_service._loaded = True
    assert eta_model_service.estimate_eta_minutes(6.0, 36.8, 10.18, 36.83, 10.24) is None


def test_loaded_model_returns_rounded_eta():
    eta_model_service.reset_state()
    eta_model_service._model = FakeModel(25.4)
    eta_model_service._loaded = True
    try:
        got = eta_model_service.estimate_eta_minutes(6.0, 36.8, 10.18, 36.83, 10.24)
        assert got == 25
    finally:
        eta_model_service.reset_state()


def test_heuristic_champion_unchanged():
    # The fallback path must be byte-identical to the Phase 1 champion.
    assert eta_service.estimate_eta_minutes(6.17) == 36
