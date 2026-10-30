"""RLS enforcement, against REAL Postgres (#36) — skipped unless
SDO_TEST_DATABASE_URL points at a THROWAWAY Postgres database.

This is the acceptance criterion SQLite cannot express: the database
engine itself refuses cross-tenant reads/inserts, no WHERE clause in
sight. The assertions run as a least-privilege role (sdo_rls_test) — the
bootstrap superuser bypasses RLS by definition, so proving against it
would prove nothing. CI runs the same proof inside
scripts/data_plane_smoke.py on its throwaway Postgres.

    SDO_TEST_DATABASE_URL=postgresql+psycopg://smartops:smartops@localhost:5434/sdo_rls_test \
        pytest tests/integration/test_rls_postgres.py -q
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SDO_TEST_DATABASE_URL"),
    reason="SDO_TEST_DATABASE_URL not set — RLS needs real Postgres",
)


@pytest.fixture(scope="module")
def _pg():
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    url = os.environ["SDO_TEST_DATABASE_URL"]
    repo = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo / "migrations"))
    cfg.attributes["db_url"] = url  # the chain must run HERE, on the scratch DB
    command.upgrade(cfg, "head")

    admin = create_engine(url)
    with admin.connect() as conn:
        conn.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sdo_rls_test') "
                "THEN CREATE ROLE sdo_rls_test LOGIN PASSWORD 'sdo_rls_test'; END IF; END $$"
            )
        )
        conn.execute(text("GRANT USAGE ON SCHEMA public TO sdo_rls_test"))
        conn.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sdo_rls_test"
            )
        )
        conn.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sdo_rls_test"))
        conn.commit()  # 2.0-style: no explicit commit = rolled back on close

    import core.tenancy

    core.tenancy.install_listeners()
    app_engine = create_engine(make_url(url).set(username="sdo_rls_test", password="sdo_rls_test"))
    yield app_engine
    app_engine.dispose()
    admin.dispose()


class TestRLSEnforcement:
    def test_engine_hides_the_other_tenant(self, _pg):
        from sqlalchemy.orm import sessionmaker

        import core.tenancy
        from domain.models import Customer, Tenant

        factory = sessionmaker(bind=_pg)

        # setup as the admin role: a superuser bypasses RLS by definition,
        # which is exactly what platform operations need here
        from sqlalchemy import create_engine as _create_engine

        setup_engine = _create_engine(os.environ["SDO_TEST_DATABASE_URL"])
        core.tenancy.mark_tenant_bypass(setup_engine)
        with sessionmaker(bind=setup_engine)() as s:
            # get-or-create: reruns against a dirty scratch DB are allowed
            a = s.query(Tenant).filter_by(slug="rls-a").one_or_none() or Tenant(
                slug="rls-a", name="A"
            )
            b = s.query(Tenant).filter_by(slug="rls-b").one_or_none() or Tenant(
                slug="rls-b", name="B"
            )
            s.add_all([a, b])
            s.flush()
            if not s.query(Customer).filter_by(tenant_id=a.id, name="cust-a").one_or_none():
                s.add(Customer(tenant_id=a.id, name="cust-a", latitude=1.0, longitude=1.0))
            if not s.query(Customer).filter_by(tenant_id=b.id, name="cust-b").one_or_none():
                s.add(Customer(tenant_id=b.id, name="cust-b", latitude=1.0, longitude=1.0))
            s.commit()
            a_id, b_id = a.id, b.id
        setup_engine.dispose()

        def visible_count(tenant_id: int) -> int:
            reset = core.tenancy.set_request_tenant(tenant_id)
            try:
                with factory() as s:
                    return len(s.query(Customer).all())  # deliberate full scan
            finally:
                core.tenancy.reset_request_tenant(reset)

        assert visible_count(a_id) == 1
        assert visible_count(b_id) == 1

        # WITH CHECK: a tenant-A session cannot INSERT into tenant B
        denied = False
        reset = core.tenancy.set_request_tenant(a_id)
        try:
            with factory() as s:
                s.add(Customer(tenant_id=b_id, name="smuggled", latitude=0.0, longitude=0.0))
                try:
                    s.commit()
                except Exception:
                    denied = True
                    s.rollback()
        finally:
            core.tenancy.reset_request_tenant(reset)
        assert denied, "cross-tenant INSERT must be rejected by RLS WITH CHECK"

    def test_policies_exist_and_are_forced(self, _pg):
        from sqlalchemy import text

        with _pg.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relname IN "
                    "('orders','customers','drivers','restaurants','order_events','driver_events')"
                )
            ).all()
        assert len(rows) == 6
        assert all(rls and forced for _, rls, forced in rows)
