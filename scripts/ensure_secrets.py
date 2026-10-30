"""Create the local secrets files if missing (Phase 5, #28 secrets half).

The stack's credentials live in gitignored files under ``secrets/`` and are
mounted as Docker secrets (``/run/secrets/...``) — they no longer travel as
plaintext env values in compose config. Local development generates dev
defaults here; production replaces the files with vault-injected ones —
no code change.

    secrets/db_password.txt    the Postgres admin password (dev: smartops)
    secrets/app_db_password.txt the runtime app role's password (#36: the
                               api/worker connect as sdo_app, a
                               least-privilege role RLS applies to — the
                               bootstrap superuser would bypass it)
    secrets/jwt_secret.txt     the API's JWT signing secret (random per deploy)
    secrets/airflow_conn.txt   full SQLAlchemy URL for Airflow's metadata DB

Usage:
    python scripts/ensure_secrets.py            # create missing files only
    python scripts/ensure_secrets.py --force    # regenerate everything
"""

import argparse
import secrets as pysecrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SECRETS_DIR = Path("secrets")
DEV_DB_PASSWORD = "smartops"  # dev default — matches every documented example


def _write(path: Path, content: str, force: bool, trailing_newline: bool = True) -> bool:
    if path.exists() and not force:
        print(f"  {path} (kept)")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": default translation would write CRLF on Windows, and the
    # shell consumers ($(...)) strip \n but NOT \r — the password would
    # silently become "smartops\r" and every login would fail.
    path.write_text(
        content + ("\n" if trailing_newline else ""),
        encoding="utf-8",
        newline="\n",
    )
    print(f"  {path} written")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="regenerate even if present")
    parser.add_argument("--db-password", default=DEV_DB_PASSWORD, help="dev DB password to store")
    args = parser.parse_args()

    print(f"secrets dir: {SECRETS_DIR.resolve()}")
    _write(SECRETS_DIR / "db_password.txt", args.db_password, args.force)
    # The runtime app role shares the dev password by default (dev only);
    # production sets a distinct least-privilege credential in the vault.
    _write(SECRETS_DIR / "app_db_password.txt", args.db_password, args.force)
    _write(SECRETS_DIR / "jwt_secret.txt", pysecrets.token_urlsafe(48), args.force)
    db_password = (SECRETS_DIR / "db_password.txt").read_text(encoding="utf-8").strip()
    # NO trailing newline: Airflow's _CMD consumer does not strip it, and a
    # newline would become part of the parsed database name ("airflow\n").
    _write(
        SECRETS_DIR / "airflow_conn.txt",
        f"postgresql+psycopg2://smartops:{db_password}@postgres:5432/airflow",
        args.force,
        trailing_newline=False,
    )
    print("done — files are gitignored; production mounts vault-managed ones instead")


if __name__ == "__main__":
    main()
