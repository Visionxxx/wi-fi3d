from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional


@dataclass
class ScanRecord:
    ts: float
    source_id: str
    source_type: str  # wifi|bluetooth
    rssi: float
    name: str = ""
    channel: Optional[int] = None
    band: str = ""


@dataclass
class EdgeSnapshot:
    source: str
    target: str
    score: float
    confidence: float
    pearson: float
    spearman: float
    cross_corr: float
    co_visibility: float
    active: bool


@dataclass
class ZoneSnapshot:
    zone_id: str
    members: List[str]
    locked: bool
    stability_ticks: int


@dataclass
class ChangeEvent:
    ts: float
    event_type: str  # added|removed|moved|cluster_changed
    source_id: str
    details: Dict[str, object] = field(default_factory=dict)


@dataclass
class NodeSnapshot:
    source_id: str
    source_type: str
    name: str
    band: str
    channel: Optional[int]
    rssi: float
    smoothed_rssi: float
    last_seen_age_sec: float
    freshness_ok: bool
    zone_id: Optional[str]
    drift_score: float


@dataclass
class GraphSnapshot:
    ts: float
    mode: str
    nodes: List[NodeSnapshot]
    edges: List[EdgeSnapshot]
    zones: List[ZoneSnapshot]
    metrics: Dict[str, object]
    events: List[ChangeEvent]

    def to_dict(self) -> Dict[str, object]:
        return {
            "ts": self.ts,
            "mode": self.mode,
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
            "zones": [asdict(z) for z in self.zones],
            "metrics": self.metrics,
            "events": [asdict(e) for e in self.events],
        }
