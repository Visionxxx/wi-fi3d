import subprocess
import re

def find_wifi_interface():
    """Finds a wireless network interface name."""
    try:
        # Use 'iw dev' to list interfaces and find one that looks like a wifi card
        process = subprocess.run(["iw", "dev"], capture_output=True, text=True, check=True)
        # Look for a line starting with 'phy#' and get the interface name from the next line
        lines = process.stdout.split('\n')
        for i, line in enumerate(lines):
            if line.strip().startswith('phy#'):
                if i + 1 < len(lines):
                    iface_line = lines[i+1]
                    if 'Interface' in iface_line:
                        return iface_line.strip().split(' ')[1]
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"Could not automatically find a Wi-Fi interface using 'iw dev': {e}")
    
    try:
        # Fallback to 'ip link'
        process = subprocess.run(["ip", "link"], capture_output=True, text=True, check=True)
        for line in process.stdout.split('\n'):
            # e.g., "3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> ..."
            match = re.match(r"^\d+:\s*([^:]+):", line.strip())
            if not match:
                continue
            iface = match.group(1).strip()
            if iface.startswith(("wl", "wlan", "wifi")):
                return iface
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
         print(f"Could not automatically find a Wi-Fi interface using 'ip link': {e}")

    return None


def parse_iw_output(output):
    """Parses the output of 'iw dev <interface> scan'."""
    aps = []
    current_ap = {}
    
    # Regex patterns for the data we want to extract
    bssid_re = re.compile(r"BSS (([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})\(on")
    ssid_re = re.compile(r"\tSSID: (.*)")
    rssi_re = re.compile(r"\tsignal: (-?\d+\.\d{2}) dBm")
    # More robust channel parsing based on common 'iw' output formats
    ds_channel_re = re.compile(r"DS Parameter set: channel (\d+)")
    ht_channel_re = re.compile(r"HT Operation:.*primary channel: (\d+)")
    freq_re = re.compile(r"\tfreq: (\d+)")

    for line in output.split('\n'):
        if line.startswith("BSS"):
            if current_ap.get("bssid"):
                aps.append(current_ap)
            current_ap = {}
            match = bssid_re.search(line)
            if match:
                current_ap["bssid"] = match.group(1)
        else:
            ssid_match = ssid_re.search(line)
            rssi_match = rssi_re.search(line)
            ds_channel_match = ds_channel_re.search(line)
            ht_channel_match = ht_channel_re.search(line)
            freq_match = freq_re.search(line)

            if ssid_match and not current_ap.get("ssid"):
                ssid = ssid_match.group(1).strip()
                if ssid and '\\x00' not in ssid: # Filter out malformed/hidden SSIDs
                    current_ap["ssid"] = ssid

            if rssi_match:
                current_ap["rssi"] = float(rssi_match.group(1))
            
            # Set channel, preferring more specific HT/DS sets.
            if ht_channel_match:
                current_ap["channel"] = int(ht_channel_match.group(1))
            elif ds_channel_match and not current_ap.get("channel"):
                current_ap["channel"] = int(ds_channel_match.group(1))

            if freq_match and not current_ap.get("band"):
                freq = int(freq_match.group(1))
                if freq > 5000:
                    current_ap["band"] = "5 GHz"
                else:
                    current_ap["band"] = "2.4 GHz"

    if current_ap.get("bssid"):
        aps.append(current_ap)
        
    return aps

def scan_wifi(interface=None, timeout_seconds=10):
    """
    Scans for Wi-Fi networks using the 'iw' command and returns a list of APs.
    
    Note: This function requires 'iw' to be installed and run with sufficient
    permissions to perform a scan (often requires sudo).
    """
    if interface is None:
        print("No interface provided, attempting to find one...")
        interface = find_wifi_interface()
        if not interface:
            print("Error: Could not find a wireless interface. Please specify one.")
            return []
        print(f"Found interface: {interface}")

    try:
        # Using `sudo -n` (non-interactive) to prevent hanging if a password is required.
        command = ["sudo", "-n", "iw", "dev", interface, "scan"]
        print(f"Running command: {' '.join(command)}")
        process = subprocess.run(
            command, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
        
        if process.returncode != 0:
            print(f"Error scanning Wi-Fi on interface '{interface}': {process.stderr.strip()}")
            return []

        return parse_iw_output(process.stdout)
    except subprocess.TimeoutExpired:
        print(f"Wi-Fi scan timed out on interface '{interface}' after {timeout_seconds}s.")
        return []
    except FileNotFoundError:
        print("Error: 'sudo' or 'iw' command not found. Please ensure 'iw' is installed and you can run 'sudo'.")
        return []
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return []


def parse_bluetoothctl_output(output):
    """Parses bluetoothctl scan output and returns devices with latest RSSI."""
    devices = {}

    # Examples:
    # [NEW] Device AA:BB:CC:DD:EE:FF My Device
    # [CHG] Device AA:BB:CC:DD:EE:FF RSSI: -71
    device_line_re = re.compile(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)$")
    rssi_line_re = re.compile(r"Device\s+([0-9A-Fa-f:]{17})\s+RSSI:\s*(-?\d+)")

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if "Device" not in line:
            continue

        rssi_match = rssi_line_re.search(line)
        if rssi_match:
            addr = rssi_match.group(1).upper()
            rssi = float(rssi_match.group(2))
            if addr not in devices:
                devices[addr] = {
                    "bssid": addr,  # Reusing frontend id field.
                    "ssid": "Unknown BLE device",
                    "band": "Bluetooth",
                    "protocol": "bluetooth",
                    "rssi": rssi,
                }
            else:
                devices[addr]["rssi"] = rssi
            continue

        device_match = device_line_re.search(line)
        if not device_match:
            continue

        addr = device_match.group(1).upper()
        tail = device_match.group(2).strip()
        if not tail:
            continue

        # Ignore change payloads that are not a human-friendly name.
        ignored_prefixes = (
            "RSSI:",
            "TxPower:",
            "ManufacturerData",
            "ServiceData",
            "UUIDs:",
            "Alias:",
            "Name:",
            "Advertising",
            "Connected:",
            "ServicesResolved:",
            "Appearance:",
            "Paired:",
            "Trusted:",
            "Blocked:",
        )
        if tail.startswith(ignored_prefixes):
            continue

        device_name = tail
        if addr not in devices:
            devices[addr] = {
                "bssid": addr,  # Reusing frontend id field.
                "ssid": device_name,
                "band": "Bluetooth",
                "protocol": "bluetooth",
            }
        else:
            devices[addr]["ssid"] = device_name

    # Keep only devices that have RSSI samples for visualization.
    return [device for device in devices.values() if "rssi" in device]


def scan_bluetooth(timeout_seconds=8):
    """
    Scans for nearby Bluetooth devices using bluetoothctl and returns a list
    compatible with the Wi-Fi data model.
    """
    try:
        command = ["bluetoothctl", "--timeout", str(timeout_seconds), "scan", "on"]
        print(f"Running command: {' '.join(command)}")
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds + 3,
        )

        if process.returncode != 0:
            print(f"Error scanning Bluetooth: {process.stderr.strip()}")
            return []

        return parse_bluetoothctl_output(process.stdout)
    except subprocess.TimeoutExpired:
        print(f"Bluetooth scan timed out after {timeout_seconds + 3}s.")
        return []
    except FileNotFoundError:
        print("Error: 'bluetoothctl' command not found. Please install bluez tools.")
        return []
    except Exception as e:
        print(f"An unexpected Bluetooth scan error occurred: {e}")
        return []

if __name__ == '__main__':
    # For direct testing of the scanner
    print("--- Wi-Fi Scanner Test ---")
    networks = scan_wifi()
    if networks:
        print(f"\nFound {len(networks)} networks:")
        # Sort by RSSI for readability
        for ap in sorted(networks, key=lambda x: x.get('rssi', -100), reverse=True):
            print(
                f"  SSID: {ap.get('ssid', '[hidden]'):<20} "
                f"BSSID: {ap['bssid']:<17} "
                f"RSSI: {ap.get('rssi', 'N/A'):<6} dBm "
                f"Channel: {ap.get('channel', 'N/A'):<3} "
                f"Band: {ap.get('band', 'N/A')}"
            )
    else:
        print("\nNo networks found or an error occurred.")
        print("Troubleshooting:")
        print("- Is 'iw' installed? (e.g., 'sudo apt-get install iw')")
        print("- Are you running this with a user that has 'sudo' privileges?")
        print("- Is your Wi-Fi adapter enabled?")
