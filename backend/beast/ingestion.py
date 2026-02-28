from __future__ import annotations

import time
from typing import List

from scanner import scan_wifi, scan_bluetooth
from .models import ScanRecord


class ScanProvider:
    def scan(self) -> List[ScanRecord]:
        raise NotImplementedError


class WifiScanProvider(ScanProvider):
    def scan(self) -> List[ScanRecord]:
        ts = time.time()
        rows = []
        for ap in scan_wifi():
            bssid = ap.get("bssid")
            rssi = ap.get("rssi")
            if not bssid or rssi is None:
                continue
            rows.append(
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
        return rows


class BleScanProvider(ScanProvider):
    def scan(self) -> List[ScanRecord]:
        ts = time.time()
        rows = []
        for dev in scan_bluetooth():
            sid = dev.get("bssid")
            rssi = dev.get("rssi")
            if not sid or rssi is None:
                continue
            rows.append(
                ScanRecord(
                    ts=ts,
                    source_id=str(sid).upper(),
                    source_type="bluetooth",
                    rssi=float(rssi),
                    name=dev.get("ssid", ""),
                    band=dev.get("band", "Bluetooth"),
                )
            )
        return rows
