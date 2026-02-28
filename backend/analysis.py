from collections import deque
import time
import pandas as pd
import numpy as np

# --- Configuration ---
MAX_SAMPLES = 100           # Store last 100 samples per AP.
MIN_SAMPLES_FOR_CORR = 20   # Need at least 20 common samples to calculate correlation.
NODE_GRACE_PERIOD = 120     # Seconds. Keep a node for this long after it was last seen.


class WifiDataStore:
    """
    Stores time-series data for WiFi access points and calculates correlations.
    """
    def __init__(self):
        # BSSID -> { "metadata": {...}, "samples": deque([...]), "last_seen": timestamp }
        self.access_points = {}
        self._is_dirty = True
        self._cached_edges = []
        self._cached_threshold = None
        self._last_corr_calc_ts = 0.0

    def add_scan_results(self, scan_results):
        """
        Adds a new list of scanned APs to the data store.
        """
        current_time = time.time()
        seen_bssids = set()

        for ap in scan_results:
            bssid = ap.get("bssid")
            rssi = ap.get("rssi")
            if not bssid or rssi is None:
                continue
            
            seen_bssids.add(bssid)

            if bssid not in self.access_points:
                self.access_points[bssid] = {
                    "metadata": {k: v for k, v in ap.items() if k != "rssi"},
                    "samples": deque(maxlen=MAX_SAMPLES),
                    "last_seen": current_time
                }
            
            # Always update metadata and last_seen timestamp
            self.access_points[bssid]["metadata"].update({k: v for k, v in ap.items() if k != "rssi"})
            self.access_points[bssid]["last_seen"] = current_time
            
            # Add the new sample
            self.access_points[bssid]["samples"].append((current_time, rssi))
            self._is_dirty = True

        # For APs that were not in this scan, add a 'NaN' sample but do NOT update 'last_seen'
        for bssid, data in self.access_points.items():
            if bssid not in seen_bssids:
                data["samples"].append((current_time, np.nan))
                self._is_dirty = True

    def get_nodes(self):
        """
        Returns a list of all access points that have been seen within the grace period.
        """
        nodes = []
        current_time = time.time()
        for bssid, data in self.access_points.items():
            # Only include nodes that have been seen recently
            if (current_time - data.get("last_seen", 0)) < NODE_GRACE_PERIOD:
                # Find the most recent non-NaN RSSI value for the node
                latest_valid_rssi = next((s[1] for s in reversed(data["samples"]) if not np.isnan(s[1])), None)
                
                if latest_valid_rssi is not None:
                    node_data = data["metadata"].copy()
                    node_data["rssi"] = latest_valid_rssi
                    node_data["last_seen"] = data.get("last_seen", current_time)
                    node_data["last_seen_age_sec"] = round(current_time - data.get("last_seen", current_time), 1)
                    nodes.append(node_data)
        return nodes

    def calculate_correlations(self, corr_threshold=0.7):
        """
        Calculates Pearson correlation between all pairs of APs with sufficient data.
        Returns a list of edges for a graph.
        """
        # Reuse correlation results if data and threshold are unchanged.
        if (
            not self._is_dirty
            and self._cached_threshold == corr_threshold
            and (time.time() - self._last_corr_calc_ts) < 2.0
        ):
            return list(self._cached_edges)

        edges = []
        # Only consider nodes that are currently active for correlation calculation
        active_nodes = self.get_nodes()
        active_bssids = [node["bssid"] for node in active_nodes]
        
        if len(active_bssids) < 2:
            return []

        df = pd.DataFrame({
            bssid: pd.Series(dict(self.access_points[bssid]["samples"]))
            for bssid in active_bssids
        })

        if df.empty:
            return []

        # Guard against very short series where time interpolation can fail.
        if len(df.index) < 3:
            return []
            
        df.index = pd.to_datetime(df.index, unit='s')
        df_filled = df.interpolate(method='time', limit=2, limit_direction='forward')
        corr_matrix = df_filled.corr(method='pearson', min_periods=MIN_SAMPLES_FOR_CORR)

        for i in range(len(corr_matrix.columns)):
            for j in range(i + 1, len(corr_matrix.columns)):
                bssid1 = corr_matrix.columns[i]
                bssid2 = corr_matrix.columns[j]
                correlation = corr_matrix.iloc[i, j]

                if pd.notna(correlation) and abs(correlation) > corr_threshold:
                    edges.append({
                        "source": bssid1,
                        "target": bssid2,
                        "correlation": correlation
                    })
        self._cached_edges = list(edges)
        self._cached_threshold = corr_threshold
        self._last_corr_calc_ts = time.time()
        self._is_dirty = False
        return edges

# ... (main block remains the same) ...
