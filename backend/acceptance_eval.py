from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
import time
from typing import Dict, List, Set, Tuple

from beast.engine import BeastEngine
from beast.io import ReplayReader
from beast.models import ScanRecord


def canonical_snapshot(snapshot: Dict[str, object]) -> Dict[str, object]:
    out = dict(snapshot)
    out["ts"] = 0.0
    out["nodes"] = sorted(out.get("nodes", []), key=lambda n: n.get("source_id", ""))
    out["edges"] = sorted(out.get("edges", []), key=lambda e: (e.get("source", ""), e.get("target", "")))
    out["zones"] = sorted(out.get("zones", []), key=lambda z: z.get("zone_id", ""))

    stable_events = []
    for event in out.get("events", []):
        e = dict(event)
        e["ts"] = 0.0
        stable_events.append(e)
    out["events"] = sorted(stable_events, key=lambda e: (e.get("event_type", ""), e.get("source_id", "")))
    return out


def digest_snapshot(snapshot: Dict[str, object]) -> str:
    payload = json.dumps(canonical_snapshot(snapshot), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_ground_truth(path: str) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = str(row.get("source_id", "")).strip().upper()
            if not sid:
                continue
            out[sid] = {
                "room": str(row.get("room", "")).strip(),
                "floor": str(row.get("floor", "")).strip(),
                "group": str(row.get("group", "")).strip(),
                "stable": str(row.get("stable", "1")).strip(),
            }
    return out


def replay_snapshots(engine: BeastEngine, replay_path: str) -> List[Dict[str, object]]:
    reader = ReplayReader(replay_path)
    rows = sorted(reader.rows, key=lambda r: float(r.get("ts", 0.0)))
    if not rows:
        return []

    first_orig_ts = float(rows[0].get("ts", 0.0))
    first_real_ts = time.time()
    grouped: Dict[Tuple[str, float], List[ScanRecord]] = defaultdict(list)
    for row in rows:
        pipeline = str(row.get("pipeline", row.get("source_type", "wifi")))
        orig_ts = float(row.get("ts", 0.0))
        ts = first_real_ts + (orig_ts - first_orig_ts)
        grouped[(pipeline, ts)].append(
            ScanRecord(
                ts=ts,
                source_id=str(row.get("source_id", "")).upper(),
                source_type=str(row.get("source_type", pipeline)),
                rssi=float(row.get("rssi", -100.0)),
                name=str(row.get("name", "")),
                channel=row.get("channel"),
                band=str(row.get("band", "")),
            )
        )

    ordered_keys = sorted(grouped.keys(), key=lambda x: (x[1], x[0]))
    snapshots: List[Dict[str, object]] = []
    current_ts = None

    for pipeline, ts in ordered_keys:
        engine.store.ingest_scan_batch(pipeline, grouped[(pipeline, ts)], scan_ts=ts)
        if current_ts is None or ts != current_ts:
            snapshots.append(engine.snapshot().to_dict())
            current_ts = ts
        else:
            snapshots[-1] = engine.snapshot().to_dict()

    return snapshots


def zone_purity(snapshot: Dict[str, object], gt: Dict[str, Dict[str, str]]) -> float:
    zones = snapshot.get("zones", [])
    numerator = 0.0
    denominator = 0.0
    for zone in zones:
        labels: Dict[str, int] = defaultdict(int)
        members = zone.get("members", [])
        counted = 0
        for sid in members:
            room = gt.get(str(sid).upper(), {}).get("room", "")
            if room:
                labels[room] += 1
                counted += 1
        if counted == 0:
            continue
        numerator += max(labels.values())
        denominator += counted
    return (numerator / denominator) if denominator > 0 else 0.0


def floor_consistency(snapshot: Dict[str, object], gt: Dict[str, Dict[str, str]]) -> float:
    zones = snapshot.get("zones", [])
    numerator = 0.0
    denominator = 0.0
    for zone in zones:
        labels: Dict[str, int] = defaultdict(int)
        counted = 0
        for sid in zone.get("members", []):
            floor = gt.get(str(sid).upper(), {}).get("floor", "")
            if floor:
                labels[floor] += 1
                counted += 1
        if counted == 0:
            continue
        numerator += max(labels.values())
        denominator += counted
    return (numerator / denominator) if denominator > 0 else 0.0


def zone_coverage(snapshot: Dict[str, object], gt: Dict[str, Dict[str, str]]) -> float:
    gt_nodes = {sid for sid, meta in gt.items() if meta.get("room")}
    if not gt_nodes:
        return 0.0
    present = {str(n.get("source_id", "")).upper() for n in snapshot.get("nodes", [])}
    return len(gt_nodes & present) / float(len(gt_nodes))


def stable_node_recall(snapshot: Dict[str, object], gt: Dict[str, Dict[str, str]]) -> float:
    stable_nodes = {sid for sid, meta in gt.items() if str(meta.get("stable", "1")) in {"1", "true", "True"}}
    if not stable_nodes:
        return 0.0
    present_fresh = {
        str(n.get("source_id", "")).upper()
        for n in snapshot.get("nodes", [])
        if bool(n.get("freshness_ok", False))
    }
    return len(stable_nodes & present_fresh) / float(len(stable_nodes))


def average_edge_churn(snapshots: List[Dict[str, object]]) -> float:
    if len(snapshots) < 2:
        return 0.0
    churn_values = []
    for i in range(1, len(snapshots)):
        prev_edges = {
            tuple(sorted((e.get("source", ""), e.get("target", ""))))
            for e in snapshots[i - 1].get("edges", [])
        }
        cur_edges = {
            tuple(sorted((e.get("source", ""), e.get("target", ""))))
            for e in snapshots[i].get("edges", [])
        }
        union = prev_edges | cur_edges
        inter = prev_edges & cur_edges
        jaccard = (len(inter) / float(len(union))) if union else 1.0
        churn_values.append(1.0 - jaccard)
    return sum(churn_values) / float(len(churn_values))


def evaluate(ground_truth_path: str, replay_path: str, config_path: str | None = None) -> Dict[str, object]:
    engine = BeastEngine(config_path=config_path)
    engine.reset()
    gt = load_ground_truth(ground_truth_path)
    snapshots = replay_snapshots(engine, replay_path)
    final = snapshots[-1] if snapshots else {"nodes": [], "edges": [], "zones": [], "events": []}

    engine2 = BeastEngine(config_path=config_path)
    engine2.reset()
    snapshots2 = replay_snapshots(engine2, replay_path)
    final2 = snapshots2[-1] if snapshots2 else {"nodes": [], "edges": [], "zones": [], "events": []}
    deterministic = digest_snapshot(final) == digest_snapshot(final2)
    metrics = {
        "zone_purity": zone_purity(final, gt),
        "zone_coverage": zone_coverage(final, gt),
        "floor_consistency": floor_consistency(final, gt),
        "stable_node_recall": stable_node_recall(final, gt),
        "avg_edge_churn": average_edge_churn(snapshots),
        "snapshot_count": len(snapshots),
        "deterministic_replay": deterministic,
    }

    thresholds = engine.config.get("validation", {})
    checks = {
        "zone_purity": metrics["zone_purity"] >= float(thresholds.get("zone_purity_min", 0.7)),
        "zone_coverage": metrics["zone_coverage"] >= float(thresholds.get("zone_coverage_min", 0.7)),
        "floor_consistency": metrics["floor_consistency"] >= float(thresholds.get("floor_consistency_min", 0.7)),
        "stable_node_recall": metrics["stable_node_recall"] >= float(thresholds.get("stable_node_recall_min", 0.75)),
        "avg_edge_churn": metrics["avg_edge_churn"] <= float(thresholds.get("avg_edge_churn_max", 0.35)),
        "deterministic_replay": bool(metrics["deterministic_replay"]),
    }
    checks["all_passed"] = all(checks.values())
    return {"metrics": metrics, "checks": checks, "thresholds": thresholds}


def main() -> int:
    parser = argparse.ArgumentParser(description="Acceptance evaluation for building topology reconstruction")
    parser.add_argument("--ground-truth", default="docs/ground_truth_nodes.csv")
    parser.add_argument("--replay", default="backend/replay/sample_walk.ndjson")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    report = evaluate(args.ground_truth, args.replay, config_path=args.config)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["checks"]["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
