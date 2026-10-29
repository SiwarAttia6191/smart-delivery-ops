"""Unit tests for geo distance and driver matching."""

from domain.models import Driver
from domain.rules import geo
from domain.services import driver_matching_service

# Tunis city centre reference point
TUNIS_LAT, TUNIS_LON = 36.8065, 10.1815


class TestHaversine:
    def test_zero_distance(self):
        assert geo.haversine_km(TUNIS_LAT, TUNIS_LON, TUNIS_LAT, TUNIS_LON) == 0.0

    def test_known_distance_tunis_to_la_marsa(self):
        # La Marsa is roughly 13–15 km north-east of central Tunis by air.
        d = geo.haversine_km(36.8065, 10.1815, 36.8786, 10.3247)
        assert 13.0 < d < 16.0

    def test_symmetry(self):
        a = geo.haversine_km(36.8, 10.1, 36.9, 10.2)
        b = geo.haversine_km(36.9, 10.2, 36.8, 10.1)
        assert a == b


class TestDriverMatching:
    def test_returns_nearest_eligible_driver(self, db):
        near = Driver(
            tenant_id=1, name="Near", vehicle="motorcycle", latitude=36.8100, longitude=10.1900
        )
        far = Driver(
            tenant_id=1, name="Far", vehicle="motorcycle", latitude=36.9500, longitude=10.3000
        )
        db.add_all([near, far])
        db.commit()

        match = driver_matching_service.find_nearest_driver(db, TUNIS_LAT, TUNIS_LON)
        assert match is not None
        assert match.driver.name == "Near"
        assert match.distance_km < 2.0

    def test_skips_unavailable_drivers(self, db):
        busy = Driver(
            tenant_id=1, name="Busy", is_available=False, latitude=36.8070, longitude=10.1820
        )
        free = Driver(
            tenant_id=1, name="Free", is_available=True, latitude=36.8500, longitude=10.2500
        )
        db.add_all([busy, free])
        db.commit()

        match = driver_matching_service.find_nearest_driver(db, TUNIS_LAT, TUNIS_LON)
        assert match is not None
        assert match.driver.name == "Free"

    def test_vehicle_filter(self, db):
        bike = Driver(
            tenant_id=1, name="Bike", vehicle="bicycle", latitude=36.8070, longitude=10.1820
        )
        car = Driver(tenant_id=1, name="Car", vehicle="car", latitude=36.9000, longitude=10.3000)
        db.add_all([bike, car])
        db.commit()

        match = driver_matching_service.find_nearest_driver(db, TUNIS_LAT, TUNIS_LON, vehicle="car")
        assert match is not None
        assert match.driver.name == "Car"

    def test_no_eligible_driver_returns_none(self, db):
        db.add(Driver(tenant_id=1, name="Solo", is_available=False, latitude=36.8, longitude=10.1))
        db.commit()
        assert driver_matching_service.find_nearest_driver(db, 36.8, 10.1) is None
