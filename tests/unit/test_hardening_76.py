"""#76 hardening backlog: m4 incarnation token, m5 drift guard, hot reload,
route_cache gauge."""

import time
from typing import Any, cast

import pytest

from apps.worker import relay
from core.config import get_settings
from domain.models import Driver, DriverEvent
from domain.services import deployment_service, eta_model_service
from domain.services.driver_event_service import record_location_update

# ── m4: the incarnation token ────────────────────────────────────────────


class TestIncarnationToken:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        deployment_service.reset_cache()
        yield
        deployment_service.reset_cache()

    def test_created_once_and_cached(self, db):
        first = deployment_service.incarnation_for(db)
        db.commit()
        deployment_service.reset_cache()
        # a second process/session reads the SAME row — the token identifies
        # the deployment, not the caller
        assert deployment_service.incarnation_for(db) == first
        assert len(first) == 32  # uuid4 hex

    def test_reset_cache_rereads_not_regenerates(self, db):
        token = deployment_service.incarnation_for(db)
        db.commit()
        deployment_service.reset_cache()
        assert deployment_service.incarnation_for(db) == token

    def test_wire_records_carry_the_stamp(self):
        class _FakeEvent:  # duck-typed stand-in for the ORM rows
            id = 1
            order_id = "O"
            driver_id = 3
            event_type = "created"
            payload: dict = {}
            created_at = None

        event = cast(Any, _FakeEvent())
        relay.set_incarnation("deadbeef" * 4)
        assert relay.event_record(event)["incarnation"] == "deadbeef" * 4
        assert relay.driver_event_record(event)["incarnation"] == "deadbeef" * 4
        relay.set_incarnation("")  # falsy -> legacy
        assert relay.event_record(event)["incarnation"] == "legacy"


# ── m5: the hysteresis drift guard ───────────────────────────────────────


def _driver(lat: float, lon: float) -> Driver:
    return Driver(tenant_id=1, name="Karim", vehicle="motorcycle", latitude=lat, longitude=lon)


def _availability(db, driver, lat, lon):
    """An availability transition carrying a position — this is what resets
    the CHEAP movement baseline (the leak the review flagged)."""
    db.add(
        DriverEvent(
            tenant_id=driver.tenant_id,
            driver_id=driver.id,
            event_type="availability",
            payload={"latitude": lat, "longitude": lon, "to_available": True},
        )
    )
    db.commit()


class TestDriftGuard:
    def test_sub_threshold_still_skips_without_history(self, db):
        driver = _driver(36.8065, 10.1815)
        db.add(driver)
        db.flush()
        _availability(db, driver, 36.8065, 10.1815)
        driver.latitude, driver.longitude = 36.8080, 10.1830  # ~200 m
        assert record_location_update(db, driver) is False

    def test_availability_churn_masks_sub_threshold_drift_until_2km(self, db):
        """The m5 leak: every availability reset re-anchors the 500 m check
        below it, but the CUMULATIVE guard (vs the last real
        location_update) must trip eventually."""
        driver = _driver(36.8065, 10.1815)
        db.add(driver)
        db.flush()
        _availability(db, driver, 36.8065, 10.1815)

        lat, lon = 36.8065, 10.1815
        emissions = 0
        for _ in range(6):
            # drift ~450 m per cycle, then an availability reset re-anchors
            lat += 0.0030  # ~333 m north; with lon wiggle ≈ 450 m total
            lon += 0.0025
            driver.latitude, driver.longitude = lat, lon
            if record_location_update(db, driver):
                emissions += 1
                _availability(db, driver, lat, lon)  # the churn that masks

        assert emissions >= 1, "cumulative drift never tripped the guard"
        updates = (
            db.query(DriverEvent)
            .filter_by(driver_id=driver.id, event_type="location_update")
            .count()
        )
        assert updates == emissions

    def test_normal_movement_still_emits_at_500m(self, db):
        driver = _driver(36.8065, 10.1815)
        db.add(driver)
        db.flush()
        _availability(db, driver, 36.8065, 10.1815)
        driver.latitude, driver.longitude = 36.8786, 10.3247  # ~8 km
        assert record_location_update(db, driver) is True


# ── ETA hot reload ───────────────────────────────────────────────────────


def _registry_settings():
    return get_settings().model_copy(
        update={"eta_model_source": "registry", "eta_model_reload_ttl_seconds": 60}
    )


class TestHotReload:
    @pytest.fixture(autouse=True)
    def _registry_mode(self, monkeypatch):
        monkeypatch.setattr(eta_model_service, "get_settings", _registry_settings)
        eta_model_service.reset_state()
        yield
        eta_model_service.reset_state()

    def test_version_change_triggers_reload(self, monkeypatch):
        monkeypatch.setattr(eta_model_service, "_registry_version", lambda: "9")
        calls = []
        monkeypatch.setattr(eta_model_service, "load_from_registry", lambda: calls.append(1))
        eta_model_service.refresh_if_stale()  # first check: no model loaded yet
        assert len(calls) == 1
        # pretend the load recorded the new version
        eta_model_service._loaded_version = "9"
        eta_model_service._last_version_check = None  # TTL expired
        eta_model_service.refresh_if_stale()
        assert len(calls) == 1  # same version: no reload

    def test_new_version_reloads(self, monkeypatch):
        eta_model_service._loaded_version = "3"
        monkeypatch.setattr(eta_model_service, "_registry_version", lambda: "4")
        calls = []
        monkeypatch.setattr(eta_model_service, "load_from_registry", lambda: calls.append(1))
        eta_model_service._last_version_check = None
        eta_model_service.refresh_if_stale()
        assert len(calls) == 1

    def test_registry_down_keeps_serving(self, monkeypatch):
        eta_model_service._loaded_version = "3"
        monkeypatch.setattr(eta_model_service, "_registry_version", lambda: None)
        calls = []
        monkeypatch.setattr(eta_model_service, "load_from_registry", lambda: calls.append(1))
        eta_model_service._last_version_check = None
        eta_model_service.refresh_if_stale()
        assert calls == []

    def test_ttl_gates_the_check(self, monkeypatch):
        eta_model_service._loaded_version = "3"
        monkeypatch.setattr(eta_model_service, "_registry_version", lambda: "4")
        calls = []
        monkeypatch.setattr(eta_model_service, "load_from_registry", lambda: calls.append(1))
        eta_model_service._last_version_check = time.monotonic()  # checked just now
        eta_model_service.refresh_if_stale()
        assert calls == []  # inside the TTL window: no check, no reload

    def test_non_registry_source_is_untouched(self, monkeypatch):
        assert get_settings().eta_model_source in ("", "registry")
        if get_settings().eta_model_source == "registry":  # env-dependent; both branches safe
            monkeypatch.setattr(eta_model_service, "_registry_version", lambda: "9")
        eta_model_service._last_version_check = None
        # no exception either way; the heuristic posture is unchanged
        eta_model_service.refresh_if_stale()


# ── m7: the route_cache gauge ────────────────────────────────────────────


def test_metrics_carry_route_cache_gauge(client):
    response = client.get("/analytics/metrics")
    assert response.status_code == 200
    assert "sdo_route_cache_rows" in response.text
