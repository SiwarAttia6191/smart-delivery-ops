"""Alembic environment: the ops database ONLY.

The warehouse's raw layer is deliberately NOT here — it is the ingest
worker's stream store (apps/worker/ingest.ensure()), created against its
own database and rebuilt from Kafka replay by design; it has no client
that needs versioned schema evolution yet.

The URL comes from the app settings (DATABASE_URL) so the API, the
scripts and the migrations share one source of truth. Programmatic
callers (tests, core.database.run_migrations) may override it per-invocation
via ``config.attributes["db_url"]``.
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

import core.tenancy  # noqa: F401  (#36: seed app.current_tenant on migration connections)
from core.config import get_settings
from domain.models import Base

config = context.config

# the single schema source of truth — every diff is against these models
target_metadata = Base.metadata


def _database_url() -> str:
    override = config.attributes.get("db_url")
    if override:
        return str(override)
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Migrations are platform operations: they may touch every tenant's
        # rows (backfills), so the connection claims the explicit bypass GUC
        # the RLS policies (migration 003) accept. Postgres-only; other
        # dialects (the SQLite parity test) have no GUCs to set. The
        # commit matters: exec_driver_sql auto-begins a transaction, and
        # alembic leaves commit to the caller once the connection is
        # already in one — the whole upgrade would silently roll back.
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql("SET app.tenant_bypass = '1'")
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
