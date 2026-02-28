# Beast Mode Test Plan

## Phase 1 - Ingestion and Contracts
Done criteria:
1. `ScanRecord` contract used across Wi-Fi/BLE ingestion paths.
2. `/api/data?mode=beast` returns a GraphSnapshot shape (`nodes`, `edges`, `zones`, `metrics`, `events`).
3. Wi-Fi/BLE remain functional in original modes.

Checks:
1. `curl '/api/data?mode=beast'` returns valid JSON with `mode=beast`.
2. Spot-check records include both Wi-Fi and BLE source types.

## Phase 2 - Time Series / Logging / Replay
Done criteria:
1. Fixed timestep store active (`timestep_seconds` in config).
2. Distinguishes `not seen` vs `no scan` via sample flags.
3. NDJSON logging writes live batches.
4. Replay endpoint supports start/stop/seek.

Checks:
1. Confirm `backend/replay/live_capture.ndjson` grows during live run.
2. `curl '/api/replay?action=start&path=backend/replay/sample_walk.ndjson'` returns `ok=true`.
3. `curl '/api/replay?action=seek&index=3'` updates cursor.

## Phase 3 - Edge Scoring / Robustness
Done criteria:
1. Pearson + Spearman + lagged cross-corr are computed.
2. Co-visibility score contributes to edge score.
3. Confidence score present per edge.
4. Hysteresis add/remove thresholds active.

Checks:
1. Inspect `/api/data?mode=beast` edges include `score`, `confidence`, `pearson`, `spearman`, `cross_corr`, `co_visibility`.
2. Observe edges not toggling rapidly at threshold boundary.

## Phase 4 - Zones and Change Detection
Done criteria:
1. Zone clustering produces stable zone IDs.
2. Zone lock requires configured stability ticks.
3. Added/removed/moved events emitted.
4. Drift score updated per node.

Checks:
1. Verify `zones[].locked` transitions from false to true after stable ticks.
2. Verify `events` list includes expected change types.
3. Verify node-level `drift_score` is non-zero after topology changes.

## Phase 5 - API + Frontend Integration
Done criteria:
1. Frontend shows weighted/confident edges for Beast mode.
2. Frontend shows zones legend and recent events.
3. Replay controls wired to `/api/replay`.
4. Export endpoint supports JSON and CSV.

Checks:
1. UI mode switch to `Beast mode` updates zones/events panes.
2. `curl '/api/export?format=json'` returns snapshot payload.
3. `curl '/api/export?format=csv'` returns file path and file exists.
