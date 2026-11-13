"""Mine degraded copilot runs into golden-set candidates (#213).

The identify-errors → iterate → verify loop from "Autonomous Agent
Improvement with LangSmith Engine", feasible with ZERO paid services:
MLflow is the trace store (every copilot conversation lands in the
``copilot`` experiment with ``degraded`` + ``degraded_reason`` params —
tracing.py), and this script is the MINER.

    python ml/evals/mine_degradations.py                    # mine → review file
    python ml/evals/mine_degradations.py --promote RUN_ID \\
        --tools analytics_summary --case-id my_case \\
        --scripted-answer "Today: ..."                      # reviewed → eval set

The loop: degraded runs are CLUSTERED by failure type (the exception
class inside ``degraded_reason``) and emitted as CANDIDATES into
``data/evals/mined_candidates.json`` — a REVIEW file, not a golden set:
a human reads the questions, decides which are real product gaps, and
promotes them. Promotion appends the case to
``data/evals/copilot_eval_set.json`` and bumps the set version — from
then on every eval run (and the scheduled DAG) scores the exact failure
that production produced. Production failures become regression tests.

When the LangSmith account lands (#214), the same mining runs against
LangSmith traces instead — the eval sink (#157) already ships that
export path; the candidate shape stays identical.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.config import get_settings  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EVAL_SET_PATH = REPO / "data" / "evals" / "copilot_eval_set.json"
CANDIDATES_PATH = REPO / "data" / "evals" / "mined_candidates.json"
EXPERIMENT = "copilot"


def _client():
    import mlflow
    from mlflow.tracking import MlflowClient

    mlflow.set_tracking_uri(get_settings().mlflow_tracking_uri)
    return MlflowClient()


def exception_key(reason: str) -> str:
    """The cluster key: the exception class inside the chain's reason
    ('openai:gpt-4o-mini failed: RateLimitError: ...' → 'RateLimitError').
    Grouping by VENDOR message would make one noisy endpoint a cluster
    per day; grouping by failure CLASS is the shape a reviewer fixes."""
    match = re.search(r"failed:\s*([A-Za-z_.]+(?:Error|Exception|Timeout))", reason)
    return (
        match.group(1).rsplit(".", 1)[-1] if match else (reason.split(":")[0].strip() or "unknown")
    )


def tools_from_artifact(client, run_id: str) -> list[str]:
    """The tools that actually served inside the degraded conversation,
    from the conversation.json artifact (role='tool' messages)."""
    try:
        artifact = client.download_artifacts(run_id, "conversation.json")
        messages = json.loads(Path(artifact).read_text(encoding="utf-8")).get("messages", [])
        return [m.get("name") for m in messages if m.get("role") == "tool" and m.get("name")]
    except Exception:  # noqa: BLE001 - an unreadable artifact leaves tools unknown
        return []


def fetch_degraded(client, experiment_name: str = EXPERIMENT) -> list[dict]:
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        raise SystemExit(f"experiment '{experiment_name}' not found — no copilot traced yet")
    runs = client.search_runs(
        [experiment.experiment_id],
        filter_string="params.degraded = 'True'",
        max_results=200,
    )
    mined = []
    for run in runs:
        params = run.data.params
        mined.append(
            {
                "run_id": run.info.run_id,
                "question": params.get("question", "").strip(),
                "degraded_reason": params.get("degraded_reason", ""),
                "tools_used": tools_from_artifact(client, run.info.run_id),
            }
        )
    return [m for m in mined if m["question"]]


def cluster(mined: list[dict]) -> dict[str, list[dict]]:
    clusters: dict[str, list[dict]] = defaultdict(list)
    for item in mined:
        clusters[exception_key(item["degraded_reason"])].append(item)
    return dict(clusters)


def mine(experiment_name: str = EXPERIMENT) -> dict:
    client = _client()
    mined = fetch_degraded(client, experiment_name)
    clusters = cluster(mined)
    candidates = []
    for reason_class, items in sorted(clusters.items()):
        candidates.append(
            {
                "failure_class": reason_class,
                "count": len(items),
                "suggested_review": "read the questions; keep the ones a golden case should cover; "
                "promote with --promote <run_id> --tools <tools that SHOULD have served>",
                "runs": items,
            }
        )
    payload = {
        "mined_at_experiment": experiment_name,
        "degraded_runs": len(mined),
        "candidates": candidates,
        "note": "REVIEW FILE — not a golden set. Promote reviewed candidates with --promote.",
    }
    CANDIDATES_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"mined {len(mined)} degraded run(s) into {len(candidates)} cluster(s): {CANDIDATES_PATH}"
    )
    for candidate in candidates:
        print(f"  - {candidate['failure_class']} x{candidate['count']}")
    return payload


def promote(run_id: str, tools: list[str], case_id: str, scripted_answer: str) -> None:
    eval_set = json.loads(EVAL_SET_PATH.read_text(encoding="utf-8"))
    if any(c["id"] == case_id for c in eval_set["cases"]):
        raise SystemExit(f"case id '{case_id}' already exists in the eval set")
    mined = {m["run_id"]: m for m in _flatten_mined()}.get(run_id, {})
    eval_set["cases"].append(
        {
            "id": case_id,
            "question": mined.get("question") or f"mined from run {run_id}",
            "expected_tools": tools,
            "scripted_answer": scripted_answer,
            "answer_min_chars": 40,
            "mined_from": run_id,  # provenance: the production failure this case guards
        }
    )
    version = eval_set.get("version", "1.0.0")
    major, minor, _ = version.split(".")
    eval_set["version"] = f"{major}.{int(minor) + 1}.0"
    EVAL_SET_PATH.write_text(
        json.dumps(eval_set, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"promoted '{case_id}' → eval set v{eval_set['version']} ({len(eval_set['cases'])} cases)"
    )


def _flatten_mined() -> list[dict]:
    if not CANDIDATES_PATH.exists():
        return []
    payload = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    return [run for candidate in payload.get("candidates", []) for run in candidate.get("runs", [])]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--promote", metavar="RUN_ID", help="append a reviewed candidate to the eval set"
    )
    parser.add_argument("--tools", default="", help="comma-separated tools that SHOULD have served")
    parser.add_argument("--case-id", default="", help="the golden case id (unique)")
    parser.add_argument(
        "--scripted-answer", default="", help="the reviewer's expected canned answer"
    )
    args = parser.parse_args()

    if args.promote:
        if not (args.tools and args.case_id and args.scripted_answer):
            parser.error("--promote needs --tools, --case-id and --scripted-answer")
        promote(
            args.promote,
            [t.strip() for t in args.tools.split(",") if t.strip()],
            args.case_id,
            args.scripted_answer,
        )
        return 0
    mine()
    return 0


if __name__ == "__main__":
    sys.exit(main())
