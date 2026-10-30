"""Database engine and session factory.

The default DATABASE_URL is SQLite so the project runs with zero infrastructure.
Point DATABASE_URL at PostgreSQL (see .env.example / docker-compose.yml) for a
production-like setup.
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.tenancy  # noqa: F401  (#36: installs the RLS GUC listeners)
from core.config import get_settings

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def run_migrations() -> None:
    """Apply the Alembic chain (Phase 5, #28): the ops schema's owner.

    The compose `migrate` service runs this once before api/worker start;
    the API's lifespan runs it too so host-mode (`make api`) bootstraps the
    same way. Pre-Alembic deployments are stamped to head, never re-DDL'd.
    """
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(repo_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(repo_root / "migrations"))
    command.upgrade(alembic_cfg, "head")


def create_all() -> None:
    """Create all tables from the metadata — the THROWAWAY-schema bootstrap
    (tests, CI sqlite fixtures) only. The real schema is owned by the
    Alembic chain (run_migrations); parity between the two is enforced by
    tests/unit/test_migrations_parity.py, which fails the moment they
    drift apart."""
    from domain.models import Base

    Base.metadata.create_all(bind=engine)
