"""Bootstrap the runtime DB role, then apply the Alembic chain.

The migrate one-shot runs this instead of bare `alembic upgrade head`.
FRESH-INSTALL GAP it closes (exposed by the demo unit, #169): the api and
worker connect as the least-privilege ``sdo_app`` role (#36), but NOTHING
in the chain created that role — every deployment's volume had it only
because it was made by hand once. A brand-new volume (demo VPS, new clone,
CI smoke that ever connects as the runtime role) crash-looped the api on
"password authentication failed for user sdo_app".

Idempotent by design and rotation-friendly: the role is created if absent
and its password is RESET to the mounted secret on every run, so rotating
``secrets/app_db_password.txt`` self-heals on the next migrate. Superuser
identity comes from the settings (DATABASE_URL + DB_PASSWORD_FILE), the
same connection the old alembic-only command used. The SQLAlchemy 2.0
commit-as-you-go trap applies: raw executors need the explicit commit.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from core.database import engine, run_migrations  # noqa: E402

RUNTIME_ROLE = "sdo_app"


def _runtime_password() -> str:
    """The API's connection secret, via its own mounted file (DB_APP_PASSWORD_FILE).
    Deliberately separate from the settings' DB_PASSWORD_FILE: that one is the
    ADMIN secret this script connects with; the runtime role gets the app one."""
    path = os.environ.get("DB_APP_PASSWORD_FILE")
    if not path:
        raise SystemExit(
            "DB_APP_PASSWORD_FILE is required: point it at the secret the api "
            "connects with (app_db_password.txt)"
        )
    password = Path(path).read_text(encoding="utf-8").strip()
    if not password:
        raise SystemExit(f"runtime password file {path} is empty")
    return password


def bootstrap_runtime_role() -> None:
    app_password = _runtime_password()
    with engine.begin() as conn:
        conn.execute(
            text(
                f"DO $$ BEGIN "
                f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN "
                f"CREATE ROLE {RUNTIME_ROLE} LOGIN; "
                f"END IF; END $$"
            )
        )
        # ALTER ROLE is a utility statement: bind parameters are not
        # accepted there ($1 is a syntax error), so the secret goes in as a
        # quoted literal — single quotes doubled, which is the only escaping
        # a password literal needs. The role name is a module constant.
        safe_password = app_password.replace("'", "''")
        conn.execute(text(f"ALTER ROLE {RUNTIME_ROLE} WITH LOGIN PASSWORD '{safe_password}'"))
        # The api owns all business tables (schema owner = smartops creates
        # them); the runtime role needs full DML plus sequence usage.
        conn.execute(
            text(
                f"GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLE}; "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                f"IN SCHEMA public TO {RUNTIME_ROLE}; "
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {RUNTIME_ROLE};"
            )
        )
        # future tables (the next migration's CREATE TABLE) pre-granted
        conn.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT "
                f"SELECT, INSERT, UPDATE, DELETE ON TABLES TO {RUNTIME_ROLE}; "
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT "
                f"USAGE, SELECT ON SEQUENCES TO {RUNTIME_ROLE};"
            )
        )
    print(f"runtime role '{RUNTIME_ROLE}' bootstrapped (created-if-absent, password synced)")


if __name__ == "__main__":
    bootstrap_runtime_role()
    run_migrations()
