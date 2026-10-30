"""Password hashing and JWT issuance/verification (Phase 5, #28).

Kept deliberately small and dependency-light: bcrypt for passwords (a
password-hash is deliberately SLOW and salted — the opposite of what you
want anywhere else), PyJWT for HS256 tokens. The signing secret comes from
settings (AUTH_JWT_SECRET); the default is a dev-only value documented as
such — a real deployment sets its own.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from core.config import get_settings

# Role hierarchy: a writer check passes for ops AND admin; "viewer" exists
# so read-closing has a role to grant without new migrations later.
ROLES = ("viewer", "ops", "admin")
_WRITE_ROLES = frozenset({"ops", "admin"})


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        # malformed hash in the store — never a match, never a crash
        return False


def create_access_token(
    username: str, role: str, tenant_id: int | None = None, tenant_slug: str | None = None
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.auth_token_expire_minutes),
        "jti": uuid.uuid4().hex,  # unique token id — revocation lists can key on this later
    }
    # Tenant binding (#36): the machine-readable id drives RLS scoping (the
    # middleware pins app.current_tenant to it); the slug rides along so the
    # token is legible on jwt.io without a lookup.
    if tenant_id is not None:
        claims["tid"] = tenant_id
    if tenant_slug is not None:
        claims["tenant"] = tenant_slug
    return jwt.encode(claims, settings.auth_jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises jwt.ExpiredSignatureError / jwt.InvalidTokenError on failure —
    the API layer translates those into 401."""
    settings = get_settings()
    return jwt.decode(token, settings.auth_jwt_secret, algorithms=["HS256"])


def role_can_write(role: str) -> bool:
    return role in _WRITE_ROLES
