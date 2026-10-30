"""Shared FastAPI dependencies."""

from collections.abc import Generator
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from core.database import SessionLocal
from core.security import decode_access_token, role_can_write

# auto_error=False: a missing token is handled below as an explicit 401
# with the WWW-Authenticate header, not FastAPI's default 403.
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token", auto_error=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_claims(token: str | None = Depends(_oauth2_scheme)) -> dict[str, Any]:
    """Decode the bearer token or 401. GETs stay open (demo posture #28);
    this dependency is what mutating endpoints hang off."""
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_writer(claims: dict[str, Any] = Depends(current_claims)) -> dict[str, Any]:
    """Guard for mutating endpoints: ops or admin (admin passes too)."""
    if not role_can_write(claims.get("role", "")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{claims.get('role')}' may not modify resources",
        )
    return claims


def require_admin(claims: dict[str, Any] = Depends(current_claims)) -> dict[str, Any]:
    """Guard for platform-administration endpoints (tenant onboarding, #36):
    only the admin role — ops writers and viewers of ONE tenant must not
    create tenants or list the platform's tenants."""
    if claims.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return claims
