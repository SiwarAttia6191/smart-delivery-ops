"""Shared test fixtures: isolated in-memory database + API test client."""

import os  # noqa: E402
from collections.abc import Generator  # noqa: E402

# Tests must not depend on (or touch) the real PostgreSQL database:
# pin the app-level engine to a throwaway SQLite file BEFORE the app modules
# import and build their engine from settings. The fixtures below override
# get_db with an in-memory engine regardless, but FastAPI's lifespan would
# otherwise still open the configured database.
os.environ["DATABASE_URL"] = "sqlite:///./.test_local.db"

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
    """API client wired to the same isolated session (lifespan skipped)."""

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
