"""Request-scoped tenant context + the Postgres RLS GUC plumbing (#36).

Two halves, one module:

1. A ``ContextVar`` carrying the authenticated request's tenant id. The ASGI
   middleware (apps/api/tenancy_middleware.py) sets it from the verified JWT
   ``tid`` claim; ``tenant_service`` reads it — write paths learn their owner
   and Postgres learns its row scope from the same source of truth.

2. SQLAlchemy event listeners that keep the database enforcing that scope.
   Postgres Row-Level Security policies (migration 003) compare every row's
   ``tenant_id`` against the ``app.current_tenant`` GUC — isolation is
   enforced by the database engine, not by remembering WHERE clauses:

   - ``connect`` (engine-class level, so EVERY engine in the process gets
     it, including scripts that build their own): seeds the session-wide
     default ``'1'`` (the default tenant). Belt for raw-connection paths.
   - ``after_begin`` (session-class level): pins ``app.current_tenant`` at
     the start of every transaction via ``set_config(..., is_local=true)``
     — the contextvar value when one is set, the default otherwise. SET
     LOCAL dies with the transaction, so a pooled connection can never leak
     one request's tenant into the next.

   Internal services that must legitimately see ACROSS tenants (the relay
   publishing every tenant's outbox, benchmark scripts evaluating the whole
   history) call :func:`mark_tenant_bypass` on their engine, which sets the
   ``app.tenant_bypass`` GUC the policies also accept. Explicit, per-engine,
   and grep-visible — the opposite of a forgotten WHERE clause.

SQLite (tests) has neither GUCs nor RLS; the listeners no-op there, which is
why the true isolation semantics are proven on Postgres in
scripts/data_plane_smoke.py and tests/integration/test_rls_postgres.py.
"""

import contextvars
from typing import Any

from sqlalchemy import text
from sqlalchemy.event import listen
from sqlalchemy.orm import Session

#: The GUC Postgres policies compare tenant_id against (migration 003).
TENANT_GUC = "app.current_tenant"
#: The escape-hatch GUC for internal cross-tenant services (see above).
BYPASS_GUC = "app.tenant_bypass"
#: Value the policies accept for the bypass flag.
BYPASS_ON = "1"

current_tenant_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "current_tenant_id", default=None
)


def set_request_tenant(tenant_id: int) -> Any:
    """Set the request's tenant; returns the reset token for ``finally``."""
    return current_tenant_id.set(tenant_id)


def reset_request_tenant(token: Any) -> None:
    current_tenant_id.reset(token)


def _guc_value() -> str:
    """The tenant the GUC should carry right now: the request's claim, or
    the deployment default (tenant 1) for anonymous/demo and background
    paths. Anything that wants cross-tenant visibility must ask for the
    bypass explicitly — silence means default-tenant scope."""
    tid = current_tenant_id.get()
    return str(tid) if tid is not None else "1"


def _seed_default_tenant(dbapi_connection: Any, _record: Any) -> None:
    # session-wide default so raw-connection paths (migrations, ad-hoc
    # scripts) land on the default tenant rather than an unset GUC, which
    # RLS treats as "no rows". Duck-typed to psycopg: other drivers no-op.
    if not hasattr(dbapi_connection, "info"):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"SET {TENANT_GUC} = '1'")
        dbapi_connection.commit()
    except Exception:  # noqa: BLE001 - non-Postgres driver; nothing to seed
        pass
    finally:
        cursor.close()


def _pin_transaction_tenant(session: Session, _transaction: Any, connection: Any) -> None:
    if connection.dialect.name != "postgresql":
        return
    connection.execute(
        text("SELECT set_config(:guc, :val, true)"), {"guc": TENANT_GUC, "val": _guc_value()}
    )


def mark_tenant_bypass(engine: Any) -> None:
    """Let THIS engine see across tenants (relay, benchmarks). Internal use
    only — the API never calls it, so JWT holders stay hard-scoped."""
    listen(engine, "connect", lambda dbc, _: _set_bypass(dbc))


def _set_bypass(dbapi_connection: Any) -> None:
    if not hasattr(dbapi_connection, "info"):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"SET {BYPASS_GUC} = '{BYPASS_ON}'")
        dbapi_connection.commit()
    except Exception:  # noqa: BLE001 - non-Postgres driver
        pass
    finally:
        cursor.close()


def install_listeners() -> None:
    """Register the engine- and session-class listeners. Idempotent per
    (class, event) — SQLAlchemy dedupes identical registrations."""
    from sqlalchemy import Engine

    listen(Engine, "connect", _seed_default_tenant)
    listen(Session, "after_begin", _pin_transaction_tenant)


install_listeners()
