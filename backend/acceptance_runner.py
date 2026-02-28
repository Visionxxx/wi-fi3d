from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from acceptance_eval import evaluate


NUMERIC_METRICS = [
    "zone_purity",
    "zone_coverage",
    "floor_consistency",
    "stable_node_recall",
    "avg_edge_churn",
]


def aggregate_runs(reports: List[Dict[str, object]]) -> Dict[str, object]:
    aggregate: Dict[str, object] = {"metrics": {}, "checks": {}}

    for metric in NUMERIC_METRICS:
        values = [float(r["metrics"][metric]) for r in reports]
        aggregate["metrics"][metric] = {
            "mean": statistics.fmean(values),
            "min": min(values),
            "max": max(values),
        }

    aggregate["metrics"]["deterministic_replay"] = all(bool(r["metrics"]["deterministic_replay"]) for r in reports)
    aggregate["metrics"]["snapshot_count_mean"] = statistics.fmean(
        [float(r["metrics"]["snapshot_count"]) for r in reports]
    )

    check_keys = [k for k in reports[0]["checks"].keys() if k != "all_passed"]
    for key in check_keys:
        aggregate["checks"][key] = all(bool(r["checks"][key]) for r in reports)
    aggregate["checks"]["all_passed"] = all(bool(r["checks"]["all_passed"]) for r in reports)
    return aggregate


def run_suite(
    runs: int,
    ground_truth: str,
    replay: str,
    config: str | None = None,
    pause_seconds: float = 0.0,
) -> Dict[str, object]:
    run_reports: List[Dict[str, object]] = []
    for i in range(runs):
        report = evaluate(ground_truth, replay, config_path=config)
        report["run_index"] = i + 1
        run_reports.append(report)
        if pause_seconds > 0 and i < runs - 1:
            time.sleep(pause_seconds)

    suite = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "runs": runs,
            "ground_truth": ground_truth,
            "replay": replay,
            "config": config,
            "pause_seconds": pause_seconds,
        },
        "thresholds": run_reports[0].get("thresholds", {}),
        "runs": run_reports,
        "aggregate": aggregate_runs(run_reports),
    }
    return suite


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 3x acceptance suite and emit a consolidated report")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--pause-seconds", type=float, default=0.0)
    parser.add_argument("--ground-truth", default="docs/ground_truth_nodes.csv")
    parser.add_argument("--replay", default="backend/replay/sample_walk.ndjson")
    parser.add_argument("--config", default=None)
    parser.add_argument("--out", default="docs/acceptance_report.json")
    args = parser.parse_args()

    suite = run_suite(
        runs=max(1, args.runs),
        ground_truth=args.ground_truth,
        replay=args.replay,
        config=args.config,
        pause_seconds=max(0.0, args.pause_seconds),
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(suite, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    print(json.dumps(suite["aggregate"], indent=2, ensure_ascii=True))
    print(f"report_path={out_path}")
    return 0 if bool(suite["aggregate"]["checks"]["all_passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
