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


def require_customer(claims: dict[str, Any] = Depends(current_claims)) -> dict[str, Any]:
    """Guard for the customer-owned surfaces (#309): the token must be a
    CUSTOMER token carrying the customer id (cid). Staff tokens are
    rejected here by design — a customer's order history is customer-
    scoped, not staff-readable through this route."""
    if claims.get("role") != "customer" or claims.get("cid") is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer role required",
        )
    return claims


def require_order_creator(claims: dict[str, Any] = Depends(current_claims)) -> dict[str, Any]:
    """POST /orders accepts two caller kinds (#309): STAFF (ops/admin,
    ordering on behalf of any customer — the operator's own flow) and a
    CUSTOMER token (which may only ever order for itself). The route
    enforces the self-ordering rule against the returned kind."""
    role = claims.get("role", "")
    if role_can_write(role):
        return {"kind": "staff", "claims": claims}
    if role == "customer" and claims.get("cid") is not None:
        return {"kind": "customer", "customer_id": int(claims["cid"])}
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Role '{role}' may not place orders",
    )


def require_driver(claims: dict[str, Any] = Depends(current_claims)) -> dict[str, Any]:
    """Guard for the driver-owned surfaces (#311): the token must be a
    DRIVER token carrying the fleet-member id (did). Staff tokens are
    rejected here — a courier's job list is courier-scoped."""
    if claims.get("role") != "driver" or claims.get("did") is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Driver role required",
        )
    return claims


def require_transition_caller(claims: dict[str, Any] = Depends(current_claims)) -> dict[str, Any]:
    """The order-transition guard (#311): STAFF may advance any order;
    a DRIVER token may only advance orders assigned to their own fleet
    row, and only ride-and-deliver transitions (the cancel stays with the
    operator). The route enforces the order ownership against the did."""
    role = claims.get("role", "")
    if role_can_write(role):
        return {"kind": "staff", "claims": claims}
    if role == "driver" and claims.get("did") is not None:
        return {"kind": "driver", "driver_id": int(claims["did"])}
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Role '{role}' may not advance orders",
    )


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
