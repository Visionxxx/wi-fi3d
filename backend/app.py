from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from scanner import scan_wifi, scan_bluetooth, find_wifi_interface
from analysis import WifiDataStore
from beast import BeastEngine
from beast.models import ScanRecord
import os
import glob
import subprocess
import re
import threading
import time

# --- Setup ---
project_root = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.dirname(project_root)
replay_root = os.path.join(project_root, "replay")
frontend_folder = os.path.join(repo_root, 'frontend')

app = Flask(__name__, static_folder=frontend_folder, static_url_path='')
CORS(app)

MODE_WIFI = "wifi"
MODE_BLUETOOTH = "bluetooth"
MODE_BEAST = "beast"
REPLAY_ONLY = os.getenv("BEAST_REPLAY_ONLY", "0") == "1"
BEAST_ENABLE_BLUETOOTH = os.getenv("BEAST_ENABLE_BLUETOOTH", "0") == "1"
ENABLE_BLUETOOTH_MODE = os.getenv("ENABLE_BLUETOOTH_MODE", "0") == "1"
SUPPORTED_MODES = {MODE_WIFI, MODE_BEAST} | ({MODE_BLUETOOTH} if ENABLE_BLUETOOTH_MODE else set())
BEAST_PIPELINES = [MODE_WIFI] + ([MODE_BLUETOOTH] if BEAST_ENABLE_BLUETOOTH else [])
SCANNED_MODES = set() if REPLAY_ONLY else ({MODE_WIFI} | ({MODE_BLUETOOTH} if ENABLE_BLUETOOTH_MODE else set()))

datastores = {
    MODE_WIFI: WifiDataStore(),
    MODE_BLUETOOTH: WifiDataStore(),
}
beast_engine = BeastEngine(config_path=os.getenv("BEAST_CONFIG_PATH"))

SCAN_INTERVAL_SECONDS = {
    MODE_WIFI: 5,
    MODE_BLUETOOTH: 8,
}

scan_state = {
    MODE_WIFI: {"results": [], "timestamp": 0.0},
    MODE_BLUETOOTH: {"results": [], "timestamp": 0.0},
}
last_ingested_scan_ts = {
    MODE_WIFI: 0.0,
    MODE_BLUETOOTH: 0.0,
}
scan_lock = threading.Lock()
thread_start_lock = threading.Lock()
scan_threads_started = False
scanner_threads = {}


def get_current_wifi_connection():
    """Returns currently connected Wi-Fi BSSID/SSID when available."""
    bssid = None
    ssid = None

    try:
        interface = find_wifi_interface()
        if interface:
            output = subprocess.check_output(["iw", "dev", interface, "link"], text=True).strip()
            bssid_match = re.search(r"Connected to ([0-9a-fA-F:]{17})", output)
            ssid_match = re.search(r"SSID:\s*(.+)", output)
            if bssid_match:
                bssid = bssid_match.group(1).upper()
            if ssid_match:
                ssid = ssid_match.group(1).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Fallback if iw link did not provide SSID.
    if not ssid:
        try:
            ssid = subprocess.check_output(["iwgetid", "-r"], text=True).strip() or None
        except (subprocess.CalledProcessError, FileNotFoundError):
            ssid = None

    return bssid, ssid


def scan_once(mode):
    if mode == MODE_BLUETOOTH:
        return scan_bluetooth()
    return scan_wifi()


def scanner_worker(mode):
    """Continuously scans the selected mode in the background."""
    interval = SCAN_INTERVAL_SECONDS.get(mode, 5)
    while True:
        started = time.time()
        try:
            results = scan_once(mode)
        except Exception as e:
            print(f"[scanner:{mode}] Unexpected scan error: {e}")
            results = []

        with scan_lock:
            scan_state[mode]["results"] = results
            scan_state[mode]["timestamp"] = time.time()

        elapsed = time.time() - started
        sleep_for = max(0.2, interval - elapsed)
        time.sleep(sleep_for)


def start_scanner_threads_once():
    global scan_threads_started
    with thread_start_lock:
        if scan_threads_started:
            return
        if REPLAY_ONLY:
            scan_threads_started = True
            print("Replay-only mode enabled. Scanner threads are disabled.")
            return

        for mode in SCANNED_MODES:
            thread = threading.Thread(target=scanner_worker, args=(mode,), daemon=True, name=f"scanner-{mode}")
            thread.start()
            scanner_threads[mode] = thread

        scan_threads_started = True
        print("Background scanner threads started.")

# --- Routes ---
@app.route('/')
def index():
    """Serves the main index.html file of the frontend application."""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/api/capabilities')
def capabilities():
    return jsonify(
        {
            "supported_modes": sorted(list(SUPPORTED_MODES)),
            "beast_pipelines": list(BEAST_PIPELINES),
            "replay_only": REPLAY_ONLY,
            "bluetooth_enabled": ENABLE_BLUETOOTH_MODE,
            "beast_bluetooth_enabled": BEAST_ENABLE_BLUETOOTH,
        }
    )

@app.route('/api/connection')
def get_connection():
    """
    Gets identifier of the currently connected network for the requested mode.
    """
    mode = request.args.get("mode", MODE_WIFI).lower()
    if mode not in SUPPORTED_MODES:
        return jsonify({"error": f"Unsupported mode '{mode}'"}), 400

    if mode == MODE_BLUETOOTH:
        # Generic Bluetooth "connected device" is not represented as one stable id.
        return jsonify({"connected_bssid": None, "connected_ssid": None})
    if mode == MODE_BEAST:
        bssid, ssid = get_current_wifi_connection()
        return jsonify({"connected_bssid": bssid, "connected_ssid": ssid})

    bssid, ssid = get_current_wifi_connection()
    if bssid or ssid:
        print(f"Current Wi-Fi connection: BSSID={bssid}, SSID={ssid}")
    else:
        print("Could not determine current Wi-Fi connection.")
    return jsonify({"connected_bssid": bssid, "connected_ssid": ssid})

@app.route('/api/data')
def get_wifi_data():
    """
    Scans data for selected mode, updates mode-specific data store,
    calculates correlations, and returns graph nodes/edges.
    """
    mode = request.args.get("mode", MODE_WIFI).lower()
    if mode not in SUPPORTED_MODES:
        return jsonify({"error": f"Unsupported mode '{mode}'"}), 400

    start_scanner_threads_once()

    if mode == MODE_BEAST:
        print("Received request for /api/data?mode=beast")
        started = time.time()
        for pipeline in BEAST_PIPELINES:
            with scan_lock:
                pipe_results = list(scan_state[pipeline]["results"])
                pipe_ts = scan_state[pipeline]["timestamp"]
            if pipe_ts > last_ingested_scan_ts[pipeline]:
                records = convert_scan_results_to_records(pipeline, pipe_results, pipe_ts)
                beast_engine.ingest_live(pipeline, records, scan_ts=pipe_ts)
                last_ingested_scan_ts[pipeline] = pipe_ts
            else:
                beast_engine.ingest_live(pipeline, [], scan_ts=time.time())

        snapshot = beast_engine.snapshot()
        payload = snapshot.to_dict()
        print(
            f"Processed beast data: {len(payload['nodes'])} nodes, {len(payload['edges'])} edges, "
            f"{len(payload['zones'])} zones in {time.time() - started:.2f}s."
        )
        return jsonify(payload)

    if REPLAY_ONLY:
        return jsonify({"mode": mode, "nodes": [], "edges": []})

    with scan_lock:
        latest_results = list(scan_state[mode]["results"])
        latest_scan_ts = scan_state[mode]["timestamp"]

    datastore = datastores[mode]
    if latest_scan_ts > last_ingested_scan_ts[mode]:
        datastore.add_scan_results(latest_results)
        last_ingested_scan_ts[mode] = latest_scan_ts

    nodes = datastore.get_nodes()
    edges = datastore.calculate_correlations()
    print(f"Processed {mode} data: {len(nodes)} nodes, {len(edges)} edges. Sending JSON response.")
    return jsonify({"mode": mode, "nodes": nodes, "edges": edges})


def convert_scan_results_to_records(pipeline, scan_rows, ts):
    records = []
    if pipeline == MODE_WIFI:
        for ap in scan_rows:
            bssid = ap.get("bssid")
            rssi = ap.get("rssi")
            if not bssid or rssi is None:
                continue
            records.append(
                ScanRecord(
                    ts=ts,
                    source_id=str(bssid).upper(),
                    source_type="wifi",
                    rssi=float(rssi),
                    name=ap.get("ssid", ""),
                    channel=ap.get("channel"),
                    band=ap.get("band", ""),
                )
            )
    else:
        for dev in scan_rows:
            sid = dev.get("bssid")
            rssi = dev.get("rssi")
            if not sid or rssi is None:
                continue
            records.append(
                ScanRecord(
                    ts=ts,
                    source_id=str(sid).upper(),
                    source_type="bluetooth",
                    rssi=float(rssi),
                    name=dev.get("ssid", ""),
                    channel=dev.get("channel"),
                    band=dev.get("band", "Bluetooth"),
                )
            )
    return records


@app.route('/api/replay', methods=['GET', 'POST'])
def replay_control():
    def _normalize_path(payload):
        if isinstance(payload, dict) and payload.get("path"):
            p = str(payload["path"])
            if os.path.isabs(p) and p.startswith(repo_root):
                payload["path"] = os.path.relpath(p, repo_root)
        return payload

    action = request.args.get("action") or request.form.get("action") or "status"
    if action == "list":
        files_abs = sorted(glob.glob(os.path.join(replay_root, "*.ndjson")))
        files = [os.path.relpath(path, repo_root) for path in files_abs]
        return jsonify({"ok": True, "files": files})
    if action == "reset":
        return jsonify(beast_engine.reset())
    if action == "start":
        path = request.args.get("path", "backend/replay/sample_walk.ndjson")
        abs_path = path if os.path.isabs(path) else os.path.abspath(os.path.join(repo_root, path))
        if not abs_path.startswith(replay_root) or not abs_path.endswith(".ndjson"):
            return jsonify({"ok": False, "error": "Invalid replay path"}), 400
        return jsonify(_normalize_path(beast_engine.replay_start(abs_path)))
    if action == "resume":
        return jsonify(_normalize_path(beast_engine.replay_resume()))
    if action == "stop":
        return jsonify(_normalize_path(beast_engine.replay_stop()))
    if action == "seek":
        idx = int(request.args.get("index", "0"))
        return jsonify(_normalize_path(beast_engine.replay_seek(idx)))
    return jsonify(_normalize_path(beast_engine.replay_status()))


@app.route('/api/export')
def export_snapshot():
    fmt = request.args.get("format", "json").lower()
    if fmt not in {"json", "csv"}:
        return jsonify({"ok": False, "error": "format must be json or csv"}), 400
    return jsonify(beast_engine.export_snapshot(fmt))

# --- Main ---
if __name__ == '__main__':
    start_scanner_threads_once()
    print(f"Serving frontend from: {app.static_folder}")
    print(f"Beast pipelines: {', '.join(BEAST_PIPELINES) if BEAST_PIPELINES else 'none'}")
    print("Starting Flask server...")
    print("Access the application at http://127.0.0.1:5000")
    debug_enabled = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host='0.0.0.0', port=5000, debug=debug_enabled, use_reloader=False)
