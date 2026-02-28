from __future__ import annotations

import csv
import os
import threading
import time
from typing import Dict, List, Optional

from .change_detection import ChangeDetector
from .clustering import ZoneClusterer
from .config import load_config
from .io import NdjsonLogger, ReplayReader
from .models import ChangeEvent, EdgeSnapshot, GraphSnapshot, NodeSnapshot, ZoneSnapshot, ScanRecord
from .scoring import EdgeScorer
from .timeseries import TimeSeriesStore


class BeastEngine:
    def __init__(self, config_path: Optional[str] = None):
        self.config = load_config(config_path)
        self.store = None
        self.scorer = None
        self.clusterer = None
        self.change_detector = None
        self._reset_runtime_state()

        lcfg = self.config["logging"]
        self.logger = NdjsonLogger(path=str(lcfg["path"]), enabled=bool(lcfg["enabled"]))

        self.lock = threading.Lock()
        self.replay_reader: Optional[ReplayReader] = None
        self.replay_path: Optional[str] = None
        self.replay_active = False
        self.replay_speed = 1.0
        self.replay_anchor_real = 0.0
        self.replay_anchor_data = 0.0

        self.last_snapshot: Optional[GraphSnapshot] = None

    def _reset_runtime_state(self) -> None:
        self.store = TimeSeriesStore(
            timestep_seconds=int(self.config["timestep_seconds"]),
            grace_seconds=int(self.config["grace_seconds"]),
            freshness_seconds=int(self.config["freshness_seconds"]),
            max_active_sources=int(self.config.get("max_active_sources", 80)),
        )
        self.scorer = EdgeScorer(self.config)
        self.clusterer = ZoneClusterer(self.config)
        self.change_detector = ChangeDetector(self.config)

    def reset(self) -> Dict[str, object]:
        with self.lock:
            self._reset_runtime_state()
            self.replay_reader = None
            self.replay_path = None
            self.replay_active = False
            self.replay_speed = 1.0
            self.replay_anchor_real = 0.0
            self.replay_anchor_data = 0.0
            self.last_snapshot = None
            return {"ok": True}

    def ingest_live(self, pipeline: str, records: List[ScanRecord], scan_ts: Optional[float] = None) -> None:
        with self.lock:
            if records:
                self.store.ingest_scan_batch(pipeline, records, scan_ts=scan_ts)
                self.logger.append_batch(pipeline, records)
            else:
                self.store.register_no_scan(pipeline, ts=scan_ts)

    def replay_start(self, path: str) -> Dict[str, object]:
        with self.lock:
            self.replay_reader = ReplayReader(path)
            self.replay_path = path
            self.replay_active = True
            self.replay_speed = 1.0
            first_ts = float(self.replay_reader.rows[0]["ts"]) if self.replay_reader.rows else time.time()
            self.replay_anchor_real = time.time()
            self.replay_anchor_data = first_ts
            return {
                "ok": True,
                "rows": self.replay_reader.size,
                "first_ts": first_ts,
                "path": self.replay_path,
                "cursor": self.replay_reader.cursor,
                "replay_active": self.replay_active,
            }

    def replay_resume(self) -> Dict[str, object]:
        with self.lock:
            if not self.replay_reader:
                return {"ok": False, "error": "Replay not loaded"}
            self.replay_active = True
            cursor_ts = (
                float(self.replay_reader.rows[self.replay_reader.cursor]["ts"])
                if self.replay_reader.rows and self.replay_reader.cursor < self.replay_reader.size
                else self.replay_anchor_data
            )
            self.replay_anchor_real = time.time()
            self.replay_anchor_data = cursor_ts
            return self.replay_status()

    def replay_stop(self) -> Dict[str, object]:
        with self.lock:
            self.replay_active = False
            return self.replay_status()

    def replay_seek(self, index: int) -> Dict[str, object]:
        with self.lock:
            if not self.replay_reader:
                return {"ok": False, "error": "Replay not loaded"}
            self.replay_reader.seek(index)
            ts = float(self.replay_reader.rows[index]["ts"]) if self.replay_reader.rows and index < self.replay_reader.size else None
            self.replay_anchor_real = time.time()
            self.replay_anchor_data = ts if ts is not None else time.time()
            status = self.replay_status()
            status["seek_ts"] = ts
            return status

    def replay_status(self) -> Dict[str, object]:
        cursor = self.replay_reader.cursor if self.replay_reader else 0
        size = self.replay_reader.size if self.replay_reader else 0
        return {
            "ok": True,
            "replay_active": self.replay_active,
            "replay_size": size,
            "cursor": cursor,
            "path": self.replay_path,
            "progress": (float(cursor) / float(size)) if size > 0 else 0.0,
        }

    def _ingest_replay_if_active(self) -> None:
        if not self.replay_active or not self.replay_reader:
            return
        data_ts = self.replay_anchor_data + (time.time() - self.replay_anchor_real) * self.replay_speed
        batches = self.replay_reader.read_until(data_ts)
        for pipeline, rows in batches.items():
            if rows:
                rows_by_ts: Dict[float, List[ScanRecord]] = {}
                for rec in rows:
                    rows_by_ts.setdefault(float(rec.ts), []).append(rec)
                for ts in sorted(rows_by_ts.keys()):
                    self.store.ingest_scan_batch(pipeline, rows_by_ts[ts], scan_ts=ts)

    def snapshot(self) -> GraphSnapshot:
        with self.lock:
            self._ingest_replay_if_active()

            now = time.time()
            source_ids = self.store.active_source_ids(now)
            edges = self.scorer.score_edges(self.store, source_ids)
            zones_raw = self.clusterer.build_zones(source_ids, edges)

            zone_map: Dict[str, str] = {}
            zones: List[ZoneSnapshot] = []
            for z in zones_raw:
                zone = ZoneSnapshot(**z)
                zones.append(zone)
                for sid in zone.members:
                    zone_map[sid] = zone.zone_id

            events = self.change_detector.detect(source_ids, zone_map, edges)

            nodes = []
            for sid in source_ids:
                meta = self.store.source_meta(sid)
                source_type = str(meta.get("source_type", ""))
                smooth_cfg = self.config.get("smoothing", {})
                default_alpha = float(smooth_cfg.get("ewma_alpha", 0.35))
                default_window = int(smooth_cfg.get("median_window", 3))
                proto_cfg = smooth_cfg.get(source_type, {})
                alpha = float(proto_cfg.get("ewma_alpha", default_alpha))
                median_window = int(proto_cfg.get("median_window", default_window))
                xs, _, _ = self.store.get_smoothed_series(
                    sid,
                    alpha=alpha,
                    median_window=median_window,
                )
                latest_smoothed = float(xs[~(xs != xs)][-1]) if xs.size and (~(xs != xs)).any() else -100.0
                latest_raw = latest_smoothed

                nodes.append(
                    NodeSnapshot(
                        source_id=sid,
                        source_type=source_type,
                        name=str(meta.get("name", "")),
                        band=str(meta.get("band", "")),
                        channel=meta.get("channel"),
                        rssi=float(latest_raw),
                        smoothed_rssi=float(latest_smoothed),
                        last_seen_age_sec=float(self.store.last_seen_age(sid, now)),
                        freshness_ok=self.store.freshness_ok(sid, now),
                        zone_id=zone_map.get(sid),
                        drift_score=self.change_detector.drift_score(sid),
                    )
                )

            edge_snaps = [
                EdgeSnapshot(
                    source=e.source,
                    target=e.target,
                    score=e.score,
                    confidence=e.confidence,
                    pearson=e.pearson,
                    spearman=e.spearman,
                    cross_corr=e.cross_corr,
                    co_visibility=e.co_visibility,
                    active=e.active,
                )
                for e in edges
                if e.active
            ]

            avg_confidence = float(sum(e.confidence for e in edge_snaps) / len(edge_snaps)) if edge_snaps else 0.0
            avg_drift = float(sum(n.drift_score for n in nodes) / len(nodes)) if nodes else 0.0
            stale_nodes = int(sum(1 for n in nodes if not n.freshness_ok))

            snapshot = GraphSnapshot(
                ts=now,
                mode="beast",
                nodes=nodes,
                edges=edge_snaps,
                zones=zones,
                metrics={
                    "node_count": len(nodes),
                    "edge_count": len(edge_snaps),
                    "zone_count": len(zones),
                    "event_count": len(events),
                    "replay_active": self.replay_active,
                    "avg_edge_confidence": avg_confidence,
                    "avg_drift_score": avg_drift,
                    "stale_node_count": stale_nodes,
                },
                events=events,
            )
            self.last_snapshot = snapshot
            return snapshot

    def export_snapshot(self, target: str = "json") -> Dict[str, object]:
        snap = self.last_snapshot or self.snapshot()
        if target == "csv":
            path = os.path.join("backend", "replay", "snapshot_report.csv")
            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["type", "id", "metric_1", "metric_2", "metric_3"])
                for n in snap.nodes:
                    writer.writerow(["node", n.source_id, n.rssi, n.smoothed_rssi, n.drift_score])
                for e in snap.edges:
                    writer.writerow(["edge", f"{e.source}->{e.target}", e.score, e.confidence, e.cross_corr])
                for z in snap.zones:
                    writer.writerow(["zone", z.zone_id, len(z.members), int(z.locked), z.stability_ticks])
            return {"ok": True, "format": "csv", "path": path}

        return {"ok": True, "format": "json", "snapshot": snap.to_dict()}
