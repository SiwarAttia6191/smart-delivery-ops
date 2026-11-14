"""Milestone-10 API tests (#155 memory, #156 streaming, #157 sink;
#153's provider dispatch is unit-covered). The scripted provider serves
every chat path here — the tests prove the PLUMBING (memory persisted and
replayed, SSE events flowing, sink dispatch honoring config), which is
exactly what survives the switch to the funded live provider."""

import json

from fastapi.testclient import TestClient

from apps.copilot import eval_sink
from core.config import get_settings


class TestConversationMemory:
    def test_roundtrip_and_replay(self, authed_client: TestClient, db):
        first = authed_client.post("/copilot/chat", json={"question": "how did we do today?"})
        assert first.status_code == 200, first.text
        cid = first.json()["conversation_id"]
        assert cid

        follow = authed_client.post(
            "/copilot/chat", json={"question": "and yesterday?", "conversation_id": cid}
        )
        assert follow.status_code == 200
        assert follow.json()["conversation_id"] == cid

        from domain.models import ConversationMessage

        msgs = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == cid)
            .order_by(ConversationMessage.id)
            .all()
        )
        assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]

    def test_cross_tenant_conversation_is_404(
        self, client: TestClient, authed_client: TestClient, db
    ):
        from core.security import hash_password
        from domain.models import Tenant, User
        from tests.integration.test_api_promotions import _headers, _token

        cid = authed_client.post("/copilot/chat", json={"question": "hi"}).json()["conversation_id"]
        db.add(Tenant(slug="acme", name="Acme"))
        db.commit()
        acme = db.query(Tenant).filter_by(slug="acme").one()
        db.add(
            User(
                username="acme-ops",
                password_hash=hash_password("pw"),
                role="ops",
                tenant_id=acme.id,
            )
        )
        db.commit()
        stolen = client.post(
            "/copilot/chat",
            json={"question": "hi", "conversation_id": cid},
            headers=_headers(_token(client, "acme-ops", "pw")),
        )
        assert stolen.status_code == 404


class TestStreaming:
    def test_sse_flow_context_nodes_answer(self, authed_client: TestClient):
        with authed_client.stream(
            "POST", "/copilot/chat/stream", json={"question": "how did we do today?"}
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(response.iter_text())

        events = [line[7:] for line in body.splitlines() if line.startswith("event: ")]
        assert "context" in events
        assert "node" in events, body[:400]  # hops visible; body shows any degraded reason
        assert events[-1] == "answer"
        answer = json.loads(body.split("event: answer\ndata: ")[-1].split("\n\n")[0])
        assert answer["answer"]
        assert answer["conversation_id"]

    def test_stream_persists_the_exchange(self, authed_client: TestClient, db):
        with authed_client.stream(
            "POST", "/copilot/chat/stream", json={"question": "tool check"}
        ) as response:
            "".join(response.iter_text())
        from domain.models import ConversationMessage

        assert db.query(ConversationMessage).count() >= 2


class TestEvalSink:
    def test_dispatch_honors_config(self, db, monkeypatch):
        from core.config import get_settings

        calls: list[str] = []
        monkeypatch.setattr(eval_sink, "log_to_langsmith", lambda *a: calls.append("langsmith"))
        eval_set = {"version": "1.0.0", "cases": [{"name": "c", "question": "q"}]}
        results = [{"passed": True, "answer": "a", "tools": []}]

        get_settings().eval_sink = "mlflow"
        eval_sink.dispatch("eval", eval_set, results)
        assert calls == []

        get_settings().eval_sink = "both"
        eval_sink.dispatch("eval", eval_set, results)
        assert calls == ["langsmith"]

    def test_langsmith_failure_never_breaks_the_eval(self, db, monkeypatch):
        def boom(*a):
            raise RuntimeError("no langsmith account")

        monkeypatch.setattr(eval_sink, "_langsmith_client", boom)
        get_settings().eval_sink = "langsmith"
        eval_sink.dispatch(
            "eval", {"version": "1", "cases": []}, [{"passed": True, "answer": "", "tools": []}]
        )  # must not raise
        get_settings().eval_sink = "mlflow"
