"""Order priority tests (#276): urgent deliveries bias dispatch.

The guarantees: the priority rides the order and its created event
(only urgent is recorded — normal is the noise-free default), the
dispatch bias flips a real near-tie (an urgent order takes the FASTER
vehicle slightly farther over the slower vehicle slightly closer, while
a normal order still picks the closest), and the priority lands in the
order response for the portal to render.
"""

from fastapi.testclient import TestClient

CUSTOMER = {"name": "Aziza", "phone": "+216 20 000 000", "latitude": 36.8381, "longitude": 10.2385}
RESTAURANT = {"name": "Chez Slah", "latitude": 36.8065, "longitude": 10.1815}
# the near-tie pair: the bicycle is CLOSER, the motorcycle slightly FARTHER
BIKE = {"name": "Bike Samir", "vehicle": "bicycle", "latitude": 36.8070, "longitude": 10.1820}
MOTO = {"name": "Moto Karim", "vehicle": "motorcycle", "latitude": 36.8060, "longitude": 10.1810}


def _place(client: TestClient, restaurant_id: int, customer_id: int, **over) -> dict:
    payload = {
        "customer_id": customer_id,
        "restaurant_id": restaurant_id,
        "dropoff_latitude": CUSTOMER["latitude"],
        "dropoff_longitude": CUSTOMER["longitude"],
        "demand_ratio": 0,
    }
    payload.update(over)
    order = client.post("/orders", json=payload)
    assert order.status_code == 201, order.text
    return order.json()


def test_priority_rides_order_and_event(authed_client: TestClient, db):
    from domain.models import OrderEvent

    customer = authed_client.post("/customers", json=CUSTOMER).json()
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    order = _place(authed_client, restaurant["id"], customer["id"], priority="urgent")

    assert order["priority"] == "urgent"
    event = db.query(OrderEvent).filter_by(order_id=order["id"], event_type="created").one()
    assert event.payload["priority"] == "urgent"

    # a normal order records NO priority key — null keys are warehouse noise
    normal = _place(authed_client, restaurant["id"], customer["id"])
    normal_event = db.query(OrderEvent).filter_by(order_id=normal["id"], event_type="created").one()
    assert "priority" not in normal_event.payload


def test_urgent_dispatch_favors_the_faster_vehicle(authed_client: TestClient):
    customer = authed_client.post("/customers", json=CUSTOMER).json()
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    bike = authed_client.post("/drivers", json=BIKE).json()
    moto = authed_client.post("/drivers", json=MOTO).json()

    # NORMAL order: the closest driver wins — the bicycle, despite being slower
    normal = _place(authed_client, restaurant["id"], customer["id"])
    assigned = authed_client.post(f"/orders/{normal['id']}/assign").json()
    assert assigned["driver_id"] == bike["id"], "normal order takes the closest"

    # free both drivers for the second scenario
    authed_client.post(f"/orders/{normal['id']}/status", json={"status": "cancelled"})

    # URGENT order: the speed factor flips the near-tie — the motorcycle wins
    # even though it is slightly farther
    urgent = _place(authed_client, restaurant["id"], customer["id"], priority="urgent")
    assigned = authed_client.post(f"/orders/{urgent['id']}/assign").json()
    assert assigned["driver_id"] == moto["id"], "urgent order takes the faster vehicle"

    authed_client.post(f"/orders/{urgent['id']}/status", json={"status": "cancelled"})

    # and the next NORMAL order goes back to the closest driver — the bias
    # engaged only for the urgent order, never leaking into normal ones
    third = _place(authed_client, restaurant["id"], customer["id"])
    assigned = authed_client.post(f"/orders/{third['id']}/assign").json()
    assert assigned["driver_id"] == bike["id"]  # closest wins again for normal


def test_normal_order_ignores_the_bias(authed_client: TestClient):
    """The control: without urgent, the closest driver wins even when the
    faster vehicle is the alternative — the bias engages ONLY for urgent."""
    customer = authed_client.post("/customers", json=CUSTOMER).json()
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    bike = authed_client.post("/drivers", json=BIKE).json()
    authed_client.post("/drivers", json=MOTO)

    order = _place(authed_client, restaurant["id"], customer["id"])
    assigned = authed_client.post(f"/orders/{order['id']}/assign").json()
    assert assigned["driver_id"] == bike["id"]
