"""Create an API user for the JWT auth wall (Phase 5, #28).

Usage (repo root):
    python scripts/create_user.py --demo            # seed the documented demo trio
    python scripts/create_user.py --username alina --role ops   # prompts for password
    echo "$PW" | python scripts/create_user.py --username alina --role ops --stdin

Demo credentials (DEV ONLY — matches the Airflow admin/admin posture):
    admin / admin   (admin role)
    ops   / ops     (ops role)
    view  / view    (viewer role — read-only, rejected on writes)
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.database import engine  # noqa: E402
from core.security import ROLES, hash_password  # noqa: E402
from domain.models import Tenant, User  # noqa: E402

DEMO_USERS = [
    ("admin", "admin", "admin"),
    ("ops", "ops", "ops"),
    ("view", "view", "viewer"),
]


def create_user(
    session, username: str, password: str, role: str, tenant_slug: str = "default"
) -> None:
    if role not in ROLES:
        raise SystemExit(f"unknown role '{role}' - valid: {', '.join(ROLES)}")
    tenant = session.query(Tenant).filter_by(slug=tenant_slug).one_or_none()
    if tenant is None:
        raise SystemExit(f"unknown tenant slug '{tenant_slug}' (create it first: POST /tenants)")
    existing = session.scalar(select(User).where(User.username == username))
    if existing is not None:
        # idempotent re-runs update the password/role instead of duplicating
        existing.password_hash = hash_password(password)
        existing.role = role
        existing.tenant_id = tenant.id
        print(f"updated existing user '{username}' (role={role}, tenant={tenant_slug})")
        return
    session.add(
        User(
            username=username,
            password_hash=hash_password(password),
            role=role,
            tenant_id=tenant.id,
        )
    )
    print(f"created user '{username}' (role={role}, tenant={tenant_slug})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="seed the documented demo trio")
    parser.add_argument("--username")
    parser.add_argument("--role", default="ops", choices=ROLES)
    parser.add_argument("--password", help="omit to be prompted (or pipe via --stdin)")
    parser.add_argument("--stdin", action="store_true", help="read the password from stdin")
    parser.add_argument("--tenant", default="default", help="tenant slug to bind the user to (#36)")
    args = parser.parse_args()

    # Schema comes from the Alembic chain (#28) — start the stack first.
    session = sessionmaker(bind=engine)()
    try:
        if args.demo:
            for username, password, role in DEMO_USERS:
                create_user(session, username, password, role)
        else:
            if not args.username:
                raise SystemExit("--username required (or use --demo)")
            if args.stdin:
                password = sys.stdin.readline().rstrip("\n")
            elif args.password:
                password = args.password
            else:
                password = getpass.getpass(f"password for {args.username}: ")
            if not password:
                raise SystemExit("empty password")
            create_user(session, args.username, password, args.role, args.tenant)
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    main()
