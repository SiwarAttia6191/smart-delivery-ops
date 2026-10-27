"""Sync guard: the ml container cannot import core/, so benchmark_eta.py
mirrors the heuristic's three config constants. This test fails if the two
drift apart — the champion must be the same formula everywhere."""

import re
from pathlib import Path

from core.config import Settings

ML_FILE = Path(__file__).resolve().parents[2] / "ml" / "training" / "benchmark_eta.py"


def _mirrored_constant(name: str) -> float:
    match = re.search(rf"^{name} = ([0-9.]+)", ML_FILE.read_text(encoding="utf-8"), re.M)
    assert match, f"{name} not found in {ML_FILE.name}"
    return float(match.group(1))


def test_ml_eta_constants_match_config_defaults():
    settings = Settings(database_url="sqlite://")
    assert _mirrored_constant("ETA_PREP_MINUTES") == settings.eta_restaurant_prep_minutes
    assert _mirrored_constant("ETA_SPEED_KMH") == settings.eta_avg_speed_kmh
    assert _mirrored_constant("ETA_BUFFER_MINUTES") == settings.eta_buffer_minutes


def test_ml_eta_formula_matches_eta_service():
    """The champion's rounding must match eta_service: ceil(prep + travel + buffer)."""
    import math

    settings = Settings(database_url="sqlite://")
    for km in (0.5, 2.3, 6.17, 12.8, 19.9):
        expected = math.ceil(
            settings.eta_restaurant_prep_minutes
            + (km / settings.eta_avg_speed_kmh) * 60
            + settings.eta_buffer_minutes
        )
        travel = (km / _mirrored_constant("ETA_SPEED_KMH")) * 60
        mirrored = math.ceil(
            _mirrored_constant("ETA_PREP_MINUTES")
            + travel
            + _mirrored_constant("ETA_BUFFER_MINUTES")
        )
        assert mirrored == expected, f"formula drift at {km} km"
