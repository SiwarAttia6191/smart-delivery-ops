"""#78: BUSINESS_TIMEZONE drives local business grains — DST-safe, no fixed offsets.

The DST pair is the whole point: the same UTC instant maps to different
local hours depending on the zone's rules, and a zone with DST maps the
same local hour to different UTC offsets across the year. A fixed
timedelta(hours=1) fails both assertions below for Paris; ZoneInfo passes.
"""

from datetime import UTC, datetime

from domain.services import eta_model_service


def _with_tz(monkeypatch, tz: str):
    from core.config import Settings

    settings = Settings(database_url="sqlite://", business_timezone=tz)
    monkeypatch.setattr(eta_model_service, "get_settings", lambda: settings)


def test_tunis_default_unchanged(monkeypatch):
    # Africa/Tunis is UTC+1 with no DST: hour_local = utc hour + 1, always.
    _with_tz(monkeypatch, "Africa/Tunis")
    row = eta_model_service.build_features(
        6.0, 36.8, 10.18, 36.83, 10.24, datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    )
    assert row["hour_local"] == 11
    row = eta_model_service.build_features(
        6.0, 36.8, 10.18, 36.83, 10.24, datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    )
    assert row["hour_local"] == 11  # no DST shift between seasons


def test_paris_dst_shifts_the_local_hour(monkeypatch):
    _with_tz(monkeypatch, "Europe/Paris")
    # winter: CET = UTC+1 → 10:00 UTC is 11:00 local
    winter = eta_model_service.build_features(
        6.0, 36.8, 10.18, 36.83, 10.24, datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    )
    assert winter["hour_local"] == 11
    # summer: CEST = UTC+2 → the SAME UTC instant is 12:00 local
    # (July 16 = same weekday as Jan 15, so dow matches)
    summer = eta_model_service.build_features(
        6.0, 36.8, 10.18, 36.83, 10.24, datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    )
    assert summer["hour_local"] == 12
    assert summer["dow"] == winter["dow"]


def test_forecast_local_to_utc_conversion(monkeypatch):
    # the forecast row-key conversion: naive LOCAL business hour → UTC
    import pandas as pd

    from ml.pipelines.forecast_demand import TZ_NAME, local_hours_to_utc

    assert TZ_NAME == "Africa/Tunis"  # default unchanged
    tunis_noon = pd.Timestamp(2026, 10, 15, 12, 0)
    assert local_hours_to_utc(tunis_noon).isoformat() == "2026-10-15T11:00:00+00:00"

    # Paris summer: 12:00 CEST is 10:00 UTC — tz database, not arithmetic
    import ml.pipelines.forecast_demand as fd

    monkeypatch.setattr(fd, "TZ_NAME", "Europe/Paris")
    paris_noon = pd.Timestamp(2026, 7, 15, 12, 0)
    assert local_hours_to_utc(paris_noon).isoformat() == "2026-07-15T10:00:00+00:00"
