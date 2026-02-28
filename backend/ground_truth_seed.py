from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from beast.io import ReplayReader


def read_existing(path: str) -> dict[str, dict[str, str]]:
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = str(row.get("source_id", "")).strip().upper()
            if not sid:
                continue
            out[sid] = {
                "room": str(row.get("room", "")).strip(),
                "floor": str(row.get("floor", "")).strip(),
                "group": str(row.get("group", "")).strip(),
                "stable": str(row.get("stable", "1")).strip() or "1",
                "note": str(row.get("note", "")).strip(),
            }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed ground truth CSV from replay log")
    parser.add_argument("--replay", default="backend/replay/sample_walk.ndjson")
    parser.add_argument("--out", default="docs/ground_truth_nodes.generated.csv")
    parser.add_argument("--merge-existing", default=None, help="Optional existing ground-truth CSV to merge labels from")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--stable-min-seen", type=int, default=15)
    args = parser.parse_args()

    reader = ReplayReader(args.replay)
    counts = Counter()
    names: dict[str, str] = {}
    protocols: dict[str, str] = {}
    for row in reader.rows:
        sid = str(row.get("source_id", "")).upper()
        if not sid:
            continue
        counts[sid] += 1
        if sid not in names:
            names[sid] = str(row.get("name", "")).strip()
        if sid not in protocols:
            protocols[sid] = str(row.get("source_type", row.get("pipeline", ""))).strip().lower()

    ranked = counts.most_common(max(1, args.limit))
    existing = read_existing(args.merge_existing) if args.merge_existing else {}
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["source_id", "room", "floor", "group", "stable", "note"])
        for sid, cnt in ranked:
            prev = existing.get(sid, {})
            default_group = "BLE" if protocols.get(sid, "") == "bluetooth" else "WiFi"
            stable = "1" if cnt >= max(1, args.stable_min_seen) else "0"
            note = f"name={names.get(sid, '')};seen={cnt};protocol={protocols.get(sid, '')}"
            merged_note = f"{prev.get('note', '')};{note}".strip(";")
            writer.writerow([
                sid,
                prev.get("room", ""),
                prev.get("floor", ""),
                prev.get("group", default_group),
                prev.get("stable", stable),
                merged_note,
            ])

    print(f"Wrote {len(ranked)} rows to {args.out} (merge_existing={bool(args.merge_existing)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
