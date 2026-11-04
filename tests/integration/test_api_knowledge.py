"""Per-tenant knowledge API tests (#154): upload, list, scoped retrieval.

The guarantees: an uploaded document is the uploading tenant's PRIVATE
knowledge (retrievable by them and the shared corpus, invisible to other
tenants), re-uploading a title replaces its chunks, and the copilot's
search tool resolves the request's tenant — the same answer scoped per
caller.
"""

from fastapi.testclient import TestClient

DOC = {
    "title": "Refund policy",
    "content": "# Refund policy\n\nWe refund late deliveries in full.\n\n## Exceptions\n\nPeak-hour orders get vouchers instead.",
}


def _seed_second_tenant(client: TestClient, db) -> dict:
    from core.security import hash_password
    from domain.models import Tenant, User
    from tests.integration.test_api_promotions import _headers, _token

    db.add(Tenant(slug="acme", name="Acme"))
    db.commit()
    acme = db.query(Tenant).filter_by(slug="acme").one()
    db.add(
        User(username="acme-ops", password_hash=hash_password("pw"), role="ops", tenant_id=acme.id)
    )
    db.commit()
    return _headers(_token(client, "acme-ops", "pw"))


class TestUploadAndList:
    def test_upload_creates_private_scoped_chunks(self, authed_client: TestClient, db):
        created = authed_client.post("/knowledge/documents", json=DOC)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["source"] == "tenant/1/Refund policy.md"
        assert body["chunks"] >= 1

        from apps.copilot.corpus import add_chunk
        from apps.copilot.providers.embeddings import embed

        add_chunk(db, "docs/runbook.md", 0, "shared runbook text", embed("shared runbook text"))
        db.commit()
        listed = authed_client.get("/knowledge/documents").json()
        sources = [d["source"] for d in listed]
        assert "tenant/1/Refund policy.md" in sources
        assert "docs/runbook.md" in sources  # shared corpus visible alongside

    def test_reupload_replaces_not_appends(self, authed_client: TestClient):
        authed_client.post("/knowledge/documents", json=DOC)
        shorter = authed_client.post(
            "/knowledge/documents",
            json={"title": "Refund policy", "content": "# Refund policy\n\nOne line now."},
        ).json()
        assert shorter["chunks"] == 1  # replaced, not appended

    def test_anonymous_and_viewer_cannot_upload(self, client: TestClient, db):
        from core.security import hash_password
        from domain.models import User
        from tests.integration.test_api_promotions import _headers, _token

        assert client.post("/knowledge/documents", json=DOC).status_code == 401
        db.add(
            User(username="view", password_hash=hash_password("view"), role="viewer", tenant_id=1)
        )
        db.commit()
        viewer = _headers(_token(client, "view", "view"))
        assert client.post("/knowledge/documents", json=DOC, headers=viewer).status_code == 403
        # reads stay open, tenant-scoped
        assert client.get("/knowledge/documents").status_code == 200


class TestScopedRetrieval:
    def test_private_doc_invisible_to_other_tenants(
        self, client: TestClient, authed_client: TestClient, db
    ):
        from apps.copilot.retriever import search_docs

        authed_client.post("/knowledge/documents", json=DOC)
        acme = _seed_second_tenant(client, db)

        # a SHARED corpus row (tenant_id NULL) — the deployment docs the
        # test database otherwise doesn't carry
        from apps.copilot.corpus import add_chunk
        from apps.copilot.providers.embeddings import embed

        add_chunk(db, "docs/runbook.md", 0, "shared runbook text", embed("shared runbook text"))
        db.commit()

        # tenant 1's list shows both; tenant 2's shows only the shared one
        acme_sources = [
            d["source"] for d in client.get("/knowledge/documents", headers=acme).json()
        ]
        assert "tenant/1/Refund policy.md" not in acme_sources
        assert "docs/runbook.md" in acme_sources  # the shared corpus reaches everyone

        # the retriever honors the tenant scope on both match paths
        tenant1_hits = search_docs(db, "refund late deliveries", tenant_id=1)
        tenant2_hits = search_docs(db, "refund late deliveries", tenant_id=2)
        assert any("tenant/1/" in h["source"] for h in tenant1_hits)
        assert not any("tenant/1/" in h["source"] for h in tenant2_hits)
        # both still see the shared corpus
        assert any(h["source"].startswith("docs/") for h in tenant2_hits)

    def test_copilot_tool_resolves_the_callers_tenant(
        self, client: TestClient, authed_client: TestClient, db
    ):
        from apps.copilot.tools import _search_docs
        from core.tenancy import reset_request_tenant, set_request_tenant

        authed_client.post("/knowledge/documents", json=DOC)
        _seed_second_tenant(client, db)

        token = set_request_tenant(2)
        try:
            scoped = _search_docs(db, "refund late deliveries")
        finally:
            reset_request_tenant(token)
        assert not any("tenant/1/" in h["source"] for h in scoped)
