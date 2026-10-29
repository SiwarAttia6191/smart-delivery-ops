"""The migration chain must never drift from the models (#28).

Runs the REAL Alembic chain against an empty throwaway SQLite database,
then diffs the result against Base.metadata (the same models create_all
uses and the app serves from). A non-empty diff means someone changed a
model without a migration (or wrote a migration that doesn't match the
models) — either way the build must go red before the schema does.

SQLite is the right engine for the diff here: the models are deliberately
dual-dialect (tests already run on SQLite), and the chain is exercised
against real Postgres in CI's data-plane smoke.
"""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from domain.models import Base

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.attributes["db_url"] = db_url
    return cfg


def test_upgrade_head_then_diff_against_models_is_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "migrated.db"
    cfg = _alembic_config(f"sqlite:///{db_path}")

    command.upgrade(cfg, "head")  # the actual chain, not create_all

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()

    assert diff == [], f"schema drifted from the models: {diff}"


def test_chain_is_idempotent_at_head(tmp_path: Path) -> None:
    db_path = tmp_path / "again.db"
    cfg = _alembic_config(f"sqlite:///{db_path}")

    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # a second run must be a clean no-op

    assert db_path.exists()
