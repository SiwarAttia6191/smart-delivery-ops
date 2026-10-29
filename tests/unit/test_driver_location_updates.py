"""#60: hysteresis location_update events + the stale-driver sweep."""

from datetime import UTC, datetime, timedelta

from domain.models import Driver, DriverEvent
from domain.services.driver_event_service import (
    record_location_update,
    sweep_stale_drivers,
)


def _driver(lat: float, lon: float) -> Driver:
    return Driver(tenant_id=1, name="Karim", vehicle="motorcycle", latitude=lat, longitude=lon)


def _positions(session, driver_id: int) -> list[tuple]:
    return [
        (e.payload["latitude"], e.payload["longitude"])
        for e in session.query(DriverEvent)
        .filter_by(driver_id=driver_id, event_type="location_update")
        .order_by(DriverEvent.id)
    ]


def _seed_driver_with_event(db, lat: float, lon: float) -> Driver:
    """Driver + one availability event (the 'registered' position anchor)."""
    driver = _driver(lat, lon)
    db.add(driver)
    db.flush()
    db.add(
        DriverEvent(
            tenant_id=1,
            driver_id=driver.id,
            event_type="availability",
            payload={
                "from_available": None,
                "to_available": True,
                "latitude": lat,
                "longitude": lon,
            },
        )
    )
    db.commit()
    return driver


def test_small_move_does_not_emit(db):
    driver = _seed_driver_with_event(db, 36.8065, 10.1815)
    driver.latitude, driver.longitude = 36.8080, 10.1830  # ~200 m
    assert record_location_update(db, driver) is False
    assert _positions(db, driver.id) == []


def test_large_move_emits_event(db):
    driver = _seed_driver_with_event(db, 36.8065, 10.1815)
    driver.latitude, driver.longitude = 36.8786, 10.3247  # ~8 km, La Marsa
    assert record_location_update(db, driver) is True
    assert _positions(db, driver.id) == [(36.8786, 10.3247)]
    event = db.query(DriverEvent).filter_by(driver_id=driver.id, event_type="location_update").one()
    assert event.payload["available"] is True


def test_repeated_pings_emit_only_when_moving_again(db):
    driver = _seed_driver_with_event(db, 36.8065, 10.1815)
    driver.latitude, driver.longitude = 36.8786, 10.3247
    assert record_location_update(db, driver) is True
    # same spot again: no event even across many heartbeats
    for _ in range(3):
        assert record_location_update(db, driver) is False
    # another large move: emits, anchored at the NEW last-evented position
    driver.latitude, driver.longitude = 36.8065, 10.1815
    assert record_location_update(db, driver) is True
    assert len(_positions(db, driver.id)) == 2


def test_offline_move_carries_available_false(db):
    driver = _seed_driver_with_event(db, 36.8065, 10.1815)
    driver.is_available = False
    driver.latitude, driver.longitude = 36.8786, 10.3247
    assert record_location_update(db, driver) is True
    event = db.query(DriverEvent).filter_by(driver_id=driver.id, event_type="location_update").one()
    assert event.payload["available"] is False


def test_sweep_forces_silent_driver_offline(db):
    driver = _seed_driver_with_event(db, 36.8065, 10.1815)
    db.query(DriverEvent).update({"created_at": datetime.now(UTC) - timedelta(hours=5)})
    db.commit()

    swept = sweep_stale_drivers(db)

    assert swept == 1
    db.refresh(driver)
    assert driver.is_available is False
    event = (
        db.query(DriverEvent).filter_by(driver_id=driver.id).order_by(DriverEvent.id.desc()).first()
    )
    assert event.event_type == "availability"
    assert event.payload["to_available"] is False
    assert event.payload["reason"] == "stale_sweep"


def test_sweep_spares_fresh_and_offline_drivers(db):
    fresh = _seed_driver_with_event(db, 36.8065, 10.1815)  # recent event
    offline = _seed_driver_with_event(db, 36.8070, 10.1820)
    offline.is_available = False
    db.commit()

    assert sweep_stale_drivers(db) == 0
    db.refresh(fresh)
    db.refresh(offline)
    assert fresh.is_available is True
    assert offline.is_available is False
