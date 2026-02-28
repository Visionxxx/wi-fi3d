from __future__ import annotations

from collections import deque
import time
from typing import Deque, Dict, List, Set

from .models import ChangeEvent
from .scoring import EdgeScore


class ChangeDetector:
    def __init__(self, config):
        ccfg = config["change_detection"]
        self.drift_window = int(ccfg["drift_window"])
        self.moved_neighbor_overlap_threshold = float(ccfg["moved_neighbor_overlap_threshold"])
        self.prev_nodes: Set[str] = set()
        self.prev_zone: Dict[str, str] = {}
        self.prev_neighbors: Dict[str, Set[str]] = {}
        self.drift_history: Dict[str, Deque[float]] = {}

    def _neighbors(self, sid: str, edges: List[EdgeScore], k: int = 3) -> Set[str]:
        ranked = []
        for e in edges:
            if not e.active:
                continue
            if e.source == sid:
                ranked.append((e.score * e.confidence, e.target))
            elif e.target == sid:
                ranked.append((e.score * e.confidence, e.source))
        ranked.sort(reverse=True)
        return set([x[1] for x in ranked[:k]])

    def detect(self, source_ids: List[str], zone_map: Dict[str, str], edges: List[EdgeScore]) -> List[ChangeEvent]:
        ts = time.time()
        events: List[ChangeEvent] = []
        current = set(source_ids)

        for sid in sorted(current - self.prev_nodes):
            events.append(ChangeEvent(ts=ts, event_type="added", source_id=sid, details={}))

        for sid in sorted(self.prev_nodes - current):
            events.append(ChangeEvent(ts=ts, event_type="removed", source_id=sid, details={}))

        for sid in sorted(current & self.prev_nodes):
            prev_zone = self.prev_zone.get(sid)
            cur_zone = zone_map.get(sid)
            prev_n = self.prev_neighbors.get(sid, set())
            cur_n = self._neighbors(sid, edges)

            overlap = (len(prev_n & cur_n) / len(prev_n | cur_n)) if (prev_n or cur_n) else 1.0
            moved = (prev_zone and cur_zone and prev_zone != cur_zone) or (overlap < self.moved_neighbor_overlap_threshold)
            if moved:
                if prev_zone and cur_zone and prev_zone != cur_zone:
                    events.append(
                        ChangeEvent(
                            ts=ts,
                            event_type="cluster_changed",
                            source_id=sid,
                            details={"prev_zone": prev_zone, "zone": cur_zone},
                        )
                    )
                events.append(
                    ChangeEvent(
                        ts=ts,
                        event_type="moved",
                        source_id=sid,
                        details={"prev_zone": prev_zone, "zone": cur_zone, "neighbor_overlap": overlap},
                    )
                )

            drift = 1.0 - overlap
            dq = self.drift_history.setdefault(sid, deque(maxlen=self.drift_window))
            dq.append(drift)

            self.prev_neighbors[sid] = cur_n

        self.prev_nodes = current
        self.prev_zone = dict(zone_map)

        return events

    def drift_score(self, sid: str) -> float:
        dq = self.drift_history.get(sid)
        if not dq:
            return 0.0
        return float(sum(dq) / len(dq))
