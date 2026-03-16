#!/usr/bin/python3
"""
WiFi management for InkSlab via NetworkManager (nmcli).
No third-party dependencies — uses only subprocess.
Designed for Raspberry Pi OS Bookworm with NetworkManager as the default backend.
"""

import os
import subprocess
import logging
import time

logger = logging.getLogger(__name__)

HOTSPOT_CON_NAME = "InkSlab-Setup"
HOTSPOT_SSID = "InkSlab-Setup"
AP_IP = "10.42.0.1"
PORTAL_DNS_DIR = "/etc/NetworkManager/dnsmasq-shared.d"
PORTAL_DNS_CONF = os.path.join(PORTAL_DNS_DIR, "inkslab-portal.conf")
PORTAL_DNS_CONTENT = "address=/#/10.42.0.1\n"


def _run_nmcli(args, timeout=30):
    """Run an nmcli command and return (returncode, stdout, stderr)."""
    cmd = ["nmcli"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        logger.error("nmcli timed out: %s", " ".join(cmd))
        return -1, "", "timeout"
    except Exception as e:
        logger.error("nmcli failed: %s", e)
        return -1, "", str(e)


def _split_nmcli_escaped(line):
    """Split a line from nmcli -t -e yes output, handling \\: escaped colons in SSIDs."""
    parts = []
    current = []
    i = 0
    while i < len(line):
        if line[i] == '\\' and i + 1 < len(line) and line[i + 1] == ':':
            current.append(':')
            i += 2
        elif line[i] == ':':
            parts.append(''.join(current))
            current = []
            i += 1
        else:
            current.append(line[i])
            i += 1
    parts.append(''.join(current))
    return parts


def _has_real_ip():
    """Fallback check: does the Pi have a non-hotspot, non-loopback IP?"""
    try:
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        for ip in result.stdout.strip().split():
            if ip and not ip.startswith("127.") and not ip.startswith("10.42."):
                return True
    except Exception:
        pass
    return False


def is_wifi_connected():
    """Check if wlan0 has an active non-hotspot WiFi connection.
    Falls back to IP address check if nmcli fails."""
    rc, out, _ = _run_nmcli(["-t", "-e", "yes", "-f", "TYPE,NAME,DEVICE", "con", "show", "--active"])
    if rc != 0:
        # nmcli failed — fall back to IP check so we don't falsely
        # enter setup mode and tear down an existing connection
        return _has_real_ip()
    for line in out.splitlines():
        parts = _split_nmcli_escaped(line)
        if len(parts) >= 3:
            conn_type, name, device = parts[0], parts[1], parts[2]
            # 802-11-wireless is WiFi; ignore our hotspot
            if "wireless" in conn_type and device == "wlan0" and name != HOTSPOT_CON_NAME:
                return True
    # nmcli says no WiFi, but double-check with IP as safety net
    return _has_real_ip()


def has_saved_wifi_profile():
    """Check if there's ANY saved WiFi connection profile (even if not currently active).
    This distinguishes 'first boot with no WiFi ever configured' from
    'WiFi is configured but temporarily down'."""
    rc, out, _ = _run_nmcli(["-t", "-e", "yes", "-f", "TYPE,NAME", "con", "show"])
    if rc != 0:
        # nmcli not working — assume WiFi is configured (safe default)
        return True
    for line in out.splitlines():
        parts = _split_nmcli_escaped(line)
        if len(parts) >= 2:
            conn_type, name = parts[0], parts[1]
            if "wireless" in conn_type and name != HOTSPOT_CON_NAME:
                return True
    return False


def get_active_ssid():
    """Return the SSID of the currently connected WiFi network, or None."""
    rc, out, _ = _run_nmcli(["-t", "-e", "yes", "-f", "ACTIVE,SSID", "dev", "wifi"])
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = _split_nmcli_escaped(line)
        if len(parts) >= 2 and parts[0] == "yes":
            ssid = parts[1]
            if ssid and ssid != HOTSPOT_SSID:
                return ssid
    return None


def get_signal_strength():
    """Return the signal strength (0-100) of the active WiFi connection, or None."""
    rc, out, _ = _run_nmcli(["-t", "-e", "yes", "-f", "IN-USE,SIGNAL", "dev", "wifi", "list"])
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = _split_nmcli_escaped(line)
        if len(parts) >= 2 and parts[0] == "*":
            try:
                return int(parts[1])
            except (ValueError, IndexError):
                pass
    return None


def get_local_ip():
    """Get the Pi's local IP address (non-hotspot)."""
    try:
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        parts = result.stdout.strip().split()
        # Filter out the hotspot IP
        for ip in parts:
            if ip != AP_IP and not ip.startswith("10.42."):
                return ip
        return parts[0] if parts else None
    except Exception:
        return None


def scan_networks():
    """Scan for available WiFi networks.
    Returns list of dicts sorted by signal strength descending."""
    # Force a rescan
    _run_nmcli(["dev", "wifi", "rescan"], timeout=10)
    time.sleep(1)

    rc, out, _ = _run_nmcli(["-t", "-e", "yes", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "dev", "wifi", "list"])
    if rc != 0:
        return []

    seen = {}
    for line in out.splitlines():
        parts = _split_nmcli_escaped(line)
        if len(parts) < 4:
            continue
        ssid = parts[0].strip()
        if not ssid or ssid == HOTSPOT_SSID or ssid == "--":
            continue
        try:
            signal = int(parts[1])
        except (ValueError, IndexError):
            signal = 0
        security = parts[2].strip() if len(parts) > 2 else ""
        active = parts[3].strip() == "*" if len(parts) > 3 else False

        # Keep the strongest signal for each SSID
        if ssid not in seen or signal > seen[ssid]["signal"]:
            seen[ssid] = {
                "ssid": ssid,
                "signal": signal,
                "security": security,
                "active": active,
            }

    networks = sorted(seen.values(), key=lambda x: x["signal"], reverse=True)
    return networks


def ensure_portal_dns():
    """Install captive portal DNS redirect config for NetworkManager's dnsmasq.
    Makes all DNS queries resolve to the AP IP when in hotspot mode."""
    try:
        if not os.path.isdir(PORTAL_DNS_DIR):
            os.makedirs(PORTAL_DNS_DIR, exist_ok=True)

        # Check if already correct
        if os.path.exists(PORTAL_DNS_CONF):
            with open(PORTAL_DNS_CONF, "r") as f:
                if f.read().strip() == PORTAL_DNS_CONTENT.strip():
                    return True

        with open(PORTAL_DNS_CONF, "w") as f:
            f.write(PORTAL_DNS_CONTENT)
        logger.info("Installed captive portal DNS config: %s", PORTAL_DNS_CONF)
        return True
    except Exception as e:
        logger.error("Failed to install portal DNS config: %s", e)
        return False


def start_hotspot():
    """Create and activate the setup hotspot (open network, no password).
    Returns True on success."""
    # Install captive portal DNS config
    ensure_portal_dns()

    # Clean up any existing hotspot profile first
    rc, out, _ = _run_nmcli(["-t", "-e", "yes", "-f", "NAME", "con", "show"])
    if rc == 0 and HOTSPOT_CON_NAME in [_split_nmcli_escaped(l)[0] for l in out.splitlines() if l]:
        _run_nmcli(["con", "down", HOTSPOT_CON_NAME])
        _run_nmcli(["con", "delete", HOTSPOT_CON_NAME])
        time.sleep(1)

    # Create an open AP directly (no password dance needed)
    rc, _, err = _run_nmcli([
        "con", "add", "type", "wifi",
        "ifname", "wlan0",
        "con-name", HOTSPOT_CON_NAME,
        "ssid", HOTSPOT_SSID,
        "802-11-wireless.mode", "ap",
        "802-11-wireless.band", "bg",
        "802-11-wireless.channel", "6",
        "ipv4.method", "shared",
    ])
    if rc != 0:
        logger.error("Failed to create hotspot profile: %s", err)
        return False

    rc2, _, err2 = _run_nmcli(["con", "up", HOTSPOT_CON_NAME])
    if rc2 == 0:
        logger.info("Hotspot '%s' created (open network)", HOTSPOT_SSID)
        return True

    logger.error("Failed to activate hotspot: %s", err2)
    return False


def stop_hotspot():
    """Tear down the setup hotspot. Returns True on success."""
    rc1, _, _ = _run_nmcli(["con", "down", HOTSPOT_CON_NAME])
    rc2, _, _ = _run_nmcli(["con", "delete", HOTSPOT_CON_NAME])
    if rc1 == 0 or rc2 == 0:
        logger.info("Hotspot '%s' stopped", HOTSPOT_SSID)
    return True  # Succeed silently even if hotspot didn't exist


def connect_to_network(ssid, password):
    """Attempt to connect to a WiFi network.
    Returns (success: bool, message: str). Message is either the IP or an error."""
    # Rescan so nmcli can see available networks after hotspot teardown
    logger.info("Scanning for networks before connection attempt...")
    _run_nmcli(["dev", "wifi", "rescan"], timeout=10)
    time.sleep(3)

    # Build connection command
    args = ["dev", "wifi", "connect", ssid, "ifname", "wlan0"]
    if password:
        args.extend(["password", password])

    rc, out, err = _run_nmcli(args, timeout=45)
    if rc != 0:
        error_msg = err or out or "Connection failed"
        # Clean up common nmcli error messages
        if "Secrets were required" in error_msg or "No suitable" in error_msg:
            error_msg = "Wrong password or network not found"
        elif "timeout" in error_msg.lower():
            error_msg = "Connection timed out"
        logger.error("WiFi connection failed: %s", error_msg)
        # Clean up any profile created by the failed attempt
        _run_nmcli(["con", "delete", "id", ssid], timeout=10)
        return False, error_msg

    # Wait for an IP address (up to 20 seconds)
    for _ in range(20):
        time.sleep(1)
        ip = get_local_ip()
        if ip:
            logger.info("Connected to '%s' with IP %s", ssid, ip)
            return True, ip

    # Connected but no IP
    logger.warning("Connected to '%s' but no IP obtained", ssid)
    return True, "connected (no IP yet)"


def get_wifi_status():
    """Return current WiFi state as a dict."""
    connected = is_wifi_connected()
    ssid = get_active_ssid() if connected else None
    ip = get_local_ip() if connected else None

    # Check if hotspot is active
    hotspot_active = False
    rc, out, _ = _run_nmcli(["-t", "-e", "yes", "-f", "NAME", "con", "show", "--active"])
    if rc == 0:
        hotspot_active = HOTSPOT_CON_NAME in [_split_nmcli_escaped(l)[0] for l in out.splitlines() if l]

    return {
        "connected": connected,
        "ssid": ssid,
        "ip": ip,
        "hotspot_active": hotspot_active,
        "hotspot_ssid": HOTSPOT_SSID if hotspot_active else None,
        "hotspot_ip": AP_IP if hotspot_active else None,
    }
