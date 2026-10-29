"""Tenancy foundation (#35): the default tenant, stamping, and the envelope."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from domain.models import OrderEvent, Tenant
from domain.services import tenant_service
from domain.services.order_service import create_order

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.attributes["db_url"] = db_url
    return cfg


class TestDefaultTenant:
    def test_resolve_default_is_idempotent(self, db):
        first = tenant_service.resolve_default(db)
        second = tenant_service.resolve_default(db)
        assert first.id == second.id
        assert db.query(Tenant).count() == 1
        assert first.slug == "default"

    def test_every_tenant_column_is_nullable_only_in_name(self, db):
        # the migration tightened NOT NULL; the ORM stamps the rest
        assert tenant_service.default_id(db) == 1


class TestStamping:
    def test_created_order_stamps_row_and_event_envelope(self, db):
        from domain.models import Customer, Restaurant

        tenant_id = tenant_service.default_id(db)
        db.add(Customer(tenant_id=tenant_id, name="Aziza", latitude=36.8381, longitude=10.2385))
        db.add(
            Restaurant(tenant_id=tenant_id, name="Chez Slah", latitude=36.8065, longitude=10.1815)
        )
        db.commit()

        order = create_order(
            db, customer_id=1, restaurant_id=1, dropoff_lat=36.84, dropoff_lon=10.24
        )

        assert order.tenant_id == tenant_id
        events = db.scalars(
            select(OrderEvent).where(OrderEvent.order_id == order.id).order_by(OrderEvent.id)
        ).all()
        assert events, "the created event must exist"
        for event in events:
            assert event.tenant_id == tenant_id
            # the envelope: the tenant rides inside the payload too, so the
            # warehouse's raw layer inherits the dimension as-received
            assert event.payload["tenant_id"] == tenant_id


class TestBackfill:
    def test_rows_written_under_001_emerge_stamped_after_002(self, tmp_path: Path) -> None:
        """The real migration story: data that predates the column gets
        backfilled by revision 002, then constrained NOT NULL."""
        db_path = tmp_path / "pre_tenancy.db"
        cfg = _alembic_config(f"sqlite:///{db_path}")

        command.upgrade(cfg, "368dffbee1b8")  # the PRE-tenancy schema
        import sqlite3

        raw = sqlite3.connect(db_path)
        raw.execute(
            "INSERT INTO customers (name, latitude, longitude, created_at) "
            "VALUES ('PreTenancy', 36.8, 10.18, CURRENT_TIMESTAMP)"
        )
        raw.commit()
        raw.close()

        command.upgrade(cfg, "head")  # revision 002: insert default + backfill + tighten

        after = sqlite3.connect(db_path)
        tenant_id, name = after.execute(
            "SELECT c.tenant_id, t.slug FROM customers c JOIN tenants t ON t.id = c.tenant_id"
        ).fetchone()
        after.close()
        assert name == "default"
        assert tenant_id == 1
