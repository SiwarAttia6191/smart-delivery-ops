"""Secret-file support in settings (#28 secrets half)."""

from pathlib import Path

import pytest

from core.config import Settings, _url_with_password


def test_url_with_password_swaps_only_the_password() -> None:
    url = "postgresql+psycopg://smartops:old@postgres:5432/smart_delivery_ops"
    swapped = _url_with_password(url, "n3w-p@ss")
    assert swapped == "postgresql+psycopg://smartops:n3w-p%40ss@postgres:5432/smart_delivery_ops"
    # special characters are percent-encoded so the URL stays parseable
    import sqlalchemy

    engine_url = sqlalchemy.make_url(swapped)
    assert engine_url.password == "n3w-p@ss"
    assert engine_url.database == "smart_delivery_ops"


def test_url_with_password_handles_missing_port() -> None:
    swapped = _url_with_password("postgresql+psycopg://smartops:x@postgres/db", "pw")
    assert swapped == "postgresql+psycopg://smartops:pw@postgres/db"


def test_db_password_file_overrides_both_urls(tmp_path: Path) -> None:
    secret = tmp_path / "db_password.txt"
    secret.write_text("file-secret\n", encoding="utf-8")
    settings = Settings(
        db_password_file=str(secret),
        database_url="postgresql+psycopg://smartops:inline@postgres:5432/ops",
        warehouse_database_url="postgresql+psycopg://smartops:inline@postgres:5432/warehouse",
    )
    assert settings.database_url == "postgresql+psycopg://smartops:file-secret@postgres:5432/ops"
    assert (
        settings.warehouse_database_url
        == "postgresql+psycopg://smartops:file-secret@postgres:5432/warehouse"
    )


def test_jwt_secret_file_overrides_default(tmp_path: Path) -> None:
    secret = tmp_path / "jwt_secret.txt"
    secret.write_text("random-48-bytes\n", encoding="utf-8")
    settings = Settings(jwt_secret_file=str(secret))
    assert settings.auth_jwt_secret == "random-48-bytes"


def test_empty_secret_file_is_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "db_password.txt"
    secret.write_text("   \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        Settings(db_password_file=str(secret))


def test_no_secret_files_means_inline_values_stand() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://smartops:inline@postgres:5432/ops",
        auth_jwt_secret="inline-secret",
    )
    assert settings.database_url.endswith("inline@postgres:5432/ops")
    assert settings.auth_jwt_secret == "inline-secret"
