"""ETA service — Phase 1 heuristic estimate.

ETA = restaurant prep time + travel time at an average urban speed + buffer.
Phase 3 replaces this with a trained regression model (see docs/ml_design.md).
"""

from math import ceil

from core.config import get_settings


def estimate_eta_minutes(distance_km: float) -> int:
    s = get_settings()
    travel_minutes = (distance_km / s.eta_avg_speed_kmh) * 60
    return ceil(s.eta_restaurant_prep_minutes + travel_minutes + s.eta_buffer_minutes)
