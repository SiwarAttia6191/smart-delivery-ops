"""Per-IP rate limiting + failed-login backoff on the public surface (#315).

The demo posture leaves GETs, the pricing quote and form login open —
which is exactly the surface an internet-facing deployment (#167) hands
to strangers. This guards it, deliberately small: an in-process token
bucket per (client, path class), a failed-login lockout on /auth/token,
429s in the app's error envelope. Not a distributed limiter — one
process, honest knobs on the settings, unit-tested math. Behind a
reverse proxy, flip `rate_limit_trust_proxy` so the client key comes
from X-Forwarded-For (only safe when the proxy overwrites the header).
"""

import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.config import get_settings

LOGIN_PATH = "/auth/token"
QUOTE_PATH = "/pricing/quote"
IMPORT_PREFIX = "/import/"
# Monitoring, API browsing and the static surfaces must never throttle —
# a doc reader or a health probe is not the threat model.
EXEMPT_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json", "/storefront", "/metrics")

_STATE: dict[tuple[str, str], tuple[float, float]] = {}
_STATE_LOCK = threading.Lock()


def reset_state() -> None:
    """Tests need a clean slate — the buckets live for the process."""
    with _STATE_LOCK:
        _STATE.clear()


def _client_key(request: Request) -> str:
    settings = get_settings()
    if settings.rate_limit_trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rpm_for(path: str, method: str) -> int | None:
    """Requests-per-minute for the path's class; None = exempt/general."""
    settings = get_settings()
    if path == LOGIN_PATH and method == "POST":
        return settings.rate_limit_login_rpm
    if path == QUOTE_PATH and method == "POST":
        return settings.rate_limit_quote_rpm
    if path.startswith(IMPORT_PREFIX) and method in ("POST", "PATCH"):
        return settings.rate_limit_import_rpm
    if path.startswith(EXEMPT_PREFIXES):
        return None
    return settings.rate_limit_general_rpm


def _bucket_allow(key: str, rpm: int, now: float) -> tuple[bool, int]:
    """Continuous-refill token bucket: `rpm` tokens per minute, a burst
    equal to the minute's worth. Returns (allowed, retry-after seconds) —
    a denial carries the wait until the next token, so an honest client
    knows exactly when to come back."""
    with _STATE_LOCK:
        tokens, last = _STATE.get(("bucket", key), (float(rpm), now))
        tokens = min(float(rpm), tokens + (now - last) * rpm / 60.0)
        if tokens < 1.0:
            _STATE[("bucket", key)] = (tokens, now)
            retry = max(1, round((1.0 - tokens) / (rpm / 60.0)))
            return False, retry
        _STATE[("bucket", key)] = (tokens - 1.0, now)
        return True, 0


def _login_locked(key: str, now: float) -> tuple[bool, int]:
    """The failed-login lockout: `threshold` wrong passwords inside the
    window locks /auth/token for the rest of it. Returns (locked, retry
    seconds)."""
    settings = get_settings()
    with _STATE_LOCK:
        count, first = _STATE.get(("login", key), (0, now))
        if now - first > settings.login_backoff_window_seconds:
            count, first = 0, now
        if count < settings.login_backoff_threshold:
            return False, 0
        remaining = int(settings.login_backoff_window_seconds - (now - first)) + 1
        return True, max(remaining, 1)


def _login_record(key: str, now: float) -> None:
    settings = get_settings()
    with _STATE_LOCK:
        count, first = _STATE.get(("login", key), (0, now))
        if now - first > settings.login_backoff_window_seconds:
            count, first = 0, now
        _STATE[("login", key)] = (count + 1, first)


def _login_clear(key: str) -> None:
    with _STATE_LOCK:
        _STATE.pop(("login", key), None)


def _denied(retry_after: int | None = None) -> JSONResponse:
    body = {
        "error": {
            "code": "rate_limited",
            "message": "Too many requests — slow down and try again shortly.",
        }
    }
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(status_code=429, content=body, headers=headers)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return await call_next(request)
        path = request.url.path
        if request.method == "OPTIONS" or path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)

        now = time.monotonic()
        key = _client_key(request)

        # the login path: bucket first, then the failed-login lockout, and
        # the response is inspected to count failures (401 = wrong guess)
        if path == LOGIN_PATH and request.method == "POST":
            locked, retry = _login_locked(key, now)
            if locked:
                return _denied(retry)
            allowed, retry = _bucket_allow("login:" + key, settings.rate_limit_login_rpm, now)
            if not allowed:
                return _denied(retry)
            response = await call_next(request)
            if response.status_code == 401:
                _login_record(key, now)
            elif response.status_code == 200:
                _login_clear(key)
            return response

        rpm = _rpm_for(path, request.method)
        if rpm is None:
            return await call_next(request)
        allowed, retry = _bucket_allow(key + ":" + path.split("/")[1], rpm, now)
        if not allowed:
            return _denied(retry)
        return await call_next(request)
