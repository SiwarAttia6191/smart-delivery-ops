"""Database engine and session factory.

The default DATABASE_URL is SQLite so the project runs with zero infrastructure.
Point DATABASE_URL at PostgreSQL (see .env.example / docker-compose.yml) for a
production-like setup.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import get_settings

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def create_all() -> None:
    """Create all tables (MVP bootstrap; Alembic migrations land in Phase 5)."""
    from domain.models import Base

    Base.metadata.create_all(bind=engine)
