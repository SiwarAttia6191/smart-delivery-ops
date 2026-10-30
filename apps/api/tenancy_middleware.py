"""Request-scope tenant pinning from the JWT (#36).

Pure ASGI middleware (contextvars set here propagate into the endpoints;
BaseHTTPMiddleware's task boundaries don't guarantee that). For every
request carrying a valid bearer token, the ``tid`` claim is pinned into
core.tenancy's ContextVar for the request's lifetime: write resolution
(tenant_service.context_tenant_id) and Postgres RLS (the after_begin
listener's SET LOCAL) both read the same value. Anything else — anonymous
demo traffic, garbage tokens, legacy pre-#36 tokens without a claim — runs
in the default-tenant scope; the auth wall itself still rejects invalid
tokens on the mutating routes.

The tenant claim is only as trustworthy as the signature: it comes out of
a verified JWT, and the user's tenant_id sits behind the claim at issuance
(create_access_token is called with User.tenant_id) — a token can never
claim a tenant its user doesn't belong to.
"""

from typing import Any

import jwt

from core.security import decode_access_token
from core.tenancy import reset_request_tenant, set_request_tenant


class TenantScopeMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            authorization = headers.get(b"authorization", b"")
            if authorization.lower().startswith(b"bearer "):
                token = authorization[7:].decode("latin-1").strip()
                try:
                    tid = decode_access_token(token).get("tid")
                except jwt.InvalidTokenError:
                    tid = None  # anonymous/default scope; write routes 401 on their own
                if tid is not None:
                    reset = set_request_tenant(int(tid))
                    try:
                        await self.app(scope, receive, send)
                    finally:
                        reset_request_tenant(reset)
                    return
        await self.app(scope, receive, send)
