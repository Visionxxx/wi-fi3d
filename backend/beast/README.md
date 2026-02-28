# Beast Mode Modules

- `models.py`: Shared contracts (`ScanRecord`, `GraphSnapshot`, node/edge/zone/event dataclasses).
- `config.py`: Loads `backend/beast_config.json`.
- `timeseries.py`: Fixed timestep store, freshness/grace handling, and smoothing prep.
- `scoring.py`: Pearson/Spearman/lagged cross-correlation, co-visibility, confidence, hysteresis.
- `clustering.py`: Zone clustering (thresholded weighted component alternative).
- `change_detection.py`: Added/removed/moved detection + drift score history.
- `io.py`: NDJSON logging and replay reader.
- `engine.py`: Orchestration for live ingest, replay control, snapshot and export.
