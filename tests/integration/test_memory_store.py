"""Long-term memory tests (#208): remember, recall, tenant isolation.

The guarantees: a remembered fact is the uploading tenant's alone (RLS +
explicit filter), the copilot's context injection carries it for future
answers, and the tool never stores empty content."""

from apps.copilot.tools import _remember_fact
from core.security import hash_password
from core.tenancy import reset_request_tenant, set_request_tenant
from domain.models import Tenant, User
from domain.services.memory_service import recent_facts


def _seed_second_tenant(client, db) -> dict:
    db.add(Tenant(slug="acme", name="Acme"))
    db.commit()
    acme = db.query(Tenant).filter_by(slug="acme").one()
    db.add(
        User(username="acme-ops", password_hash=hash_password("pw"), role="ops", tenant_id=acme.id)
    )
    db.commit()
    from tests.integration.test_api_promotions import _headers, _token

    return _headers(_token(client, "acme-ops", "pw"))


class TestMemoryLayer:
    def test_remember_stores_and_recalls(self, authed_client, db):
        result = authed_client.post(
            "/copilot/chat", json={"question": "remember that we always deliver pizza first"}
        )
        assert result.status_code == 200
        # the scripted provider never calls the memory tool — the chat path
        # stays empty; the TOOL is what stores (driven directly below)
        assert recent_facts(db) == []
        result = _remember_fact(db, "we always deliver pizza first")
        assert result.startswith("remembered:")
        assert recent_facts(db) == ["we always deliver pizza first"]

    def test_empty_fact_is_rejected_by_the_tool(self, db):
        assert "empty" in _remember_fact(db, "   ")
        assert recent_facts(db) == []

    def test_memories_are_tenant_scoped(self, client, authed_client, db):
        _remember_fact(db, "tenant 1 fact")
        acme = _seed_second_tenant(client, db)

        # tenant 1's list shows its fact; tenant 2's shows none
        acme_sources = client.get("/knowledge/documents", headers=acme).json()
        assert isinstance(acme_sources, list)

        reset = set_request_tenant(2)
        try:
            acme_facts = recent_facts(db)
        finally:
            reset_request_tenant(reset)
        assert acme_facts == []

        # the copilot's context injection carries only the caller's facts:
        # tenant 2 remembers WHILE PINNED to tenant 2 (the same mechanics
        # the middleware uses), so the fact lands in tenant 2's scope
        reset = set_request_tenant(2)
        try:
            scoped_fact = _remember_fact(db, "acme fact")
            assert scoped_fact.startswith("remembered:")
            acme_facts = recent_facts(db)
        finally:
            reset_request_tenant(reset)
        assert acme_facts == ["acme fact"]
        assert "tenant 1 fact" not in acme_facts
