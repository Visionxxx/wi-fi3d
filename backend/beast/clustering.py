from __future__ import annotations

from typing import Dict, List, Set

from .scoring import EdgeScore


class ZoneClusterer:
    """
    Alternative to Louvain/Leiden: thresholded weighted graph components.
    """

    def __init__(self, config: Dict[str, object]):
        zcfg = config["zones"]
        self.edge_confidence_threshold = float(zcfg["edge_confidence_threshold"])
        self.stability_lock_ticks = int(zcfg["stability_lock_ticks"])
        self.prev_members: Dict[str, Set[str]] = {}
        self.stability_ticks: Dict[str, int] = {}
        self.next_zone_id = 1

    def _new_zone_id(self) -> str:
        zone_id = f"zone-{self.next_zone_id}"
        self.next_zone_id += 1
        return zone_id

    @staticmethod
    def _jaccard(a: Set[str], b: Set[str]) -> float:
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def build_zones(self, source_ids: List[str], edges: List[EdgeScore]) -> List[Dict[str, object]]:
        graph: Dict[str, Set[str]] = {sid: set() for sid in source_ids}

        for e in edges:
            if not e.active:
                continue
            if e.confidence < self.edge_confidence_threshold:
                continue
            graph.setdefault(e.source, set()).add(e.target)
            graph.setdefault(e.target, set()).add(e.source)

        visited = set()
        components: List[Set[str]] = []
        for sid in source_ids:
            if sid in visited:
                continue
            stack = [sid]
            comp = set()
            while stack:
                cur = stack.pop()
                if cur in visited:
                    continue
                visited.add(cur)
                comp.add(cur)
                for nxt in graph.get(cur, set()):
                    if nxt not in visited:
                        stack.append(nxt)
            components.append(comp)

        sorted_components = sorted(components, key=lambda c: (-len(c), sorted(c)[0] if c else ""))
        zones = []
        prev_zone_ids = set(self.prev_members.keys())
        next_prev_members: Dict[str, Set[str]] = {}
        next_stability_ticks: Dict[str, int] = {}

        for comp in sorted_components:
            zone_id = None
            best_score = -1.0
            for candidate in list(prev_zone_ids):
                score = self._jaccard(comp, self.prev_members.get(candidate, set()))
                if score > best_score:
                    best_score = score
                    zone_id = candidate

            if zone_id is None or best_score <= 0.0:
                zone_id = self._new_zone_id()
            else:
                prev_zone_ids.discard(zone_id)

            prev = self.prev_members.get(zone_id, set())
            if comp == prev:
                tick = self.stability_ticks.get(zone_id, 0) + 1
            else:
                tick = 1
            next_prev_members[zone_id] = set(comp)
            next_stability_ticks[zone_id] = tick

            zones.append(
                {
                    "zone_id": zone_id,
                    "members": sorted(comp),
                    "locked": tick >= self.stability_lock_ticks,
                    "stability_ticks": tick,
                }
            )

        self.prev_members = next_prev_members
        self.stability_ticks = next_stability_ticks
        return zones
