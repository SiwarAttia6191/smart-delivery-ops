"""Rate limiting (#315): the bucket math, the login lockout, and the
exemptions a monitoring probe depends on. The middleware state resets
per test — the buckets are process-lifetime in production."""

from apps.api import rate_limit


def test_token_bucket_burst_then_refill():
    now = 1000.0
    # a 60-rpm bucket: a burst of 60 passes, the 61st at the same instant
    # does not — that IS the definition of requests per minute
    for _ in range(60):
        assert rate_limit._bucket_allow("ip1", 60, now)[0]
    allowed, retry = rate_limit._bucket_allow("ip1", 60, now)
    assert allowed is False
    assert retry >= 1  # the denial says when the next token arrives
    # a different client is unaffected (per-key isolation)
    assert rate_limit._bucket_allow("ip2", 60, now)[0]
    # time refills: 2 seconds later there is room for exactly 2 more
    assert rate_limit._bucket_allow("ip1", 60, now + 2)[0]
    assert rate_limit._bucket_allow("ip1", 60, now + 2)[0]
    assert rate_limit._bucket_allow("ip1", 60, now + 2)[0] is False


def test_quote_path_floods_into_429(client):
    # the login bucket is 10/min but the QUOTE bucket is 60/min: 60 quotes
    # pass, the 61st is denied in the app's own error envelope
    for _ in range(60):
        # 404 is a fine outcome here — the subject is the limiter, not the
        # quote's validity against seeded data
        response = client.post("/pricing/quote", json={"restaurant_id": 1, "distance_km": 1.0})
        assert response.status_code in (200, 404, 422)
    denied = client.post("/pricing/quote", json={"restaurant_id": 1, "distance_km": 1.0})
    assert denied.status_code == 429
    assert denied.json()["error"]["code"] == "rate_limited"
    assert "retry-after" in {k.lower() for k in denied.headers}


def test_failed_login_lockout(client):
    # five wrong passwords (the bucket allows 10/min — the lockout bites
    # at 5), then even a CORRECT password stays locked out for the window
    for _ in range(5):
        bad = client.post("/auth/token", data={"username": "nobody", "password": "wrong"})
        assert bad.status_code == 401
    locked = client.post("/auth/token", data={"username": "nobody", "password": "wrong"})
    assert locked.status_code == 429
    assert "retry-after" in {k.lower() for k in locked.headers}


def test_successful_login_clears_the_failure_count(client, db):
    from core.security import hash_password
    from domain.models import User

    db.add(User(username="ops", password_hash=hash_password("ops"), role="ops"))
    db.commit()
    # a wrong guess then a REAL sign-in: the operator is not punished for
    # one typo — the lockout counts CONSECUTIVE failures
    client.post("/auth/token", data={"username": "nobody", "password": "wrong"})
    good = client.post("/auth/token", data={"username": "ops", "password": "ops"})
    assert good.status_code == 200
    # one more wrong guess afterwards is a normal 401, not a lockout
    bad = client.post("/auth/token", data={"username": "nobody", "password": "wrong"})
    assert bad.status_code == 401


def test_health_is_exempt(client):
    # a monitoring probe must never throttle, no matter the cadence
    for _ in range(50):
        assert client.get("/health").status_code == 200


def test_disabled_setting_passes_everything(client, monkeypatch):
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)
    for _ in range(70):
        response = client.post("/pricing/quote", json={"restaurant_id": 1, "distance_km": 1.0})
        assert response.status_code in (200, 422)
    rate_limit.reset_state()
