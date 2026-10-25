"""End-to-end smoke test against a running API.

Start the API first (make api), then:
    python scripts/smoke_test.py
    BASE_URL=http://localhost:8010 python scripts/smoke_test.py
"""

import os
import sys
import uuid

import httpx

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8010")


def main() -> int:
    run_id = uuid.uuid4().hex[:6]
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        health = c.get("/health").json()
        assert health["database"] == "up", f"database down: {health}"
        print(f"[ok] health — {health['app']} v{health['version']}")

        customer = c.post(
            "/customers",
            json={"name": f"Smoke {run_id}", "latitude": 36.8381, "longitude": 10.2385},
        ).json()
        restaurant = c.post(
            "/restaurants",
            json={"name": f"Smoke Kitchen {run_id}", "latitude": 36.8065, "longitude": 10.1815},
        ).json()
        driver = c.post(
            "/drivers",
            json={
                "name": f"Smoke Rider {run_id}",
                "vehicle": "motorcycle",
                "latitude": 36.8100,
                "longitude": 10.1900,
            },
        ).json()
        print(
            f"[ok] seeded customer={customer['id']} restaurant={restaurant['id']} driver={driver['id']}"
        )

        quote = c.post(
            "/pricing/quote",
            json={
                "restaurant_id": restaurant["id"],
                "dropoff_latitude": 36.8381,
                "dropoff_longitude": 10.2385,
                "demand_ratio": 1.3,
            },
        ).json()
        print(
            f"[ok] quote — {quote['distance_km']} km, fee {quote['total']} (surge x{quote['surge_multiplier']})"
        )

        order = c.post(
            "/orders",
            json={
                "customer_id": customer["id"],
                "restaurant_id": restaurant["id"],
                "dropoff_latitude": 36.8381,
                "dropoff_longitude": 10.2385,
                "demand_ratio": 1.3,
            },
        ).json()
        assert order["status"] == "pending"
        print(
            f"[ok] order {order['id']} pending — fee {order['delivery_fee']}, eta {order['eta_minutes']} min"
        )

        order = c.post(f"/orders/{order['id']}/assign").json()
        assert order["status"] == "assigned"
        print(f"[ok] assigned driver {order['driver_id']}")

        for status in ("in_transit", "delivered"):
            order = c.post(f"/orders/{order['id']}/status", json={"status": status}).json()
        assert order["status"] == "delivered"
        print(f"[ok] delivered in {order['delivery_minutes']} min (simulated)")

        summary = c.get("/analytics/summary").json()
        print(
            f"[ok] analytics — {summary['orders_total']} orders, revenue {summary['delivered_revenue']}"
        )

        metrics = c.get("/analytics/metrics").text
        assert "sdo_orders_total" in metrics
        print("[ok] prometheus metrics served")

    print("\nSmoke test passed ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
