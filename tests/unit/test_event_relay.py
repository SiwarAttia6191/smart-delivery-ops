"""Unit tests for the outbox relay (apps/worker/relay.py) — fully hermetic:
a fake publisher stands in for Kafka, the session runs on the shared SQLite
fixture, and no broker is ever contacted.

Note on locking: the claim query uses FOR UPDATE SKIP LOCKED, which SQLite
silently ignores — so the locking semantics themselves are exercised only on
PostgreSQL. What these tests CAN pin hermetically is the selection logic
(claim-based, not offset-based), the delivery-gating invariant, and the
at-least-once replay behavior.
"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from apps.worker.relay import SCHEMA_VERSION, relay_once
from domain.models import OrderEvent, RelayDeadLetter, RelayState


class FakePublisher:
    """In-memory stand-in. Simulates the failure modes that matter:
    produce() raising mid-batch, flush() failing as a broker outage
    (fail_flush), and permanent per-event delivery failures (fail_event_ids,
    reported via take_delivery_failures exactly like delivery reports)."""

    def __init__(
        self,
        fail_flush: bool = False,
        fail_produce_after: int | None = None,
        fail_event_ids: tuple[int, ...] = (),
        fail_produce_event_ids: tuple[int, ...] = (),
    ) -> None:
        self.sent: list[tuple[str, str, str, list[tuple[str, str | bytes | None]] | None]] = []
        self.fail_flush = fail_flush
        self.fail_produce_after = fail_produce_after  # raise on the Nth produce call
        self.fail_event_ids = set(fail_event_ids)
        self.fail_produce_event_ids = set(fail_produce_event_ids)
        self._reported: list[int] = []
        self._reported_once = False

    def produce(
        self,
        topic: str,
        key: str,
        value: str,
        headers: list[tuple[str, str | bytes | None]] | None = None,
    ) -> None:
        if self.fail_produce_after is not None and len(self.sent) >= self.fail_produce_after:
            raise RuntimeError("produce queue full")
        if self.fail_produce_event_ids:
            for hdr_key, hdr_val in headers or []:
                if (
                    hdr_key == "event_id"
                    and int(hdr_val.decode() if isinstance(hdr_val, bytes) else str(hdr_val))
                    in self.fail_produce_event_ids
                ):
                    raise RuntimeError("payload rejected at serialization")
        self.sent.append((topic, key, value, headers))

    def flush(self) -> None:
        if self.fail_event_ids and not self._reported_once:
            # Permanent failures only: the poisoned messages are removed from
            # the queue, so a re-flush of the survivors succeeds.
            self._reported = sorted(self.fail_event_ids)
            self._reported_once = True
            raise RuntimeError("delivery not confirmed")
        if self.fail_flush:
            raise RuntimeError("delivery not confirmed")

    def take_delivery_failures(self) -> list[int]:
        failed = self._reported
        self._reported = []
        return failed


def add_event(session: Session, order_id: str, event_type: str = "created") -> OrderEvent:
    event = OrderEvent(order_id=order_id, event_type=event_type, payload={"note": event_type})
    session.add(event)
    session.commit()
    return event


def published_ids(session: Session) -> list[int]:
    session.expire_all()  # re-read post-rollback state, not cached objects
    return [
        e.id for e in session.query(OrderEvent).filter(OrderEvent.published_at.isnot(None)).all()
    ]


class TestClaimBasedSelection:
    def test_publishes_due_events_keyed_by_order_in_id_order(self, db: Session):
        add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        add_event(db, "ORD-1", "assigned")
        publisher = FakePublisher()

        relayed = relay_once(db, publisher, "orders.events")

        assert relayed == 3
        assert [key for _, key, _, _ in publisher.sent] == ["ORD-1", "ORD-2", "ORD-1"]
        assert all(topic == "orders.events" for topic, _, _, _ in publisher.sent)
        records = [json.loads(value) for _, _, value, _ in publisher.sent]
        assert [r["event_id"] for r in records] == [1, 2, 3]
        assert records[0]["event_type"] == "created"
        assert records[2]["event_type"] == "assigned"

    def test_rows_ignored_by_offset_are_still_claimed(self, db: Session):
        """The old offset cursor would have skipped these — e.g. after a
        sequence value was burned by a rolled-back insert. Claim-based
        selection relays whatever rows exist and are due."""
        first = add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        first.published_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
        publisher = FakePublisher()

        relayed = relay_once(db, publisher, "orders.events")

        assert relayed == 1
        assert [key for _, key, _, _ in publisher.sent] == ["ORD-2"]

    def test_marked_rows_are_never_relayed_again(self, db: Session):
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

    def test_permanently_failed_delivery_is_quarantined_and_survivors_relayed(self, db: Session):
        add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        add_event(db, "ORD-3")
        publisher = FakePublisher(fail_event_ids=(2,))

        relayed = relay_once(db, publisher, "orders.events")

        assert relayed == 2  # 1 and 3 delivered; 2 quarantined
        assert published_ids(db) == [1, 3]
        dead = db.query(RelayDeadLetter).one()
        assert dead.event_id == 2
        assert "permanent failure" in dead.error
        # The quarantined row is never claimed again
        assert relay_once(db, publisher, "orders.events") == 0

    def test_produce_failure_quarantines_one_row_not_the_batch(self, db: Session):
        add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        add_event(db, "ORD-3")
        publisher = FakePublisher(fail_produce_event_ids=(2,))  # only event 2 rejects

        relayed = relay_once(db, publisher, "orders.events")

        assert relayed == 2
        assert published_ids(db) == [1, 3]
        assert db.query(RelayDeadLetter).one().event_id == 2

    def test_broker_outage_rolls_back_entire_batch(self, db: Session):
        add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        publisher = FakePublisher(fail_flush=True)

        try:
            relay_once(db, publisher, "orders.events")
        except RuntimeError:
            pass
        db.rollback()

        assert published_ids(db) == []
        assert db.query(RelayDeadLetter).count() == 0  # outage quarantines nothing
        # and the batch replays later
        healthy = FakePublisher()
        assert relay_once(db, healthy, "orders.events") == 2

    def test_empty_log_writes_no_state_row(self, db: Session):
        publisher = FakePublisher()

        relayed = relay_once(db, publisher, "orders.events")

        assert relayed == 0
        assert db.get(RelayState, 1) is None


class TestDeliveryGating:
    """The at-least-once invariant: the batch is marked published ONLY after
    the publisher confirms delivery. Any failure must leave every row due."""

    def test_flush_failure_marks_nothing_and_raises(self, db: Session):
        add_event(db, "ORD-1")
        publisher = FakePublisher(fail_flush=True)

        try:
            relay_once(db, publisher, "orders.events")
        except RuntimeError:
            pass
        db.rollback()  # what the worker loop does on failure

        assert published_ids(db) == []  # nothing marked → the batch will replay
        assert db.get(RelayState, 1) is None

    def test_failed_batch_replays_fully_on_retry(self, db: Session):
        add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        failing = FakePublisher(fail_flush=True)
        try:
            relay_once(db, failing, "orders.events")
        except RuntimeError:
            pass
        db.rollback()

        healthy = FakePublisher()
        relayed = relay_once(db, healthy, "orders.events")

        assert relayed == 2  # both events replayed — duplicates are the contract
        assert [key for _, key, _, _ in healthy.sent] == ["ORD-1", "ORD-2"]


class TestWireFormat:
    def test_schema_version_and_event_type_header(self, db: Session):
        add_event(db, "ORD-1", "assigned")
        publisher = FakePublisher()

        relay_once(db, publisher, "orders.events")

        record = json.loads(publisher.sent[0][2])
        assert record["schema_version"] == SCHEMA_VERSION
        assert publisher.sent[0][3] == [("event_type", b"assigned"), ("event_id", b"1")]

    def test_state_row_tracks_last_published_for_observability(self, db: Session):
        add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        relay_once(db, FakePublisher(), "orders.events")

        state = db.get(RelayState, 1)
        assert state is not None
        assert state.last_relayed_event_id == 2
