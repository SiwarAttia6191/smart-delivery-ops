"""Reset the public demo to a clean morning state (decision 0004, phase D).

Runs INSIDE the api container (docker compose run --rm api python
scripts/reset_demo.py) or anywhere the DATABASE_URL reaches the demo
database. One command, two halves:

  1. wipe the demo day's activity: orders + their events + payments
     (menu items, coupons and receipts survive — coupons get their
     redemption counter zeroed so the storefront demo starts fresh)
  2. re-seed everything idempotent (sign-in trio, coupon, driver,
     forecasts) via the same scripts/seed_demo.py the deploy used

Deletes are bounded to the demo database by construction — this script
must never point at anything but the demo unit.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from core.database import engine  # noqa: E402


def reset() -> None:
    with engine.begin() as conn:
        # order_events/payments reference orders; CASCADE clears both.
        # menu_items, coupons, users, restaurants, drivers survive —
        # the menu is the operator's work, not the day's traffic.
        conn.execute(text("TRUNCATE orders, order_events, payments RESTART IDENTITY CASCADE"))
        conn.execute(text("UPDATE coupons SET times_used = 0"))
        # checkpoint retention (#184): LangGraph does not prune — without
        # this the checkpoint tables balloon (a 2026 production postmortem
        # measured 40 GB in six weeks). Threads older than 7 days go.
        try:
            stale = (
                "DELETE FROM {table} WHERE thread_id IN "
                "(SELECT id FROM conversations WHERE created_at < now() - interval '7 days')"
            )
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                conn.execute(text(stale.format(table=table)))
        except Exception as exc:  # noqa: BLE001 - checkpoint tables may not exist yet
            # fail-open WITH a trace (bug-hunt #260): a silent skip here
            # would balloon the checkpoint tables exactly like the 2026
            # postmortem — a permission error must be findable
            print(f"[warn] checkpoint retention skipped: {exc}")
    print("demo activity wiped (orders, events, payments; coupon counters zeroed)")

    from scripts.seed_demo import main as seed_demo

    sys.argv = ["seed_demo"]
    seed_demo()
    print("demo re-seeded — clean morning state")


if __name__ == "__main__":
    reset()
