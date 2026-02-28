from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from acceptance_runner import run_suite


def pass_count(run_reports: List[Dict[str, object]]) -> int:
    return sum(1 for r in run_reports if bool(r.get("checks", {}).get("all_passed", False)))


def route_name_from_path(path: str) -> str:
    return Path(path).stem


def render_markdown(matrix: Dict[str, object]) -> str:
    lines = []
    lines.append("# Acceptance Matrix")
    lines.append("")
    lines.append(f"Generated: {matrix['generated_at']}")
    lines.append("")
    lines.append("| Route | Replay | Runs | Passed Runs | All Passed | Zone Purity Mean | Zone Coverage Mean | Floor Consistency Mean | Stable Recall Mean | Avg Edge Churn Mean |")
    lines.append("|---|---|---:|---:|---|---:|---:|---:|---:|---:|")
    for item in matrix["routes"]:
        agg = item["suite"]["aggregate"]
        m = agg["metrics"]
        lines.append(
            f"| {item['route']} | `{item['replay']}` | {item['inputs']['runs']} | {item['passed_runs']} | "
            f"{'yes' if agg['checks']['all_passed'] else 'no'} | "
            f"{m['zone_purity']['mean']:.3f} | {m['zone_coverage']['mean']:.3f} | "
            f"{m['floor_consistency']['mean']:.3f} | {m['stable_node_recall']['mean']:.3f} | "
            f"{m['avg_edge_churn']['mean']:.3f} |"
        )
    lines.append("")
    lines.append(f"Overall all_passed: {'yes' if matrix['overall']['all_passed'] else 'no'}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run acceptance suite across multiple replay routes")
    parser.add_argument("--replay-glob", default="backend/replay/*.ndjson")
    parser.add_argument("--ground-truth", default="docs/ground_truth_nodes.csv")
    parser.add_argument("--config", default=None)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--pause-seconds", type=float, default=0.0)
    parser.add_argument("--out-json", default="docs/acceptance_matrix.json")
    parser.add_argument("--out-md", default="docs/acceptance_matrix.md")
    args = parser.parse_args()

    replay_paths = sorted(glob.glob(args.replay_glob))
    routes = []
    for replay in replay_paths:
        suite = run_suite(
            runs=max(1, args.runs),
            ground_truth=args.ground_truth,
            replay=replay,
            config=args.config,
            pause_seconds=max(0.0, args.pause_seconds),
        )
        routes.append(
            {
                "route": route_name_from_path(replay),
                "replay": replay,
                "inputs": suite["inputs"],
                "passed_runs": pass_count(suite["runs"]),
                "suite": suite,
            }
        )

    overall_all_passed = bool(routes) and all(bool(r["suite"]["aggregate"]["checks"]["all_passed"]) for r in routes)
    matrix = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "replay_glob": args.replay_glob,
            "ground_truth": args.ground_truth,
            "config": args.config,
            "runs": args.runs,
            "pause_seconds": args.pause_seconds,
        },
        "routes": routes,
        "overall": {
            "route_count": len(routes),
            "all_passed": overall_all_passed,
        },
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(matrix, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(matrix), encoding="utf-8")

    print(f"json_report={out_json}")
    print(f"markdown_report={out_md}")
    print(f"overall_all_passed={overall_all_passed}")
    return 0 if overall_all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
