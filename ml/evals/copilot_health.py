"""Copilot production telemetry (#212): the degradation digest + alert.

"Monitoring Production Agents" applied to the free stack: the copilot
already logs one MLflow run per conversation (tracing.py) carrying
provider, tool calls, token usage and the degraded flag — this script is
the WATCHER that data deserved.

    python ml/evals/copilot_health.py                       # digest + alert decision
    python ml/evals/copilot_health.py --days 3 --threshold 0.25

The digest (a markdown report in the established benchmarks directory)
buckets the copilot experiment's runs per business day: conversations,
degradation rate, average tool calls and token usage. When the overall
degradation rate crosses --threshold the script exits 1 — as an Airflow
task that failure IS the alert (ml_alerts.alert_on_failure fires with
the incident narration), so a copilot regression pages like any other
regression. Pairs with #205 (the webhook wiring) and #213 (the miner:
the digest says WHICH days hurt; the miner turns those runs into golden
cases).
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.config import get_settings  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EXPERIMENT = "copilot"


def _client():
    import mlflow
    from mlflow.tracking import MlflowClient

    mlflow.set_tracking_uri(get_settings().mlflow_tracking_uri)
    return MlflowClient()


def collect(client, days: int) -> list[dict]:
    experiment = client.get_experiment_by_name(EXPERIMENT)
    if experiment is None:
        raise SystemExit(f"experiment '{EXPERIMENT}' not found — the copilot has not traced yet")
    cutoff = datetime.now(UTC) - timedelta(days=days)
    runs = client.search_runs(
        [experiment.experiment_id],
        filter_string=f"attributes.start_time >= {int(cutoff.timestamp() * 1000)}",
        max_results=500,
        order_by=["attributes.start_time DESC"],
    )
    return [
        {
            "run_id": run.info.run_id,
            "start": datetime.fromtimestamp(run.info.start_time / 1000, tz=UTC),
            "degraded": run.data.params.get("degraded") == "True",
            "reason": run.data.params.get("degraded_reason", ""),
            "tool_calls": int(run.data.params.get("tool_calls_used") or 0),
            "tokens": int(run.data.params.get("usage_total_tokens") or 0),
            "provider": run.data.params.get("provider", "unknown"),
        }
        for run in runs
    ]


def bucket_by_day(runs: list[dict]) -> dict[str, dict]:
    """Per BUSINESS-day buckets (the same timezone convention the
    warehouse and the sales snapshot grain on)."""
    tz = ZoneInfo(get_settings().business_timezone)
    buckets: dict[str, dict] = defaultdict(
        lambda: {"conversations": 0, "degraded": 0, "tool_calls": 0, "tokens": 0}
    )
    for run in runs:
        day = run["start"].astimezone(tz).date().isoformat()
        bucket = buckets[day]
        bucket["conversations"] += 1
        bucket["degraded"] += 1 if run["degraded"] else 0
        bucket["tool_calls"] += run["tool_calls"]
        bucket["tokens"] += run["tokens"]
    for bucket in buckets.values():
        n = bucket["conversations"]
        bucket["degradation_rate"] = round(bucket["degraded"] / n, 3) if n else 0.0
        bucket["avg_tool_calls"] = round(bucket["tool_calls"] / n, 2) if n else 0.0
        bucket["avg_tokens"] = round(bucket["tokens"] / n, 1) if n else 0.0
    return dict(sorted(buckets.items()))


def render_digest(buckets: dict[str, dict], days: int) -> str:
    total = sum(b["conversations"] for b in buckets.values())
    degraded = sum(b["degraded"] for b in buckets.values())
    rate = round(degraded / total, 3) if total else 0.0
    lines = [
        "# Copilot health digest",
        "",
        f"* Window: last {days} day(s) · {total} conversation(s) · "
        f"{degraded} degraded · **degradation rate {rate:.1%}**",
        "",
        "| business day | conversations | degraded | rate | avg tool calls | avg tokens |",
        "|---|---|---|---|---|---|",
    ]
    for day, bucket in buckets.items():
        lines.append(
            f"| {day} | {bucket['conversations']} | {bucket['degraded']} "
            f"| {bucket['degradation_rate']:.1%} | {bucket['avg_tool_calls']} "
            f"| {bucket['avg_tokens']} |"
        )
    lines += ["", "* Mined into golden cases by ml/evals/mine_degradations.py (#213)."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.34,
        help="the degradation rate that fails the check (a DAG task "
        "failure IS the alert: ml_alerts posts it with narration)",
    )
    parser.add_argument(
        "--digest-dir", default=None, help="default: BENCHMARK_REPORT_DIR or data/benchmarks"
    )
    args = parser.parse_args()

    buckets = bucket_by_day(collect(_client(), args.days))
    digest = render_digest(buckets, args.days)
    print(digest)

    digest_dir = Path(
        args.digest_dir
        or os.environ.get("BENCHMARK_REPORT_DIR", "")
        or REPO / "data" / "benchmarks"
    )
    digest_dir.mkdir(parents=True, exist_ok=True)
    out = digest_dir / f"copilot_health_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.md"
    out.write_text(digest, encoding="utf-8")
    print(f"digest saved: {out}")

    total = sum(b["conversations"] for b in buckets.values())
    degraded = sum(b["degraded"] for b in buckets.values())
    rate = round(degraded / total, 3) if total else 0.0
    if total and rate > args.threshold:
        print(
            f"copilot degradation rate {rate:.1%} exceeds the {args.threshold:.0%} threshold "
            f"({degraded}/{total} in {args.days}d) — failing the task so the alert fires"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
