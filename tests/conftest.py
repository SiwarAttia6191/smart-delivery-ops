"""Shared test fixtures: isolated in-memory database + API test client."""

import os  # noqa: E402
from collections.abc import Generator  # noqa: E402
from pathlib import Path  # noqa: E402

# Tests must not depend on (or touch) the real PostgreSQL database:
# pin the app-level engine to a throwaway SQLite file BEFORE the app modules
# import and build their engine from settings. The fixtures below override
# get_db with an in-memory engine regardless, but FastAPI's lifespan would
# otherwise still open the configured database.
#
# The lifespan now applies the Alembic chain to that file (#28), so a file
# left over from a PREVIOUS session (or a pre-Alembic run: tables but no
# alembic_version) would break the upgrade — the same stamp problem the
# live demo DB has. Delete it so every session truly starts from zero.
_TEST_DB = Path(".test_local.db")
_TEST_DB.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from apps.api.deps import get_db  # noqa: E402
from apps.api.main import app  # noqa: E402
from domain.models import Base  # noqa: E402


@pytest.fixture
def engine() -> Generator[object, None, None]:
    engine = create_engine(
        "sqlite://",  # in-memory, one connection shared across threads
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def default_tenant(db):
    """Every business row carries tenant_id (#35): seed the deployment's
    default tenant once per test — deterministic id 1 on the fresh schema,
    which fixture constructions stamp directly."""
    from domain.services import tenant_service

    tenant_service.resolve_default(db)
    db.commit()


@pytest.fixture(autouse=True)
def _no_route_capture(monkeypatch) -> None:
    """Order creation best-effort-captures OSRM geometry (#58); tests must
    not depend on (or wait for) a network service. Tests that exercise the
    route path monkeypatch _fetch_geometry themselves and win over this."""
    from domain.services import route_service

    monkeypatch.setattr(route_service, "_fetch_geometry", lambda base_url, key: None)


@pytest.fixture
def db(engine) -> Generator[Session, None, None]:
    """Function-scoped session over a fresh schema (tables dropped per test)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    """API client wired to the same isolated session (lifespan skipped).
    Unauthenticated by design — tests that must see the auth wall's 401s
    use this; tests that just need to mutate use `authed_client`."""

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def authed_client(db: Session) -> Generator[TestClient, None, None]:
    """API client carrying a REAL JWT (#28): seeds an ops user in the
    isolated schema, logs in through /auth/token (the actual flow), and
    sends the bearer token on every request."""

    def _override():
        yield db

    from core.security import hash_password
    from domain.models import User

    db.add(User(username="ops", password_hash=hash_password("ops"), role="ops"))
    db.commit()
    app.dependency_overrides[get_db] = _override
    with TestClient(app) as probe:
        token = probe.post("/auth/token", data={"username": "ops", "password": "ops"}).json()[
            "access_token"
        ]
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as c:
        yield c
    app.dependency_overrides.clear()
