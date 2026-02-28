from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Dict, List, Tuple

import numpy as np

from .timeseries import TimeSeriesStore


@dataclass
class EdgeState:
    active: bool = False


@dataclass
class EdgeScore:
    source: str
    target: str
    score: float
    confidence: float
    pearson: float
    spearman: float
    cross_corr: float
    co_visibility: float
    active: bool


class EdgeScorer:
    def __init__(self, config: Dict[str, object]):
        scoring = config["scoring"]
        self.min_samples = int(scoring["min_samples"])
        self.max_lag = int(scoring["max_lag"])
        self.add_threshold = float(scoring["add_threshold"])
        self.remove_threshold = float(scoring["remove_threshold"])
        self.w_corr = float(scoring["correlation_weight"])
        self.w_xcorr = float(scoring["cross_corr_weight"])
        self.w_cov = float(scoring["co_visibility_weight"])
        self.top_k_per_node = int(scoring.get("top_k_per_node", 0) or 0)

        smooth = config["smoothing"]
        self.default_alpha = float(smooth.get("ewma_alpha", 0.35))
        self.default_median_window = int(smooth.get("median_window", 3))
        self.protocol_smoothing = {
            "wifi": {
                "ewma_alpha": float(smooth.get("wifi", {}).get("ewma_alpha", self.default_alpha)),
                "median_window": int(smooth.get("wifi", {}).get("median_window", self.default_median_window)),
            },
            "bluetooth": {
                "ewma_alpha": float(smooth.get("bluetooth", {}).get("ewma_alpha", self.default_alpha)),
                "median_window": int(smooth.get("bluetooth", {}).get("median_window", self.default_median_window)),
            },
        }

        self.edge_state: Dict[Tuple[str, str], EdgeState] = {}
        self.max_history = 180

    def _pair_key(self, a: str, b: str) -> Tuple[str, str]:
        return (a, b) if a <= b else (b, a)

    def _lagged_corr(self, a: np.ndarray, b: np.ndarray) -> float:
        best = 0.0
        for lag in range(-self.max_lag, self.max_lag + 1):
            if lag < 0:
                x = a[-lag:]
                y = b[:len(x)]
            elif lag > 0:
                x = a[:-lag]
                y = b[lag:lag + len(x)]
            else:
                x, y = a, b

            valid = (~np.isnan(x)) & (~np.isnan(y))
            if np.sum(valid) < self.min_samples:
                continue
            corr = np.corrcoef(x[valid], y[valid])[0, 1]
            if np.isnan(corr):
                continue
            if abs(corr) > abs(best):
                best = float(corr)
        return best

    def _spearman_fast(self, x: np.ndarray, y: np.ndarray) -> float:
        if x.size < 2 or y.size < 2:
            return 0.0
        rx = np.argsort(np.argsort(x)).astype(float)
        ry = np.argsort(np.argsort(y)).astype(float)
        c = np.corrcoef(rx, ry)[0, 1]
        if np.isnan(c):
            return 0.0
        return float(c)

    def _smoothing_for_source(self, source_type: str) -> Tuple[float, int]:
        cfg = self.protocol_smoothing.get(source_type, None)
        if not cfg:
            return self.default_alpha, self.default_median_window
        return float(cfg["ewma_alpha"]), int(cfg["median_window"])

    def _apply_top_k_pruning(self, scores: List[EdgeScore]) -> None:
        if self.top_k_per_node <= 0:
            return

        per_node: Dict[str, List[Tuple[float, int]]] = {}
        for idx, edge in enumerate(scores):
            if not edge.active:
                continue
            rank = float(edge.score * edge.confidence)
            per_node.setdefault(edge.source, []).append((rank, idx))
            per_node.setdefault(edge.target, []).append((rank, idx))

        keep_indices = set()
        for _, ranked in per_node.items():
            ranked.sort(key=lambda x: x[0], reverse=True)
            for _, idx in ranked[: self.top_k_per_node]:
                keep_indices.add(idx)

        for idx, edge in enumerate(scores):
            if edge.active and idx not in keep_indices:
                edge.active = False

    def score_edges(self, store: TimeSeriesStore, source_ids: List[str]) -> List[EdgeScore]:
        scores: List[EdgeScore] = []

        for a, b in combinations(source_ids, 2):
            a_meta = store.source_meta(a)
            b_meta = store.source_meta(b)
            a_alpha, a_window = self._smoothing_for_source(str(a_meta.get("source_type", "")))
            b_alpha, b_window = self._smoothing_for_source(str(b_meta.get("source_type", "")))

            xa, scan_a, seen_a = store.get_smoothed_series(a, a_alpha, a_window)
            xb, scan_b, seen_b = store.get_smoothed_series(b, b_alpha, b_window)
            n = min(len(xa), len(xb))
            if n == 0:
                continue
            n = min(n, self.max_history)

            xa = xa[-n:]
            xb = xb[-n:]
            scan_a = scan_a[-n:]
            scan_b = scan_b[-n:]
            seen_a = seen_a[-n:]
            seen_b = seen_b[-n:]

            valid = (~np.isnan(xa)) & (~np.isnan(xb))
            valid_count = int(np.sum(valid))
            if valid_count < self.min_samples:
                continue

            pearson = float(np.corrcoef(xa[valid], xb[valid])[0, 1]) if valid_count else 0.0
            if np.isnan(pearson):
                pearson = 0.0

            # Spearman via ranking to avoid scipy dependency at runtime paths.
            spearman = self._spearman_fast(xa[valid], xb[valid])

            cross_corr = self._lagged_corr(xa, xb)

            joint_seen = float(np.sum((seen_a > 0.5) & (seen_b > 0.5)))
            joint_scans = float(np.sum((scan_a > 0.5) & (scan_b > 0.5)))
            co_visibility = (joint_seen / joint_scans) if joint_scans > 0 else 0.0

            corr_mix = (abs(pearson) + abs(spearman)) * 0.5
            score = (self.w_corr * corr_mix) + (self.w_xcorr * abs(cross_corr)) + (self.w_cov * co_visibility)

            # Confidence blends sample support and temporal stability.
            support = min(1.0, valid_count / float(self.min_samples * 2))
            std_a = np.nanstd(xa)
            std_b = np.nanstd(xb)
            variability_penalty = 1.0 / (1.0 + (std_a + std_b) / 20.0)
            confidence = max(0.0, min(1.0, 0.7 * support + 0.3 * variability_penalty))

            key = self._pair_key(a, b)
            state = self.edge_state.setdefault(key, EdgeState(active=False))
            if state.active:
                if score < self.remove_threshold:
                    state.active = False
            else:
                if score >= self.add_threshold:
                    state.active = True

            scores.append(
                EdgeScore(
                    source=a,
                    target=b,
                    score=float(score),
                    confidence=float(confidence),
                    pearson=float(pearson),
                    spearman=float(spearman),
                    cross_corr=float(cross_corr),
                    co_visibility=float(co_visibility),
                    active=state.active,
                )
            )

        self._apply_top_k_pruning(scores)
        return scores
