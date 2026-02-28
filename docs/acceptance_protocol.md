# Acceptance Protocol - Building Topology Reconstruction

## Goal
Reconstruct practical building topology (zones/rooms/floors/attachment groups) from RSSI variation over time while the observer moves.

## Required Inputs
- Replay log: `backend/replay/*.ndjson`
- Ground truth node map: `docs/ground_truth_nodes.csv`
- Route protocol: `docs/test_routes.md`

## KPI Metrics
- `zone_purity`: majority-label consistency inside each inferred zone.
- `zone_coverage`: fraction of known ground-truth nodes present in final graph.
- `floor_consistency`: majority floor consistency inside each inferred zone.
- `stable_node_recall`: fraction of stable anchors present and fresh.
- `avg_edge_churn`: average edge-set volatility across snapshots (lower is better).
- `deterministic_replay`: same replay yields same canonical graph.

Thresholds are configured in `backend/beast_config.json` under `validation`.

## Commands
Run determinism check:

```bash
.venv/bin/python backend/determinism_check.py --path backend/replay/sample_walk.ndjson
```

Run acceptance evaluation:

```bash
.venv/bin/python backend/acceptance_eval.py \
  --ground-truth docs/ground_truth_nodes.csv \
  --replay backend/replay/sample_walk.ndjson
```

Run 3x acceptance suite (writes consolidated JSON report):

```bash
.venv/bin/python backend/acceptance_runner.py \
  --runs 3 \
  --ground-truth docs/ground_truth_nodes.csv \
  --replay backend/replay/sample_walk.ndjson \
  --out docs/acceptance_report.json
```

Generate a ground-truth template from replay (first pass):

```bash
.venv/bin/python backend/ground_truth_seed.py \
  --replay backend/replay/sample_walk.ndjson \
  --out docs/ground_truth_nodes.generated.csv
```

Merge existing labels while adding latest seen nodes from replay:

```bash
.venv/bin/python backend/ground_truth_seed.py \
  --replay backend/replay/live_capture.ndjson \
  --merge-existing docs/ground_truth_nodes.csv \
  --out docs/ground_truth_nodes.generated.csv \
  --limit 80
```

## Done Criteria
1. All KPI checks pass in one run.
2. Same checks pass in 3 repeated runs per route profile.
3. Change events are confirmed during Route R3 (added/removed/moved/cluster_changed).

Use `docs/acceptance_report.json` as the evidence artifact for the 3-run criterion.

For multi-route evidence in one shot:

```bash
.venv/bin/python backend/acceptance_matrix.py \
  --replay-glob "backend/replay/*.ndjson" \
  --ground-truth docs/ground_truth_nodes.csv \
  --runs 3 \
  --out-json docs/acceptance_matrix.json \
  --out-md docs/acceptance_matrix.md
```
