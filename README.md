# Wi-Fi 3D Signal Visualizer

Real-time 3D visualization of nearby Wi-Fi (and optionally Bluetooth) signals using Three.js. Signals are rendered as nodes in 3D space where distance from center represents signal strength and edges show correlation between access points.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![License](https://img.shields.io/badge/License-MIT-green)

## Features

- Live scanning of Wi-Fi networks via `iw`
- 3D visualization with Three.js (signal strength mapped to distance/size)
- Connected network highlighted at center
- Correlation edges between co-located access points
- **Beast mode**: advanced analysis engine with RSSI smoothing, zone clustering, drift detection, and replay support
- Optional Bluetooth device scanning
- Replay system for recorded walks (`.ndjson` files)
- Docker support for demo/replay-only mode

## Requirements

- Linux with a wireless network interface
- Python 3.10+
- `iw` and `sudo` access for live Wi-Fi scanning

## Quick Start

```bash
# Clone the repository
git clone https://github.com/<your-username>/wi-fi3d.git
cd wi-fi3d

# Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run the application
python backend/app.py
```

Open http://localhost:5000 in your browser.

### Sudo access for scanning

Live Wi-Fi scanning requires `sudo iw dev <interface> scan`. To allow passwordless scanning, add a sudoers rule:

```bash
echo "$USER ALL=(ALL) NOPASSWD: /usr/sbin/iw" | sudo tee /etc/sudoers.d/iw-scan
```

## Docker (replay-only)

For demo purposes without Wi-Fi hardware access:

```bash
docker compose up --build
```

Opens at http://localhost:8080. Use the replay controls to play back recorded walk data.

## Modes

| Mode | Description |
|------|-------------|
| **Wi-Fi** | Basic scanning and visualization |
| **Beast** | Advanced analysis with smoothing, correlation scoring, zone clustering, and drift detection |
| **Bluetooth** | Bluetooth LE device scanning (requires `bluetoothctl`) |

## Project Structure

```
wi-fi3d/
├── backend/
│   ├── app.py              # Flask API server
│   ├── scanner.py           # Wi-Fi/Bluetooth scanning (iw, bluetoothctl)
│   ├── analysis.py          # Data store and correlation analysis
│   ├── beast/               # Beast analysis engine
│   │   ├── engine.py        # Main engine with replay support
│   │   ├── timeseries.py    # RSSI smoothing (EWMA + median)
│   │   ├── scoring.py       # Correlation and co-visibility scoring
│   │   ├── clustering.py    # Zone clustering
│   │   ├── change_detection.py  # Drift detection
│   │   ├── models.py        # Data models
│   │   ├── config.py        # Configuration loader
│   │   ├── ingestion.py     # Scan record ingestion
│   │   └── io.py            # Import/export
│   ├── beast_config.json    # Beast config (desktop)
│   ├── beast_config.pi.json # Beast config (Raspberry Pi)
│   ├── replay/              # Replay data files
│   │   └── sample_walk.ndjson
│   └── Dockerfile
├── frontend/
│   ├── index.html           # Main page
│   ├── main.js              # Three.js visualization
│   ├── style.css            # Styles
│   ├── nginx.conf           # Nginx config (Docker)
│   └── Dockerfile
├── docs/                    # Test plans and acceptance criteria
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/capabilities` | Supported modes and features |
| `GET /api/connection?mode=wifi` | Currently connected network |
| `GET /api/data?mode=wifi` | Scan results with nodes and edges |
| `GET /api/replay?action=list` | List available replay files |
| `POST /api/replay?action=start&path=...` | Start replay playback |
| `GET /api/export?format=json` | Export current snapshot |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BEAST_REPLAY_ONLY` | `0` | Set to `1` to disable live scanning |
| `BEAST_CONFIG_PATH` | - | Path to Beast config JSON |
| `BEAST_ENABLE_BLUETOOTH` | `0` | Enable Bluetooth in Beast pipeline |
| `ENABLE_BLUETOOTH_MODE` | `0` | Enable Bluetooth mode option |
| `FLASK_DEBUG` | `0` | Enable Flask debug mode |

## License

MIT
