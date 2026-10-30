"""core.tenancy + tenant_service: the request-scoped funnel (#36).

SQLite can exercise the resolution mechanics (contextvar, overrides,
fallbacks) but NOT the RLS enforcement — the database-engine proof lives
in scripts/data_plane_smoke.py (CI, real Postgres) and
tests/integration/test_rls_postgres.py.
"""

import pytest

from core.config import get_settings
from core.tenancy import reset_request_tenant, set_request_tenant
from domain.services import tenant_service


class TestContextResolution:
    def test_without_a_claim_the_default_tenant_answers(self, db):
        assert tenant_service.context_tenant_id(db) == 1

    def test_a_claim_pins_the_tenant(self, db):
        reset = set_request_tenant(42)
        try:
            assert tenant_service.context_tenant_id(db) == 42
        finally:
            reset_request_tenant(reset)
        # after reset the request scope is gone — default answers again
        assert tenant_service.context_tenant_id(db) == 1


class TestPricingOverrides:
    def test_set_overrides_rejects_unknown_keys(self, db):
        tenant = tenant_service.resolve_default(db)
        with pytest.raises(ValueError, match="pricing_per_km_free"):
            tenant_service.set_overrides(db, tenant, {"pricing_per_km_free": 0.0})
        # nothing partial was written
        assert tenant.pricing_overrides is None

    def test_effective_settings_merge_tenant_overrides(self, db):
        global_base = get_settings().pricing_base_fee
        tenant = tenant_service.resolve_default(db)
        tenant_service.set_overrides(db, tenant, {"pricing_base_fee": global_base + 3.0})
        db.commit()

        reset = set_request_tenant(tenant.id)
        try:
            merged = tenant_service.effective_settings(db)
        finally:
            reset_request_tenant(reset)
        assert merged.pricing_base_fee == pytest.approx(global_base + 3.0)
        # untouched knobs pass through verbatim
        assert merged.pricing_per_km == get_settings().pricing_per_km
        # anonymous resolves through the same funnel: default tenant's config
        assert tenant_service.effective_settings(db).pricing_base_fee == pytest.approx(
            global_base + 3.0
        )

    def test_tenant_without_overrides_gets_defaults_untouched(self, db):
        reset = set_request_tenant(1)
        try:
            merged = tenant_service.effective_settings(db)
        finally:
            reset_request_tenant(reset)
        assert merged.pricing_base_fee == get_settings().pricing_base_fee
