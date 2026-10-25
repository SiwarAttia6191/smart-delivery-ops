"""Unit tests for the outbox relay (apps/worker/relay.py) — fully hermetic:
a fake publisher stands in for Kafka, the session runs on the shared SQLite
fixture, and no broker is ever contacted."""

import json

from sqlalchemy.orm import Session

from apps.worker.relay import relay_once
from domain.models import OrderEvent, RelayState


class FakePublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []  # (topic, key, value)

    def produce(self, topic: str, key: str, value: str) -> None:
        self.sent.append((topic, key, value))

    def flush(self) -> None:
        pass


def add_event(session: Session, order_id: str, event_type: str = "created") -> OrderEvent:
    event = OrderEvent(order_id=order_id, event_type=event_type, payload={"note": event_type})
    session.add(event)
    session.commit()
    return event


class TestRelayOnce:
    def test_publishes_events_keyed_by_order_in_id_order(self, db: Session):
        add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        add_event(db, "ORD-1", "assigned")
        publisher = FakePublisher()

        relayed = relay_once(db, publisher, "orders.events")

        assert relayed == 3
        assert [key for _, key, _ in publisher.sent] == ["ORD-1", "ORD-2", "ORD-1"]
        assert all(topic == "orders.events" for topic, _, _ in publisher.sent)
        records = [json.loads(value) for _, _, value in publisher.sent]
        assert [r["event_id"] for r in records] == [1, 2, 3]
        assert records[0]["event_type"] == "created"
        assert records[2]["event_type"] == "assigned"

    def test_state_row_tracks_progress(self, db: Session):
        add_event(db, "ORD-1")
        relay_once(db, FakePublisher(), "orders.events")

        state = db.get(RelayState, 1)
        assert state is not None
        assert state.last_relayed_event_id == 1

    def test_second_run_is_a_noop(self, db: Session):
        add_event(db, "ORD-1")
        publisher = FakePublisher()
        relay_once(db, publisher, "orders.events")

        relayed = relay_once(db, publisher, "orders.events")

        assert relayed == 0
        assert len(publisher.sent) == 1  # nothing re-sent

    def test_batch_limit_and_resume(self, db: Session):
        for i in range(3):
            add_event(db, f"ORD-{i}")
        publisher = FakePublisher()

        first = relay_once(db, publisher, "orders.events", batch_size=2)
        second = relay_once(db, publisher, "orders.events", batch_size=2)

        assert (first, second) == (2, 1)
        assert len(publisher.sent) == 3

    def test_empty_log_writes_no_state_row(self, db: Session):
        publisher = FakePublisher()

        relayed = relay_once(db, publisher, "orders.events")

        assert relayed == 0
        assert db.get(RelayState, 1) is None
