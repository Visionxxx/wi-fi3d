from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import time
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from .models import ScanRecord


@dataclass
class Sample:
    ts_bucket: int
    rssi: Optional[float]
    scan_present: bool
    seen: bool


class TimeSeriesStore:
    def __init__(self, timestep_seconds: int, grace_seconds: int, freshness_seconds: int, max_active_sources: int = 80):
        self.timestep_seconds = timestep_seconds
        self.grace_seconds = grace_seconds
        self.freshness_seconds = freshness_seconds
        self.max_active_sources = max_active_sources

        self.series: Dict[str, Deque[Sample]] = defaultdict(lambda: deque(maxlen=600))
        self.meta: Dict[str, Dict[str, object]] = {}
        self.last_scan_bucket: Dict[str, int] = {"wifi": -1, "bluetooth": -1}
        self.last_seen_ts: Dict[str, float] = {}

    def _bucket(self, ts: float) -> int:
        return int(ts // self.timestep_seconds)

    def ingest_scan_batch(self, pipeline: str, records: List[ScanRecord], scan_ts: Optional[float] = None) -> None:
        ts = scan_ts if scan_ts is not None else (records[0].ts if records else time.time())
        bucket = self._bucket(ts)
        self.last_scan_bucket[pipeline] = max(self.last_scan_bucket[pipeline], bucket)

        seen_ids = set()
        for rec in records:
            sid = rec.source_id
            seen_ids.add(sid)
            self.meta[sid] = {
                "source_type": rec.source_type,
                "name": rec.name,
                "channel": rec.channel,
                "band": rec.band,
            }
            self.last_seen_ts[sid] = rec.ts
            self.series[sid].append(Sample(bucket, rec.rssi, scan_present=True, seen=True))

        for sid, meta in list(self.meta.items()):
            if meta.get("source_type") != pipeline:
                continue
            if sid in seen_ids:
                continue
            self.series[sid].append(Sample(bucket, None, scan_present=True, seen=False))

    def register_no_scan(self, pipeline: str, ts: Optional[float] = None) -> None:
        scan_ts = ts if ts is not None else time.time()
        bucket = self._bucket(scan_ts)
        self.last_scan_bucket[pipeline] = max(self.last_scan_bucket[pipeline], bucket)
        for sid, meta in list(self.meta.items()):
            if meta.get("source_type") != pipeline:
                continue
            self.series[sid].append(Sample(bucket, None, scan_present=False, seen=False))

    def active_source_ids(self, now_ts: Optional[float] = None) -> List[str]:
        now = now_ts if now_ts is not None else time.time()
        out = []
        for sid, last_seen in self.last_seen_ts.items():
            if (now - last_seen) <= self.grace_seconds:
                out.append(sid)
        out.sort(key=lambda sid: self.last_seen_ts.get(sid, 0.0), reverse=True)
        return out[: self.max_active_sources]

    def freshness_ok(self, sid: str, now_ts: Optional[float] = None) -> bool:
        now = now_ts if now_ts is not None else time.time()
        last_seen = self.last_seen_ts.get(sid, 0.0)
        return (now - last_seen) <= self.freshness_seconds

    def last_seen_age(self, sid: str, now_ts: Optional[float] = None) -> float:
        now = now_ts if now_ts is not None else time.time()
        last_seen = self.last_seen_ts.get(sid, 0.0)
        return max(0.0, now - last_seen)

    def get_smoothed_series(self, sid: str, alpha: float = 0.35, median_window: int = 3) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        samples = list(self.series.get(sid, []))
        if not samples:
            return np.array([]), np.array([]), np.array([])

        x = np.array([s.rssi if s.rssi is not None else np.nan for s in samples], dtype=float)
        scan_present = np.array([1.0 if s.scan_present else 0.0 for s in samples], dtype=float)
        seen = np.array([1.0 if s.seen else 0.0 for s in samples], dtype=float)

        if np.all(np.isnan(x)):
            return x, scan_present, seen

        # Median pre-filter (ignore NaN by local forward fill on a copy).
        y = x.copy()
        valid = np.where(~np.isnan(y))[0]
        if valid.size > 0:
            fill_val = y[valid[0]]
            for i in range(len(y)):
                if np.isnan(y[i]):
                    y[i] = fill_val
                else:
                    fill_val = y[i]

            if median_window > 1:
                k = max(1, int(median_window))
                med = np.array([np.median(y[max(0, i-k+1):i+1]) for i in range(len(y))], dtype=float)
            else:
                med = y

            ewma = np.empty_like(med)
            ewma[0] = med[0]
            for i in range(1, len(med)):
                ewma[i] = alpha * med[i] + (1.0 - alpha) * ewma[i-1]

            # Restore explicit NaN where source was not seen in scan.
            ewma[np.isnan(x)] = np.nan
            return ewma, scan_present, seen

        return x, scan_present, seen

    def source_meta(self, sid: str) -> Dict[str, object]:
        return self.meta.get(sid, {})
