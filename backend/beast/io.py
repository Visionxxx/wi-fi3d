from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Dict, Iterable, List

from .models import ScanRecord


class NdjsonLogger:
    def __init__(self, path: str, enabled: bool = True):
        self.path = path
        self.enabled = enabled
        if self.enabled:
            os.makedirs(os.path.dirname(path), exist_ok=True)

    def append_batch(self, pipeline: str, records: List[ScanRecord]) -> None:
        if not self.enabled or not records:
            return
        with open(self.path, "a", encoding="utf-8") as f:
            for rec in records:
                payload = asdict(rec)
                payload["pipeline"] = pipeline
                f.write(json.dumps(payload, ensure_ascii=True) + "\n")


class ReplayReader:
    def __init__(self, path: str):
        self.path = path
        self.rows: List[Dict[str, object]] = []
        self.cursor = 0
        self._load()

    def _load(self) -> None:
        self.rows = []
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        self.rows.sort(key=lambda x: float(x.get("ts", 0.0)))

    def seek(self, index: int) -> None:
        self.cursor = max(0, min(index, len(self.rows)))

    def read_until(self, ts_limit: float) -> Dict[str, List[ScanRecord]]:
        out: Dict[str, List[ScanRecord]] = {"wifi": [], "bluetooth": []}
        while self.cursor < len(self.rows):
            row = self.rows[self.cursor]
            ts = float(row.get("ts", 0.0))
            if ts > ts_limit:
                break
            pipeline = str(row.get("pipeline", row.get("source_type", "wifi")))
            rec = ScanRecord(
                ts=ts,
                source_id=str(row.get("source_id", "")),
                source_type=str(row.get("source_type", pipeline)),
                rssi=float(row.get("rssi", -100.0)),
                name=str(row.get("name", "")),
                channel=row.get("channel"),
                band=str(row.get("band", "")),
            )
            if pipeline in out:
                out[pipeline].append(rec)
            self.cursor += 1
        return out

    @property
    def size(self) -> int:
        return len(self.rows)
