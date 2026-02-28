from __future__ import annotations

import argparse
import hashlib
import json
from typing import Dict, List

from beast.engine import BeastEngine
from beast.io import ReplayReader
from beast.models import ScanRecord


def canonical_snapshot(snapshot: Dict[str, object]) -> Dict[str, object]:
    out = dict(snapshot)
    out["ts"] = 0.0
    out["nodes"] = sorted(out.get("nodes", []), key=lambda n: n.get("source_id", ""))
    out["edges"] = sorted(
        out.get("edges", []),
        key=lambda e: (e.get("source", ""), e.get("target", "")),
    )
    out["zones"] = sorted(out.get("zones", []), key=lambda z: z.get("zone_id", ""))
    stable_events = []
    for event in out.get("events", []):
        e = dict(event)
        e["ts"] = 0.0
        stable_events.append(e)
    out["events"] = sorted(stable_events, key=lambda e: (e.get("event_type", ""), e.get("source_id", "")))
    return out


def ingest_replay(path: str) -> Dict[str, object]:
    engine = BeastEngine()
    engine.reset()
    reader = ReplayReader(path)
    batches = reader.read_until(float("inf"))
    for pipeline, rows in batches.items():
        rows_by_ts: Dict[float, List[ScanRecord]] = {}
        for rec in rows:
            rows_by_ts.setdefault(float(rec.ts), []).append(rec)
        for ts in sorted(rows_by_ts.keys()):
            engine.store.ingest_scan_batch(pipeline, rows_by_ts[ts], scan_ts=ts)
    return engine.snapshot().to_dict()


def digest_snapshot(snapshot: Dict[str, object]) -> str:
    payload = json.dumps(canonical_snapshot(snapshot), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay determinism check for Beast mode")
    parser.add_argument("--path", default="backend/replay/sample_walk.ndjson", help="Replay NDJSON path")
    args = parser.parse_args()

    snap_a = ingest_replay(args.path)
    snap_b = ingest_replay(args.path)
    hash_a = digest_snapshot(snap_a)
    hash_b = digest_snapshot(snap_b)

    print(f"path={args.path}")
    print(f"hash_a={hash_a}")
    print(f"hash_b={hash_b}")
    print(f"equal={hash_a == hash_b}")
    return 0 if hash_a == hash_b else 1


if __name__ == "__main__":
    raise SystemExit(main())
