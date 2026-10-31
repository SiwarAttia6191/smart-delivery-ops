"""The deterministic eval suite (#123 Phase D): the golden eval set
runs through the real graph in CI — a wrong tool called, a broken shape,
a budget that stops stopping, or a guardrail that lets an injection
through fails the build like any other test. No keys, no network."""

from pathlib import Path

import pytest

from apps.copilot.evals import load_eval_set, run_case, run_eval
from apps.copilot.providers.base import ProviderReply, ToolCall
from apps.copilot.providers.scripted import ScriptedProvider

EVAL_SET = load_eval_set()
CASES = EVAL_SET["cases"]


def test_eval_set_meta():
    assert EVAL_SET["version"]
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    assert len(CASES) >= 5  # a set this small proves nothing


def test_load_rejects_unknown_tool(tmp_path: Path):
    bad = tmp_path / "bad_set.json"
    bad.write_text(
        '{"version":"0","cases":[{"id":"x","question":"q",'
        '"expected_tools":["nonexistent_tool"],"scripted_answer":"a",'
        '"expected_answer_contains":[],"answer_min_chars":0}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown tool"):
        load_eval_set(bad)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case_passes(case, db):
    result = run_case(case, db)
    assert result["passed"], result


def test_whole_set_scores_perfect_in_ci(db):
    summary = run_eval(db, eval_set=EVAL_SET)
    assert summary["pass_rate"] == 1.0, summary["results"]
    assert summary["total"] == len(CASES)


def test_harness_can_fail_wrong_tool_choice(db):
    """The harness's own regression test: a provider that calls the WRONG
    tool must score a failure — the eval can never be an always-true
    rubber stamp."""
    case = next(c for c in CASES if c["id"] == "kpi_summary")
    wrong = ScriptedProvider(
        [
            ProviderReply(tool_calls=[ToolCall(name="orders_recent", args={"limit": 3})]),
            ProviderReply(content="Here are orders and plenty more text to pass the shape checks."),
        ]
    )
    result = run_case(case, db, provider=wrong)
    assert result["passed"] is False
    assert result["checks"]["tools_matched"] is False


def test_harness_can_fail_bad_shape(db):
    """A right tool call with an unusable answer (too short, missing the
    substance) also fails — shape is part of the contract."""
    case = next(c for c in CASES if c["id"] == "tenant_breakdown")
    shallow = ScriptedProvider(
        [
            ProviderReply(tool_calls=[ToolCall(name="analytics_by_tenant", args={})]),
            ProviderReply(content="ok"),
        ]
    )
    result = run_case(case, db, provider=shallow)
    assert result["passed"] is False
