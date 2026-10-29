"""core.security: bcrypt hashing + JWT round-trip (#28)."""

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

from core.config import get_settings
from core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    role_can_write,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("s3cret")
    assert hashed != "s3cret"  # stored hashed, never plaintext
    assert hashed.startswith("$2")  # bcrypt format
    assert verify_password("s3cret", hashed)
    assert not verify_password("wrong", hashed)


def test_verify_password_never_crashes_on_malformed_hash() -> None:
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_token_roundtrip_carries_identity_claims() -> None:
    token = create_access_token("alina", "ops")
    claims = decode_access_token(token)
    assert claims["sub"] == "alina"
    assert claims["role"] == "ops"
    assert claims["exp"] > claims["iat"]


def test_decode_rejects_tampered_token() -> None:
    token = create_access_token("alina", "ops")
    header, payload, signature = token.split(".")
    forged = f"{header}.{payload}.AAAA{signature[4:]}"
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_access_token(forged)


def test_decode_rejects_expired_token() -> None:
    now = datetime.now(UTC)
    expired = pyjwt.encode(
        {"sub": "alina", "role": "ops", "exp": now - timedelta(minutes=1)},
        get_settings().auth_jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_access_token(expired)


def test_decode_rejects_wrong_secret() -> None:
    now = datetime.now(UTC)
    foreign = pyjwt.encode(
        {"sub": "alina", "role": "ops", "exp": now + timedelta(hours=1)},
        "some-other-secret",
        algorithm="HS256",
    )
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_access_token(foreign)


def test_role_hierarchy() -> None:
    assert role_can_write("ops")
    assert role_can_write("admin")
    assert not role_can_write("viewer")
    assert not role_can_write("")
