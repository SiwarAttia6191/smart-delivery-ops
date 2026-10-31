"""The deterministic evaluation harness (#123 Phase D): the copilot's
answers become testable artifacts, not vibes.

One EVAL SET (data/evals/copilot_eval_set.json, committed and versioned)
holds the golden cases: a question, the tool sequence a competent
operator expects, and the shape that makes an answer usable. Two runners
share this module and the set:

- CI (tests/eval/): each case replays through the REAL graph with the
  deterministic provider scripted from the case's own expected_tools —
  proving the registry, the schemas, tool execution on a real session,
  the budgets and the guardrails. A regression here fails the build
  like any other test.
- Scheduled live run (ml/evals/run_copilot_eval.py → the copilot_evals
  DAG): the same cases go to the CONFIGURED provider, whose ACTUAL tool
  selection and answer shapes get scored and logged to the copilot_evals
  MLflow experiment — the model's judgment, measured over time.

The harness is deliberately honest about what each mode proves: the
scripted runner cannot measure tool CHOICE (the script IS the choice);
it measures that every layer under the choice works. Tool selection is
only measurable against the live provider.
"""

import json
from pathlib import Path
from typing import Any

from apps.copilot import guardrails
from apps.copilot.graph import run_copilot
from apps.copilot.providers.base import ProviderReply, ToolCall
from apps.copilot.providers.scripted import ScriptedProvider
from apps.copilot.tools import ToolSpec, build_registry

DEFAULT_EVAL_SET = Path("data/evals/copilot_eval_set.json")

_KINDS = {"tool_sequence", "budget_stop", "guardrail"}


def load_eval_set(path: Path | str = DEFAULT_EVAL_SET) -> dict[str, Any]:
    """Load and sanity-check the eval set: unique ids, known kinds, and
    (when the case expects tools) tools that actually exist in the
    registry — a renamed tool fails the eval, loudly, not silently."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    registry = build_registry()
    ids = [c["id"] for c in raw["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("eval set has duplicate case ids")
    for case in raw["cases"]:
        kind = case.get("kind", "tool_sequence")
        if kind not in _KINDS:
            raise ValueError(f"case {case['id']}: unknown kind '{kind}'")
        for tool in case.get("expected_tools", []):
            if tool not in registry:
                raise ValueError(f"case {case['id']}: unknown tool '{tool}'")
    return raw


def _scripted_provider_for(case: dict[str, Any]) -> ScriptedProvider:
    """A deterministic provider replaying the case's expectation. A
    tool_sequence case scripts one tool-call step per expected tool then
    the canned answer; a budget_stop case never stops asking for tools
    (the last step repeats) so the graph's budget node is what ends it."""
    if case.get("kind") == "budget_stop":
        return ScriptedProvider(
            [ProviderReply(tool_calls=[ToolCall(name="analytics_summary", args={})])]
        )
    steps = [
        ProviderReply(
            tool_calls=[ToolCall(name=tool, args=case.get("tool_args", {}).get(tool, {}))]
        )
        for tool in case["expected_tools"]
    ]
    steps.append(ProviderReply(content=case["scripted_answer"]))
    return ScriptedProvider(steps)


def _extract_tool_sequence(result: dict[str, Any]) -> list[str]:
    return [m["name"] for m in result.get("messages", []) if m.get("role") == "tool"]


def _shape_checks(case: dict[str, Any], answer: str | None) -> dict[str, bool]:
    answer = answer or ""
    haystack = answer.lower()
    return {
        "answer_min_chars": len(answer) >= case.get("answer_min_chars", 0),
        "expected_answer_contains": all(
            fragment.lower() in haystack for fragment in case.get("expected_answer_contains", [])
        ),
    }


def run_case(
    case: dict[str, Any],
    db: Any,
    provider: Any = None,
    registry: dict[str, ToolSpec] | None = None,
) -> dict[str, Any]:
    """One golden case through the copilot. With no provider, the
    deterministic replay described in the module docstring runs; with a
    provider (the scheduled live run), its actual behavior is scored."""
    registry = registry or build_registry()
    result: dict[str, Any] = {"id": case["id"], "kind": case.get("kind", "tool_sequence")}

    if result["kind"] == "guardrail":
        try:
            guardrails.screen_question(case["question"])
        except ValueError:
            result["passed"] = True
            result["detail"] = "rejected by the guardrails before any model call"
        else:
            result["passed"] = False
            result["detail"] = "the injection pattern reached the model"
        return result

    outcome = run_copilot(provider or _scripted_provider_for(case), registry, db, case["question"])
    result["provider"] = outcome["provider"]
    result["degraded"] = outcome["degraded"]
    result["degraded_reason"] = outcome.get("degraded_reason")
    result["tool_sequence"] = _extract_tool_sequence(outcome)
    result["answer"] = outcome.get("answer") or ""
    checks = _shape_checks(case, outcome.get("answer"))

    if result["kind"] == "budget_stop":
        checks["budget_triggered"] = outcome["tool_calls_used"] >= guardrails.MAX_TOOL_CALLS
        checks["no_crash"] = outcome["degraded"] is False
    else:
        checks["tools_matched"] = result["tool_sequence"] == case["expected_tools"]
        checks["not_degraded"] = outcome["degraded"] is False

    result["checks"] = checks
    result["passed"] = all(checks.values())
    return result


def run_eval(
    db: Any,
    eval_set: dict[str, Any] | None = None,
    provider_factory: Any = None,
) -> dict[str, Any]:
    """The whole set: results per case plus the summary scores the CI
    gate and the MLflow eval run both consume.

    `provider_factory` is a zero-arg callable returning the provider for
    ONE case — the scripted runner needs a FRESH script per case (a
    ScriptedProvider's replay cursor is spent by the first case), while
    the live runner's factory returns the same stateless flagship. With
    no factory, every case scripts itself (the CI path)."""
    eval_set = eval_set or load_eval_set()
    results = [
        run_case(case, db, provider=provider_factory() if provider_factory else None)
        for case in eval_set["cases"]
    ]
    passed = sum(1 for r in results if r["passed"])
    return {
        "eval_set_version": eval_set["version"],
        "total": len(results),
        "passed": passed,
        "pass_rate": passed / len(results) if results else 0.0,
        "results": results,
    }
