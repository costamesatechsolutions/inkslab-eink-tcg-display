#!/usr/bin/python3
"""
InkSlab Web Dashboard
https://github.com/costamesatechsolutions/inkslab-eink-tcg-display

A lightweight Flask web UI for managing InkSlab from your phone or browser.
Access via the IP address shown on startup after enabling the systemd service.

By Costa Mesa Tech Solutions (a brand of Pine Heights Ventures LLC)
"""

import datetime
import os
import json
import shutil
import signal
import subprocess
import time
import threading
from flask import Flask, request, jsonify, send_file, redirect, make_response
import wifi_manager
from inkslab_paths import (
    APP_DIR,
    COLLECTION_FILE,
    COLLECTION_TRIGGER,
    CONFIG_FILE,
    DOWNLOAD_LOG,
    LIBRARY_TRIGGER,
    NEXT_TRIGGER,
    PAUSE_FILE,
    PREV_TRIGGER,
    STATUS_FILE,
    UNBOX_TRIGGER,
    UPDATE_LOCK_FILE,
    UPDATE_STATUS_FILE,
    WATCHDOG_SETUP_FLAG,
    WIFI_CONNECTED_TRIGGER,
    WIFI_FAILED_TRIGGER,
    WIFI_SETUP_TRIGGER,
)
from inkslab_plugins import (
    default_enabled_plugins,
    default_display_schedule,
    get_card_libraries,
    get_plugin_payload,
    get_plugins,
    normalize_display_config,
    normalize_enabled_plugins,
    normalize_active_plugin,
)
from inkslab_update_helpers import (
    configure_git_safe_directory,
    git_current_branch,
    git_default_branch,
    git_remote_branches,
    normalize_update_branch,
)
from inkslab_web_state import (
    WEB_DEFAULTS,
    atomic_write_json as _atomic_write_json,
    load_collection as _load_collection_state,
    load_config as _load_config_state,
    manual_control_block_reason as _manual_control_block_reason,
    read_status as _read_status,
    save_collection as _save_collection_state,
    save_config as _save_config_state,
    write_status as _write_status,
)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB upload limit

VERSION = "1.0.0"

SCRIPT_DIR = APP_DIR

TCG_REGISTRY = {
    plugin_id: {
        "name": plugin.name,
        "path": plugin.card_library_path,
        "color": plugin.accent_color,
        "download_script": plugin.download_script,
    }
    for plugin_id, plugin in get_plugins().items()
}
TCG_LIBRARIES = get_card_libraries()

IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.avif')

DEFAULTS = WEB_DEFAULTS

# Track running download process
_download_proc = None
_download_tcg = None
_download_log_fh = None
_download_lock = threading.Lock()

# --- WIFI SETUP MODE ---
_wifi_setup_mode = False
_wifi_connect_result = {"status": "idle"}
_wifi_connect_lock = threading.Lock()

# --- FILE SAFETY ---
_config_lock = threading.Lock()
_collection_lock = threading.Lock()
_custom_lock = threading.Lock()
MIN_FREE_SPACE_MB = 50  # Refuse writes if less than this much free space


def _check_disk_space(path="/home/pi"):
    """Return free space in MB. Returns 0 if check fails."""
    try:
        st = shutil.disk_usage(path)
        return st.free // (1024 * 1024)
    except Exception:
        return 0


def _has_disk_space(path="/home/pi"):
    """Return True if there's enough free space to safely write."""
    return _check_disk_space(path) >= MIN_FREE_SPACE_MB


# --- TTL CACHE (avoids re-walking 15,000+ files on every request) ---
_cache = {}
_cache_lock = threading.Lock()


def _cache_get(key, ttl=30):
    """Return cached value if fresh, else None."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry[1]) < ttl:
            return entry[0]
    return None


def _cache_set(key, value):
    """Store a value in cache with current timestamp."""
    with _cache_lock:
        _cache[key] = (value, time.time())


def _cache_invalidate(*keys):
    """Remove specific keys from cache."""
    with _cache_lock:
        for key in keys:
            _cache.pop(key, None)


def _close_download_log():
    """Close the download log file handle if open."""
    global _download_log_fh
    if _download_log_fh:
        try:
            _download_log_fh.close()
        except Exception:
            pass
        _download_log_fh = None


def _signal_library_changed(tcg):
    """Notify the display daemon when active-library contents changed."""
    if not tcg:
        return
    try:
        config = load_config()
    except Exception:
        config = {}
    if config.get("active_tcg") != tcg:
        return
    try:
        with open(LIBRARY_TRIGGER, 'w') as f:
            f.write(tcg)
    except OSError:
        pass


# --- HELPERS ---

def load_config():
    return _load_config_state(SCRIPT_DIR)


def save_config(config):
    try:
        _save_config_state(config)
    except Exception as e:
        app.logger.error(f"Failed to save config: {e}")


def load_collection():
    return _load_collection_state()


def save_collection(data):
    try:
        _save_collection_state(data)
    except Exception as e:
        app.logger.error(f"Failed to save collection: {e}")
    # Signal daemon that collection changed so it can rebuild its deck
    try:
        with open(COLLECTION_TRIGGER, 'w') as f:
            f.write('1')
    except OSError:
        pass


def _is_card_image(filename):
    """Check if a file is a supported card image."""
    return filename.lower().endswith(IMAGE_EXTENSIONS) and not filename.startswith('_')


def rarity_sort_key(rarity):
    """Sort key for rarities — rarest first."""
    order = {
        "special": 1, "mythic rare": 2, "bonus": 3,
        "hyper rare": 1, "special illustration rare": 2, "rare secret": 3,
        "rare rainbow": 4, "illustration rare": 5, "shiny ultra rare": 6,
        "rare ultra": 7, "ultra rare": 7, "double rare": 8, "ace spec rare": 8,
        "rare holo vstar": 9, "rare holo vmax": 9, "rare holo v": 10,
        "rare holo gx": 10, "rare holo ex": 10, "shiny rare": 11,
        "rare holo": 12, "rare prism star": 12, "rare": 15,
        "uncommon": 20, "common": 30, "promo": 25,
        # Lorcana rarities
        "enchanted": 1, "legendary": 5, "super rare": 8,
    }
    return order.get(rarity.lower().strip(), 15)


def get_local_ip():
    try:
        result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5)
        parts = result.stdout.strip().split()
        # Filter out hotspot IP (10.42.*) to return the real LAN address
        for ip in parts:
            if not ip.startswith("10.42."):
                return ip
        return parts[0] if parts else None
    except Exception:
        return None


# --- API ROUTES ---

@app.route('/api/status')
def api_status():
    status = _read_status()
    # Auto-clear stale flags (e.g. if daemon crashed mid-update)
    if status.get('pending') and time.time() - status.get('timestamp', 0) > 60:
        status.pop('pending', None)
    if status.get('display_updating') and time.time() - status.get('timestamp', 0) > 60:
        status.pop('display_updating', None)
    config = load_config()
    tz_name = config.get("timezone_name")
    tz_offset = config.get("timezone_offset")
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            status['server_time'] = datetime.datetime.now(ZoneInfo(tz_name)).strftime("%-I:%M %p")
        except Exception:
            status['server_time'] = time.strftime("%-I:%M %p")
    elif tz_offset is not None:
        try:
            utc_now = datetime.datetime.now(datetime.timezone.utc)
            adjusted = utc_now + datetime.timedelta(hours=int(tz_offset))
            status['server_time'] = adjusted.strftime("%-I:%M %p")
        except (ValueError, TypeError):
            status['server_time'] = time.strftime("%-I:%M %p")
    else:
        status['server_time'] = time.strftime("%-I:%M %p")
    return jsonify(status)


@app.route('/api/config', methods=['GET'])
def api_get_config():
    return jsonify(load_config())


@app.route('/api/config', methods=['POST'])
def api_set_config():
    updates = request.get_json(force=True)
    if not isinstance(updates, dict):
        return jsonify({"error": "invalid request"}), 400
    if 'active_plugin' in updates and 'active_tcg' not in updates:
        updates['active_tcg'] = updates['active_plugin']
    if 'single_plugin' in updates and 'active_tcg' not in updates:
        updates['active_tcg'] = updates['single_plugin']
    if 'update_branch' in updates:
        updates['update_branch'] = normalize_update_branch(SCRIPT_DIR, updates['update_branch'])
    # Validate values to prevent bad config from crashing the display daemon
    try:
        if 'day_interval' in updates:
            updates['day_interval'] = max(60, min(86400, int(updates['day_interval'])))
        if 'night_interval' in updates:
            updates['night_interval'] = max(60, min(86400, int(updates['night_interval'])))
        if 'day_start' in updates:
            updates['day_start'] = max(0, min(23, int(updates['day_start'])))
        if 'day_end' in updates:
            updates['day_end'] = max(0, min(23, int(updates['day_end'])))
        if 'color_saturation' in updates:
            updates['color_saturation'] = max(0.5, min(5.0, float(updates['color_saturation'])))
        if 'timezone_offset' in updates:
            if updates['timezone_offset'] is not None:
                updates['timezone_offset'] = max(-12, min(14, int(updates['timezone_offset'])))
        if 'timezone_name' in updates:
            tn = updates['timezone_name']
            if tn is not None:
                # Validate timezone name exists
                try:
                    from zoneinfo import ZoneInfo
                    ZoneInfo(str(tn))
                    updates['timezone_name'] = str(tn)
                except Exception:
                    updates['timezone_name'] = None
        if 'rotation_angle' in updates:
            updates['rotation_angle'] = int(updates['rotation_angle']) if int(updates['rotation_angle']) in (90, 270) else 270
    except (ValueError, TypeError):
        return jsonify({"error": "invalid value"}), 400
    if 'active_tcg' in updates:
        blocked = _manual_control_block_reason()
        if blocked:
            return jsonify({"error": blocked}), 409
        updates['active_tcg'] = normalize_active_plugin(updates['active_tcg'])
        updates['single_plugin'] = updates['active_tcg']
    if 'slab_header_mode' in updates:
        if updates['slab_header_mode'] not in ('normal', 'inverted', 'off'):
            updates['slab_header_mode'] = 'normal'
    with _config_lock:
        config = load_config()
        for key in DEFAULTS:
            if key in updates:
                config[key] = updates[key]
        if 'single_plugin' in updates:
            config['active_tcg'] = updates['single_plugin']
        config = normalize_display_config(config)
        save_config(config)
    # Write interim status so the web UI reflects the change instantly,
    # even if the display daemon is blocked on a 15-30s e-paper refresh.
    if 'active_tcg' in updates:
        try:
            status = {}
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, 'r') as f:
                    status = json.load(f)
            status['tcg'] = updates['active_tcg']
            status['pending'] = 'Switching to ' + updates['active_tcg'].upper() + '...'
            status['timestamp'] = int(time.time())
            status.pop('display_updating', None)
            _write_status(status)
        except Exception:
            pass
    # Only wake the display daemon for TCG switch or collection mode toggle —
    # other settings (rotation, saturation, header) take effect on the next natural card change
    if 'active_tcg' in updates or 'collection_only' in updates:
        try:
            with open(NEXT_TRIGGER, 'w') as f:
                f.write('1')
        except OSError:
            pass
    return jsonify(config)


@app.route('/api/next', methods=['POST'])
def api_next():
    blocked = _manual_control_block_reason()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 409
    try:
        with open(NEXT_TRIGGER, 'w') as f:
            f.write('1')
        # Write interim status so web UI shows "loading" immediately
        try:
            status = {}
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, 'r') as f:
                    status = json.load(f)
            status['pending'] = 'Loading next card...'
            status['timestamp'] = int(time.time())
            status.pop('display_updating', None)
            _write_status(status)
        except Exception:
            pass
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/prev', methods=['POST'])
def api_prev():
    blocked = _manual_control_block_reason()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 409
    try:
        with open(PREV_TRIGGER, 'w') as f:
            f.write('1')
        try:
            status = {}
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, 'r') as f:
                    status = json.load(f)
            status['pending'] = 'Loading previous card...'
            status['timestamp'] = int(time.time())
            status.pop('display_updating', None)
            _write_status(status)
        except Exception:
            pass
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/pause', methods=['POST'])
def api_pause():
    """Toggle pause state. Returns new paused state and next_change timestamp."""
    next_change_out = 0
    if os.path.exists(PAUSE_FILE):
        # Unpausing — read remaining seconds that were saved when paused
        remaining = None
        try:
            with open(PAUSE_FILE, 'r') as f:
                val = f.read().strip()
                remaining = int(val) if val.isdigit() else None
        except Exception:
            pass
        try:
            os.remove(PAUSE_FILE)
        except OSError:
            pass
        paused = False
        try:
            status = {}
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, 'r') as f:
                    status = json.load(f)
            status['paused'] = False
            if remaining is not None:
                next_change_out = int(time.time()) + remaining
            elif status.get('interval'):
                next_change_out = int(time.time()) + status['interval']
            status['next_change'] = next_change_out
            _write_status(status)
        except Exception:
            pass
    else:
        # Pausing — snapshot how many seconds are left and write to pause file
        remaining = None
        try:
            status = {}
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, 'r') as f:
                    status = json.load(f)
            next_change = status.get('next_change', 0)
            if next_change:
                remaining = max(0, int(next_change) - int(time.time()))
            else:
                remaining = status.get('interval', 600)
        except Exception:
            pass
        try:
            with open(PAUSE_FILE, 'w') as f:
                f.write(str(remaining if remaining is not None else 0))
        except OSError:
            pass
        paused = True
        try:
            status = {}
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, 'r') as f:
                    status = json.load(f)
            status['paused'] = True
            status['next_change'] = 0
            _write_status(status)
        except Exception:
            pass
    return jsonify({"ok": True, "paused": paused, "next_change": next_change_out})


@app.route('/api/ip')
def api_ip():
    return jsonify({"ip": get_local_ip()})


@app.route('/api/card_image')
def api_current_card_image():
    """Serve the current card image from the display status."""
    allowed_dirs = list(TCG_LIBRARIES.values())
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r') as f:
                status = json.load(f)
            card_path = status.get("card_path")
            if card_path and os.path.exists(card_path):
                # Validate path is within a known card directory
                real = os.path.realpath(card_path)
                if not any(real.startswith(os.path.realpath(d)) for d in allowed_dirs):
                    return '', 403
                mime = 'image/png' if card_path.lower().endswith('.png') else 'image/jpeg'
                resp = send_file(card_path, mimetype=mime)
                resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                return resp
        except Exception:
            pass
    return '', 404


@app.route('/api/card_image/<tcg>/<set_id>/<card_id>')
def api_card_image(tcg, set_id, card_id):
    """Serve a specific card image on demand."""
    library = TCG_LIBRARIES.get(tcg)
    if not library:
        return '', 404
    # Sanitize to prevent path traversal
    safe_set = os.path.basename(set_id)
    safe_card = os.path.basename(card_id)
    for ext in IMAGE_EXTENSIONS:
        card_path = os.path.join(library, safe_set, safe_card + ext)
        if os.path.exists(card_path):
            mime = 'image/png' if ext == '.png' else 'image/jpeg'
            resp = make_response(send_file(card_path, mimetype=mime))
            resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return resp
    return '', 404


@app.route('/api/tcg_list')
def api_tcg_list():
    """Return the TCG registry for dynamic UI generation."""
    return jsonify(TCG_REGISTRY)


@app.route('/api/plugins')
def api_plugins():
    """Return plugin metadata for the modular feature branch UI."""
    config = load_config()
    return jsonify({
        "active_plugin": config["active_plugin"],
        "enabled_plugins": config["enabled_plugins"],
        "plugin_settings": config.get("plugin_settings", {}),
        "display_mode": config["display_mode"],
        "single_plugin": config["single_plugin"],
        "display_schedule": config["display_schedule"],
        "plugins": get_plugin_payload(),
    })


@app.route('/api/plugins/config', methods=['POST'])
def api_set_plugin_config():
    """Save enabled plugin selection for the modular feature branch UI."""
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    enabled_plugins = normalize_enabled_plugins(body.get('enabled_plugins'))
    plugin_settings = body.get('plugin_settings')
    with _config_lock:
        config = load_config()
        config['enabled_plugins'] = enabled_plugins
        if isinstance(plugin_settings, dict):
            config['plugin_settings'] = plugin_settings
        config = normalize_display_config(config)
        save_config(config)
    try:
        with open(NEXT_TRIGGER, 'w') as f:
            f.write('1')
    except OSError:
        pass
    return jsonify({
        "ok": True,
        "enabled_plugins": config["enabled_plugins"],
        "plugin_settings": config.get("plugin_settings", {}),
        "single_plugin": config["single_plugin"],
        "display_schedule": config["display_schedule"],
        "active_plugin": config["active_plugin"],
    })


@app.route('/api/display_plan')
def api_display_plan():
    """Return the experimental display planning config."""
    config = load_config()
    return jsonify({
        "enabled_plugins": config["enabled_plugins"],
        "display_mode": config["display_mode"],
        "single_plugin": config["single_plugin"],
        "display_schedule": config["display_schedule"],
        "active_plugin": config["active_plugin"],
        "plugins": get_plugin_payload(),
    })


@app.route('/api/display_plan', methods=['POST'])
def api_set_display_plan():
    """Save the experimental display planning config."""
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    updates = {}
    if 'display_mode' in body:
        updates['display_mode'] = body.get('display_mode')
    if 'single_plugin' in body:
        updates['single_plugin'] = body.get('single_plugin')
        updates['active_tcg'] = body.get('single_plugin')
    if 'display_schedule' in body:
        updates['display_schedule'] = body.get('display_schedule')

    with _config_lock:
        config = load_config()
        config.update(updates)
        config = normalize_display_config(config)
        save_config(config)

    try:
        status = {}
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, 'r') as f:
                status = json.load(f)
        status['pending'] = 'Updating display plan...'
        status['timestamp'] = int(time.time())
        status.pop('display_updating', None)
        _write_status(status)
    except Exception:
        pass

    try:
        with open(NEXT_TRIGGER, 'w') as f:
            f.write('1')
    except OSError:
        pass

    return jsonify({
        "ok": True,
        "display_mode": config["display_mode"],
        "single_plugin": config["single_plugin"],
        "display_schedule": config["display_schedule"],
        "active_plugin": config["active_plugin"],
    })


@app.route('/api/sets')
def api_sets():
    config = load_config()
    tcg = request.args.get('tcg', config['active_tcg'])
    library = TCG_LIBRARIES.get(tcg)
    if not library or not os.path.exists(library):
        return jsonify([])

    cache_key = 'sets_' + tcg
    sets_cache = _cache_get(cache_key, ttl=60)

    master = {}
    index_path = os.path.join(library, "master_index.json")
    if os.path.exists(index_path):
        try:
            with open(index_path, 'r') as f:
                master = json.load(f)
        except Exception:
            pass

    collection = load_collection()
    owned_ids = set(collection.get(tcg, []))

    if sets_cache:
        # Fast path: recompute owned counts from cached card IDs
        result = []
        for s in sets_cache:
            owned_count = sum(1 for cid in s["_cids"] if cid in owned_ids)
            result.append({
                "id": s["id"], "name": s["name"], "year": s["year"],
                "card_count": s["card_count"], "downloaded_count": s["downloaded_count"],
                "owned_count": owned_count,
            })
        result.sort(key=lambda x: x["year"], reverse=True)
        return jsonify(result)

    # Slow path: read _data.json per set (faster than listing 300+ .png files per dir)
    sets_data = []
    result = []
    for d in sorted(os.listdir(library)):
        set_path = os.path.join(library, d)
        if not os.path.isdir(set_path):
            continue
        data_file = os.path.join(set_path, "_data.json")
        card_ids = []
        if os.path.exists(data_file):
            try:
                with open(data_file, 'r') as f:
                    card_ids = list(json.load(f).keys())
            except Exception:
                pass
        # Count actual image files on disk (may differ from _data.json during partial downloads)
        downloaded_count = sum(1 for f in os.listdir(set_path) if _is_card_image(f))
        if not card_ids:
            card_ids = [os.path.splitext(f)[0] for f in os.listdir(set_path)
                        if _is_card_image(f)]
        owned_count = sum(1 for cid in card_ids if cid in owned_ids)
        info = master.get(d, {})
        sets_data.append({
            "id": d, "name": info.get("name", d), "year": info.get("year", ""),
            "card_count": len(card_ids), "downloaded_count": downloaded_count, "_cids": card_ids,
        })
        result.append({
            "id": d, "name": info.get("name", d), "year": info.get("year", ""),
            "card_count": len(card_ids), "downloaded_count": downloaded_count,
            "owned_count": owned_count,
        })

    _cache_set(cache_key, sets_data)
    result.sort(key=lambda x: x["year"], reverse=True)
    return jsonify(result)


@app.route('/api/sets/<set_id>/cards')
def api_set_cards(set_id):
    config = load_config()
    tcg = request.args.get('tcg', config['active_tcg'])
    library = TCG_LIBRARIES.get(tcg)
    if not library:
        return jsonify([])

    set_path = os.path.join(library, os.path.basename(set_id))
    if not os.path.isdir(set_path):
        return jsonify([])

    metadata = {}
    data_file = os.path.join(set_path, "_data.json")
    if os.path.exists(data_file):
        try:
            with open(data_file, 'r') as f:
                metadata = json.load(f)
        except Exception:
            pass

    collection = load_collection()
    owned_ids = set(collection.get(tcg, []))

    cards = []
    for f in sorted(os.listdir(set_path)):
        if not _is_card_image(f):
            continue
        card_id = os.path.splitext(f)[0]
        info = metadata.get(card_id, {})
        cards.append({
            "id": card_id,
            "name": info.get("name", card_id),
            "number": info.get("number", "?"),
            "rarity": info.get("rarity", ""),
            "owned": card_id in owned_ids,
            "set_id": set_id,
        })

    cards.sort(key=lambda x: (x["number"].zfill(5) if x["number"].isdigit() else x["number"]))
    return jsonify(cards)


@app.route('/api/collection/toggle', methods=['POST'])
def api_collection_toggle():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400
    card_id = data.get("card_id")
    if not card_id:
        return jsonify({"error": "card_id required"}), 400

    config = load_config()
    tcg = data.get("tcg", config["active_tcg"])

    with _collection_lock:
        collection = load_collection()
        if tcg not in collection:
            collection[tcg] = []

        if card_id in collection[tcg]:
            collection[tcg].remove(card_id)
            owned = False
        else:
            collection[tcg].append(card_id)
            owned = True

        save_collection(collection)
    _cache_invalidate('rarities_' + tcg)
    return jsonify({"card_id": card_id, "owned": owned})


@app.route('/api/collection/toggle_set', methods=['POST'])
def api_collection_toggle_set():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400
    set_id = data.get("set_id")
    owned = data.get("owned", True)
    if not set_id:
        return jsonify({"error": "set_id required"}), 400

    config = load_config()
    tcg = data.get("tcg", config["active_tcg"])
    library = TCG_LIBRARIES.get(tcg)
    if not library:
        return jsonify({"error": "invalid tcg"}), 400

    safe_set = os.path.basename(set_id)
    set_path = os.path.join(library, safe_set)
    if not os.path.isdir(set_path):
        return jsonify({"error": "set not found"}), 404

    card_ids = [os.path.splitext(f)[0] for f in os.listdir(set_path)
                if _is_card_image(f)]

    with _collection_lock:
        collection = load_collection()
        if tcg not in collection:
            collection[tcg] = []

        if owned:
            existing = set(collection[tcg])
            for cid in card_ids:
                if cid not in existing:
                    collection[tcg].append(cid)
        else:
            remove_set = set(card_ids)
            collection[tcg] = [cid for cid in collection[tcg] if cid not in remove_set]

        save_collection(collection)
    _cache_invalidate('rarities_' + tcg)
    return jsonify({"set_id": set_id, "owned": owned, "count": len(card_ids)})


@app.route('/api/collection/clear', methods=['POST'])
def api_collection_clear():
    config = load_config()
    tcg = config["active_tcg"]
    with _collection_lock:
        collection = load_collection()
        collection[tcg] = []
        save_collection(collection)
    _cache_invalidate('rarities_' + tcg)
    return jsonify({"ok": True})


@app.route('/api/rarities')
def api_rarities():
    """Return rarity objects with card counts for the active TCG, sorted rarest first."""
    config = load_config()
    tcg = request.args.get('tcg', config['active_tcg'])
    cache_key = 'rarities_' + tcg
    cached = _cache_get(cache_key, ttl=60)
    if cached:
        return jsonify(cached)

    library = TCG_LIBRARIES.get(tcg)
    rarity_counts = {}
    collection = load_collection()
    owned_ids = set(collection.get(tcg, []))
    rarity_owned = {}

    if library and os.path.isdir(library):
        for d in os.listdir(library):
            set_path = os.path.join(library, d)
            data_file = os.path.join(set_path, "_data.json")
            if not os.path.exists(data_file):
                continue
            # Only count cards that are actually downloaded (have image files on disk)
            try:
                downloaded = {os.path.splitext(f)[0] for f in os.listdir(set_path)
                              if _is_card_image(f)}
            except Exception:
                downloaded = set()
            try:
                with open(data_file, 'r') as f:
                    data = json.load(f)
                for card_id, card in data.items():
                    if card_id not in downloaded:
                        continue
                    r = card.get("rarity", "")
                    if r:
                        rarity_counts[r] = rarity_counts.get(r, 0) + 1
                        if card_id in owned_ids:
                            rarity_owned[r] = rarity_owned.get(r, 0) + 1
            except Exception:
                pass

    result = [{"name": r, "count": rarity_counts[r], "owned": rarity_owned.get(r, 0)}
              for r in sorted(rarity_counts.keys(), key=rarity_sort_key)]

    _cache_set(cache_key, result)
    return jsonify(result)


@app.route('/api/collection/toggle_all', methods=['POST'])
def api_collection_toggle_all():
    """Select or deselect ALL cards for the active TCG in one request."""
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    owned = body.get("owned", True)
    config = load_config()
    tcg = body.get("tcg", config["active_tcg"])
    library = TCG_LIBRARIES.get(tcg)
    if not library or not os.path.isdir(library):
        return jsonify({"error": "invalid tcg"}), 400

    # Scan filesystem outside lock to avoid blocking other requests
    all_ids = set()
    if owned:
        for d in os.listdir(library):
            set_path = os.path.join(library, d)
            if not os.path.isdir(set_path):
                continue
            for f in os.listdir(set_path):
                if _is_card_image(f):
                    all_ids.add(os.path.splitext(f)[0])

    with _collection_lock:
        collection = load_collection()
        if tcg not in collection:
            collection[tcg] = []

        if not owned:
            count = len(collection[tcg])
            collection[tcg] = []
        else:
            collection[tcg] = list(all_ids)
            count = len(all_ids)

        save_collection(collection)
    _cache_invalidate('rarities_' + tcg)
    return jsonify({"owned": owned, "count": count})


@app.route('/api/collection/toggle_batch', methods=['POST'])
def api_collection_toggle_batch():
    """Add or remove a specific list of card_ids in one request."""
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    card_ids = body.get("card_ids", [])
    owned = body.get("owned", True)
    config = load_config()
    tcg = body.get("tcg", config["active_tcg"])

    with _collection_lock:
        collection = load_collection()
        if tcg not in collection:
            collection[tcg] = []

        if owned:
            existing = set(collection[tcg])
            for cid in card_ids:
                if cid not in existing:
                    collection[tcg].append(cid)
                    existing.add(cid)
        else:
            remove = set(card_ids)
            collection[tcg] = [c for c in collection[tcg] if c not in remove]

        save_collection(collection)
    _cache_invalidate('rarities_' + tcg)
    return jsonify({"owned": owned, "count": len(card_ids)})


@app.route('/api/collection/toggle_rarity', methods=['POST'])
def api_collection_toggle_rarity():
    """Select or deselect all cards of a given rarity. Optionally scoped to a single set."""
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    rarity = body.get("rarity")
    owned = body.get("owned", True)
    set_id = body.get("set_id")  # optional — None means all sets

    if not rarity:
        return jsonify({"error": "rarity required"}), 400

    config = load_config()
    tcg = body.get("tcg", config["active_tcg"])
    library = TCG_LIBRARIES.get(tcg)
    if not library or not os.path.isdir(library):
        return jsonify({"error": "invalid tcg or no data"}), 400

    # Find all card IDs matching the rarity
    matching_ids = []
    if set_id:
        dirs_to_scan = [os.path.basename(set_id)]
    else:
        dirs_to_scan = [d for d in os.listdir(library)
                        if os.path.isdir(os.path.join(library, d))]
    for d in dirs_to_scan:
        data_file = os.path.join(library, d, "_data.json")
        if not os.path.exists(data_file):
            continue
        try:
            with open(data_file, 'r') as f:
                cards_data = json.load(f)
            for card_id, card_info in cards_data.items():
                if card_info.get("rarity") == rarity:
                    matching_ids.append(card_id)
        except Exception:
            pass

    # Update collection
    with _collection_lock:
        collection = load_collection()
        if tcg not in collection:
            collection[tcg] = []

        if owned:
            existing = set(collection[tcg])
            for cid in matching_ids:
                if cid not in existing:
                    collection[tcg].append(cid)
        else:
            remove_set = set(matching_ids)
            collection[tcg] = [cid for cid in collection[tcg] if cid not in remove_set]

        save_collection(collection)
    _cache_invalidate('rarities_' + tcg)
    return jsonify({"rarity": rarity, "owned": owned, "count": len(matching_ids)})


@app.route('/api/search')
def api_search():
    """Search card names across all sets. Returns up to 100 matches."""
    q = request.args.get('q', '').strip().lower()
    if len(q) < 2:
        return jsonify([])

    config = load_config()
    tcg = request.args.get('tcg', config['active_tcg'])
    library = TCG_LIBRARIES.get(tcg)
    if not library or not os.path.isdir(library):
        return jsonify([])

    master = {}
    index_path = os.path.join(library, "master_index.json")
    if os.path.exists(index_path):
        try:
            with open(index_path, 'r') as f:
                master = json.load(f)
        except Exception:
            pass

    collection = load_collection()
    owned_ids = set(collection.get(tcg, []))

    results = []
    sets_searched = 0
    for d in os.listdir(library):
        set_path = os.path.join(library, d)
        data_file = os.path.join(set_path, "_data.json")
        if not os.path.exists(data_file):
            continue
        sets_searched += 1
        # Only include cards that have image files on disk
        try:
            downloaded = {os.path.splitext(f)[0] for f in os.listdir(set_path)
                          if _is_card_image(f)}
        except Exception:
            downloaded = set()
        try:
            with open(data_file, 'r') as f:
                data = json.load(f)
            set_info = master.get(d, {})
            for card_id, card in data.items():
                if card_id not in downloaded:
                    continue
                name = card.get("name", "")
                if q in name.lower():
                    results.append({
                        "id": card_id,
                        "name": name,
                        "number": card.get("number", "?"),
                        "rarity": card.get("rarity", ""),
                        "set_id": d,
                        "set_name": set_info.get("name", d),
                        "owned": card_id in owned_ids,
                    })
        except Exception:
            pass

    results.sort(key=lambda x: (x["name"].lower(), x["set_id"]))
    total = len(results)
    return jsonify({"results": results[:200], "total": total, "sets_searched": sets_searched})


@app.route('/api/collection/favorites')
def api_favorites_get():
    """Return the favorites list for the active TCG."""
    config = load_config()
    tcg = config["active_tcg"]
    collection = load_collection()
    favs = collection.get("_favorites", {}).get(tcg, [])
    return jsonify(favs)


@app.route('/api/collection/favorites', methods=['POST'])
def api_favorites_set():
    """Add or remove a favorite name. Also batch-adds/removes all matching card IDs."""
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    name = body.get("name", "").strip()
    owned = body.get("owned", True)
    if not name:
        return jsonify({"error": "name required"}), 400

    config = load_config()
    tcg = body.get("tcg", config["active_tcg"])

    # Find matching card IDs (read-only, outside lock)
    library = TCG_LIBRARIES.get(tcg)
    matching_ids = []
    if library and os.path.isdir(library):
        for d in os.listdir(library):
            data_file = os.path.join(library, d, "_data.json")
            if not os.path.exists(data_file):
                continue
            try:
                with open(data_file, 'r') as f:
                    data = json.load(f)
                for card_id, card in data.items():
                    if card.get("name", "").lower() == name.lower():
                        matching_ids.append(card_id)
            except Exception:
                pass

    with _collection_lock:
        collection = load_collection()

        # Manage favorites list
        if "_favorites" not in collection:
            collection["_favorites"] = {}
        if tcg not in collection["_favorites"]:
            collection["_favorites"][tcg] = []

        favs = collection["_favorites"][tcg]

        if owned:
            if not any(f.lower() == name.lower() for f in favs):
                favs.append(name)
        else:
            collection["_favorites"][tcg] = [f for f in favs if f.lower() != name.lower()]

        if tcg not in collection:
            collection[tcg] = []

        if owned:
            existing = set(collection[tcg])
            for cid in matching_ids:
                if cid not in existing:
                    collection[tcg].append(cid)
                    existing.add(cid)
        else:
            remove = set(matching_ids)
            collection[tcg] = [c for c in collection[tcg] if c not in remove]

        save_collection(collection)
    _cache_invalidate('rarities_' + tcg)
    return jsonify({"name": name, "owned": owned, "count": len(matching_ids)})


@app.route('/api/download/start', methods=['POST'])
def api_download_start():
    global _download_proc, _download_tcg, _download_log_fh

    with _download_lock:
        if _download_proc and _download_proc.poll() is None:
            return jsonify({"ok": False, "error": "Download already running"})

        # Close any leftover file handle from a previous download
        _close_download_log()

        if not _has_disk_space():
            return jsonify({"ok": False, "error": "Not enough storage space. Delete some cards first."})

        data = request.get_json(force=True) if request.data else {}
        tcg = data.get("tcg", "pokemon")
        since = data.get("since")

        reg = TCG_REGISTRY.get(tcg)
        if not reg or not reg.get("download_script"):
            return jsonify({"ok": False, "error": "Unknown TCG or no download script"})

        cmd = ["python3", os.path.join(SCRIPT_DIR, "scripts", reg["download_script"])]
        if tcg == "mtg" and since:
            cmd.extend(["--since", str(since)])

        try:
            _download_log_fh = open(DOWNLOAD_LOG, 'w')
        except OSError as e:
            return jsonify({"ok": False, "error": "Failed to open download log."})
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        try:
            _download_proc = subprocess.Popen(
                cmd, stdout=_download_log_fh, stderr=subprocess.STDOUT,
                cwd=SCRIPT_DIR, env=env
            )
        except Exception as e:
            _close_download_log()
            return jsonify({"ok": False, "error": "Failed to start download."})
        _download_tcg = tcg

        return jsonify({"ok": True, "tcg": tcg, "pid": _download_proc.pid})


@app.route('/api/download/stop', methods=['POST'])
def api_download_stop():
    global _download_proc, _download_tcg

    with _download_lock:
        if _download_proc and _download_proc.poll() is None:
            tcg = _download_tcg
            try:
                _download_proc.send_signal(signal.SIGTERM)
                _download_proc.wait(timeout=5)
            except Exception:
                try:
                    _download_proc.kill()
                except Exception:
                    pass
            _download_proc = None
            _download_tcg = None
            _close_download_log()
            _cache_invalidate('storage', 'rarities_' + tcg, 'sets_' + tcg)
            _signal_library_changed(tcg)
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "No download running"})


@app.route('/api/download/status')
def api_download_status():
    global _download_proc, _download_tcg

    running = False
    with _download_lock:
        tcg = _download_tcg
        if _download_proc and _download_proc.poll() is None:
            running = True
        elif _download_proc:
            # Process finished — close log file handle and clean up
            finished_tcg = tcg
            _close_download_log()
            _download_proc = None
            _download_tcg = None
            _cache_invalidate('storage', 'rarities_' + finished_tcg, 'sets_' + finished_tcg)
            _signal_library_changed(finished_tcg)
            tcg = None
        else:
            tcg = None

    lines = []
    if os.path.exists(DOWNLOAD_LOG):
        try:
            with open(DOWNLOAD_LOG, 'rb') as f:
                f.seek(0, 2)
                size = f.tell()
                chunk = min(size, 8192)
                f.seek(size - chunk)
                tail = f.read().decode('utf-8', errors='replace')
                lines = [l.rstrip() for l in tail.splitlines()[-30:]]
        except Exception:
            pass

    return jsonify({"running": running, "tcg": tcg, "lines": lines})


_storage_computing = False
_storage_lock = threading.Lock()


def _compute_storage():
    """Compute storage info. Uses native du for size (much faster than Python stat)."""
    info = {}
    for tcg, path in TCG_LIBRARIES.items():
        if os.path.exists(path):
            # Use du -sb for fast size calculation (native C, avoids 50k+ Python stat calls)
            total_size = 0
            try:
                result = subprocess.run(['du', '-sb', path],
                                        capture_output=True, text=True, timeout=120)
                if result.returncode == 0 and result.stdout.strip():
                    total_size = int(result.stdout.split()[0])
            except Exception:
                pass

            # Count cards and sets with listdir (no stat on each file)
            card_count = 0
            set_count = 0
            try:
                for d in os.listdir(path):
                    set_path = os.path.join(path, d)
                    if os.path.isdir(set_path):
                        set_count += 1
                        for f in os.listdir(set_path):
                            if _is_card_image(f):
                                card_count += 1
            except Exception:
                pass

            info[tcg] = {
                "path": path,
                "size_mb": round(total_size / (1024 * 1024)),
                "size_gb": round(total_size / (1024 * 1024 * 1024), 2),
                "card_count": card_count,
                "set_count": set_count,
            }
        else:
            info[tcg] = {"path": path, "size_mb": 0, "size_gb": 0.0,
                         "card_count": 0, "set_count": 0}
    try:
        usage = shutil.disk_usage('/home/pi')
        info['_disk'] = {
            'free_gb': round(usage.free / (1024 * 1024 * 1024), 2),
            'total_gb': round(usage.total / (1024 * 1024 * 1024), 2),
        }
    except Exception:
        pass
    return info


@app.route('/api/storage')
def api_storage():
    global _storage_computing
    cached = _cache_get('storage', ttl=120)
    if cached:
        return jsonify(cached)

    # Return stale cache while recomputing in background
    stale = _cache_get('storage', ttl=float('inf'))

    with _storage_lock:
        if not _storage_computing:
            _storage_computing = True

            def compute():
                global _storage_computing
                try:
                    result = _compute_storage()
                    _cache_set('storage', result)
                finally:
                    with _storage_lock:
                        _storage_computing = False

            threading.Thread(target=compute, daemon=True).start()

    if stale:
        return jsonify(stale)
    return jsonify({"_computing": True})


@app.route('/api/delete', methods=['POST'])
def api_delete():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400
    tcg = data.get("tcg")
    if not tcg or tcg not in TCG_LIBRARIES:
        return jsonify({"ok": False, "error": "Invalid TCG"}), 400

    with _download_lock:
        if _download_proc and _download_proc.poll() is None and _download_tcg == tcg:
            return jsonify({"ok": False, "error": "Stop the current download before deleting this TCG."}), 409

    path = TCG_LIBRARIES[tcg]
    if os.path.exists(path):
        try:
            shutil.rmtree(path)
            _cache_invalidate('storage', 'rarities_' + tcg, 'sets_' + tcg)
            _signal_library_changed(tcg)
            return jsonify({"ok": True, "tcg": tcg})
        except Exception as e:
            app.logger.error(f"Delete failed for {tcg}: {e}")
            return jsonify({"ok": False, "error": "Delete failed. Try again or reboot."})
    return jsonify({"ok": True, "tcg": tcg})


# --- OTA UPDATE ---
# Fix "dubious ownership" — web service runs as root but repo is owned by pi
configure_git_safe_directory(SCRIPT_DIR)


@app.route('/api/version')
def api_version():
    """Return the current version (hardcoded + git hash if available)."""
    git_hash = ""
    try:
        local = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=SCRIPT_DIR,
                               capture_output=True, text=True, timeout=5)
        if local.returncode == 0:
            git_hash = local.stdout.strip()[:8]
    except Exception:
        pass
    version = f"{VERSION}-{git_hash}" if git_hash else VERSION
    return jsonify({"version": version})


@app.route('/api/update/branches')
def api_update_branches():
    """Return available update branches for the current repo."""
    branches = git_remote_branches(SCRIPT_DIR, fetch_first=True)
    config = load_config()
    return jsonify({
        "branches": branches,
        "selected": normalize_update_branch(SCRIPT_DIR, config.get("update_branch"), branches),
        "current": git_current_branch(SCRIPT_DIR),
        "default": git_default_branch(SCRIPT_DIR),
    })


@app.route('/api/update/check', methods=['POST'])
def api_update_check():
    """Check if updates are available by comparing local vs remote HEAD."""
    try:
        body = request.get_json(silent=True) or {}
        # Fetch first so branch detection can see remote refs
        fetch = subprocess.run(['git', 'fetch', '--prune', 'origin'], cwd=SCRIPT_DIR,
                               capture_output=True, text=True, timeout=30)
        if fetch.returncode != 0:
            return jsonify({"ok": False, "error": "Could not reach update server. Check your internet connection."})
        branches = git_remote_branches(SCRIPT_DIR, fetch_first=False)
        config = load_config()
        branch = normalize_update_branch(SCRIPT_DIR, body.get("branch") or config.get("update_branch"), branches)
        local = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=SCRIPT_DIR,
                               capture_output=True, text=True, timeout=5)
        remote = subprocess.run(['git', 'rev-parse', f'origin/{branch}'], cwd=SCRIPT_DIR,
                                capture_output=True, text=True, timeout=5)
        local_hash = local.stdout.strip()[:8] if local.returncode == 0 else ""
        remote_hash = remote.stdout.strip()[:8] if remote.returncode == 0 else ""
        behind = subprocess.run(['git', 'rev-list', '--count', f'HEAD..origin/{branch}'],
                                cwd=SCRIPT_DIR, capture_output=True, text=True, timeout=5)
        commits_behind = int(behind.stdout.strip()) if behind.returncode == 0 else 0
        # Build display version: "1.0.0-abc123" or just "1.0.0"
        local_version = f"{VERSION}-{local_hash}" if local_hash else VERSION
        return jsonify({"ok": True, "branch": branch, "local": local_version, "remote": remote_hash,
                        "behind": commits_behind, "up_to_date": commits_behind == 0})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/update/start', methods=['POST'])
def api_update_start():
    """Launch OTA update script detached from this process."""
    lock_file = UPDATE_LOCK_FILE
    if os.path.exists(lock_file):
        try:
            with open(lock_file) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)  # Check if process is alive
            return jsonify({"ok": False, "error": "Update already in progress"})
        except (ValueError, OSError):
            pass  # Stale lock, allow proceeding
    script = os.path.join(SCRIPT_DIR, "scripts", "ota_update.sh")
    if not os.path.exists(script):
        return jsonify({"ok": False, "error": "Update script not found"})
    try:
        body = request.get_json(silent=True) or {}
        branches = git_remote_branches(SCRIPT_DIR, fetch_first=True)
        branch = normalize_update_branch(SCRIPT_DIR, body.get("branch") or load_config().get("update_branch"), branches)
        with _config_lock:
            config = load_config()
            config["update_branch"] = branch
            save_config(config)
        # Clear old status
        if os.path.exists(UPDATE_STATUS_FILE):
            os.remove(UPDATE_STATUS_FILE)
        env = os.environ.copy()
        env['INKSLAB_UPDATE_BRANCH'] = branch
        subprocess.Popen(['bash', script], cwd=SCRIPT_DIR, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return jsonify({"ok": True, "branch": branch})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/update/status')
def api_update_status():
    """Read OTA update progress."""
    if os.path.exists(UPDATE_STATUS_FILE):
        try:
            with open(UPDATE_STATUS_FILE, 'r') as f:
                status = json.load(f)
            # Auto-clear stale status
            if time.time() - status.get('timestamp', 0) > 120:
                return jsonify({"stage": "idle"})
            return jsonify(status)
        except Exception:
            pass
    return jsonify({"stage": "idle"})


# --- WIFI SETUP ---

def _perform_wifi_connection(ssid, password):
    """Background thread: tear down hotspot, connect, handle failure."""
    global _wifi_setup_mode, _wifi_connect_result

    try:
        # Step 1: Tear down hotspot and wait for wlan0 to switch from AP to station mode.
        # Pi Zero W has a single-chip radio — it needs 10-15s to fully exit AP mode
        # before nmcli can scan and connect in station mode.
        wifi_manager.stop_hotspot()
        time.sleep(12)

        # Step 2: Attempt connection
        success, message = wifi_manager.connect_to_network(ssid, password)

        if success:
            with _wifi_connect_lock:
                _wifi_connect_result = {
                    "status": "success", "ip": message,
                    "ssid": ssid, "error": None,
                }
            _wifi_setup_mode = False
            # Clear watchdog flag — WiFi is back
            try:
                os.remove(WATCHDOG_SETUP_FLAG)
            except OSError:
                pass
            # Signal inkslab.py to refresh splash screen
            try:
                with open(WIFI_CONNECTED_TRIGGER, "w") as f:
                    f.write(message)
            except OSError:
                pass
        else:
            # Connection failed — restore hotspot and signal e-ink display
            with _wifi_connect_lock:
                _wifi_connect_result = {
                    "status": "failed", "error": message, "ssid": ssid,
                }
            try:
                with open(WIFI_FAILED_TRIGGER, "w") as f:
                    f.write(ssid)
            except OSError:
                pass
            wifi_manager.start_hotspot()
            _wifi_setup_mode = True
    except Exception as e:
        with _wifi_connect_lock:
            _wifi_connect_result = {
                "status": "failed", "error": str(e), "ssid": ssid,
            }
        try:
            with open(WIFI_FAILED_TRIGGER, "w") as f:
                f.write(ssid)
        except OSError:
            pass
        try:
            wifi_manager.start_hotspot()
            _wifi_setup_mode = True
        except Exception:
            pass


@app.route('/api/wifi/status')
def api_wifi_status():
    """Return current WiFi state and setup mode flag."""
    global _wifi_setup_mode
    # Daemon watchdog may have auto-started hotspot — pick up its flag
    if not _wifi_setup_mode and os.path.exists(WATCHDOG_SETUP_FLAG):
        _wifi_setup_mode = True
    status = wifi_manager.get_wifi_status()
    status["setup_mode"] = _wifi_setup_mode
    with _wifi_connect_lock:
        status["connect_status"] = dict(_wifi_connect_result)
    return jsonify(status)


@app.route('/api/wifi/scan')
def api_wifi_scan():
    """Scan for available networks."""
    networks = wifi_manager.scan_networks()
    return jsonify(networks)


@app.route('/api/wifi/connect', methods=['POST'])
def api_wifi_connect():
    """Begin connection to a WiFi network (non-blocking)."""
    global _wifi_connect_result
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400
    ssid = data.get("ssid", "").strip()
    password = data.get("password", "").strip()

    if not ssid:
        return jsonify({"ok": False, "error": "SSID required"}), 400

    with _wifi_connect_lock:
        if _wifi_connect_result.get("status") == "connecting":
            return jsonify({"ok": False, "error": "Connection already in progress"}), 409
        _wifi_connect_result = {"status": "connecting", "ssid": ssid, "error": None}

    t = threading.Thread(target=_perform_wifi_connection, args=(ssid, password), daemon=True)
    t.start()
    return jsonify({"ok": True, "message": "Connecting..."})


@app.route('/api/wifi/disconnect', methods=['POST'])
def api_wifi_disconnect():
    """Disconnect WiFi, forget the saved profile, and re-enter setup mode."""
    global _wifi_setup_mode, _wifi_connect_result
    try:
        wifi_manager.stop_hotspot()
        # Disconnect AND delete (forget) the WiFi profile
        ssid = wifi_manager.get_active_ssid()
        if ssid:
            subprocess.run(["nmcli", "con", "down", "id", ssid],
                           capture_output=True, timeout=10)
            subprocess.run(["nmcli", "con", "delete", "id", ssid],
                           capture_output=True, timeout=10)
        with _wifi_connect_lock:
            _wifi_connect_result = {"status": "idle"}
        _wifi_setup_mode = True
        wifi_manager.start_hotspot()
        # Signal display daemon to show setup screen
        try:
            with open(WIFI_SETUP_TRIGGER, "w") as f:
                f.write("1")
        except OSError:
            pass
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/factory_reset', methods=['POST'])
def api_factory_reset():
    """Prepare unit for shipping: forget WiFi, delete card data, reset config."""
    global _wifi_setup_mode, _wifi_connect_result, _download_proc, _download_tcg
    errors = []

    # 0. Stop any running card download before deleting data
    with _download_lock:
        if _download_proc and _download_proc.poll() is None:
            try:
                _download_proc.terminate()
                _download_proc.wait(timeout=5)
            except Exception:
                pass
        _close_download_log()
        _download_proc = None
        _download_tcg = None

    # 1. Forget all saved WiFi profiles (except hotspot)
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-e", "yes", "-f", "TYPE,NAME", "con", "show"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().splitlines():
            parts = wifi_manager._split_nmcli_escaped(line)
            if len(parts) >= 2 and "wireless" in parts[0] and parts[1] != "InkSlab-Setup":
                subprocess.run(["nmcli", "con", "down", "id", parts[1]],
                               capture_output=True, timeout=10)
                subprocess.run(["nmcli", "con", "delete", "id", parts[1]],
                               capture_output=True, timeout=10)
    except Exception as e:
        errors.append(f"WiFi cleanup: {e}")

    # 2. Delete all downloaded card data
    for tcg_key, tcg_info in TCG_REGISTRY.items():
        card_path = tcg_info["path"]
        if os.path.isdir(card_path):
            try:
                shutil.rmtree(card_path)
            except Exception as e:
                errors.append(f"Delete {tcg_key}: {e}")

    # Clean up Python cache
    for root, dirs, files in os.walk(SCRIPT_DIR):
        for d in dirs:
            if d == '__pycache__':
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)

    # 3. Reset config and collection to defaults
    for f in [CONFIG_FILE, COLLECTION_FILE]:
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception as e:
            errors.append(f"Reset {f}: {e}")

    # 4. Enter setup mode
    with _wifi_connect_lock:
        _wifi_connect_result = {"status": "idle"}
    _wifi_setup_mode = True
    try:
        wifi_manager.start_hotspot()
    except Exception as e:
        errors.append(f"Hotspot: {e}")

    # 5. Clean up temp files, logs, and user traces
    with _download_lock:
        _close_download_log()
    for tmp_file in [STATUS_FILE, DOWNLOAD_LOG, NEXT_TRIGGER, COLLECTION_TRIGGER, LIBRARY_TRIGGER,
                     PREV_TRIGGER, PAUSE_FILE,
                     WIFI_CONNECTED_TRIGGER, WIFI_FAILED_TRIGGER,
                     WIFI_SETUP_TRIGGER, WATCHDOG_SETUP_FLAG,
                     UNBOX_TRIGGER,
                     UPDATE_STATUS_FILE,
                     UPDATE_LOCK_FILE,
                     STATUS_FILE + ".tmp"]:
        try:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
        except OSError:
            pass
    # Purge journal logs (contain SSIDs, IPs)
    try:
        subprocess.run(["journalctl", "--rotate"], capture_output=True, timeout=10)
        subprocess.run(["journalctl", "--vacuum-time=1s"], capture_output=True, timeout=10)
    except Exception:
        pass
    # Clear bash history
    for hist in ["/home/pi/.bash_history", "/root/.bash_history"]:
        try:
            if os.path.exists(hist):
                with open(hist, 'w') as _:
                    pass
        except OSError:
            pass

    # 6. Signal display daemon to show unbox/shipping screen
    # The "Plug me in!" screen stays on the e-ink after power off — perfect for shipping.
    # When the customer plugs it in, it boots without WiFi and shows the setup screen automatically.
    try:
        with open(UNBOX_TRIGGER, "w") as f:
            f.write("1")
    except OSError:
        pass

    # 7. Invalidate all caches
    _cache_invalidate("storage", "card_counts")

    if errors:
        return jsonify({"ok": True, "warnings": errors})
    return jsonify({"ok": True})



# Captive portal detection endpoints — redirect to setup page
@app.route('/hotspot-detect.html')
@app.route('/generate_204')
@app.route('/ncsi.txt')
@app.route('/connecttest.txt')
@app.route('/redirect')
@app.route('/canonical.html')
def captive_portal_detect():
    global _wifi_setup_mode
    if not _wifi_setup_mode and os.path.exists(WATCHDOG_SETUP_FLAG):
        _wifi_setup_mode = True
    if _wifi_setup_mode:
        # Serve setup page directly — avoids redirect delay that shows blank blue screen
        resp = make_response(WIFI_SETUP_HTML)
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp
    return "Success", 200


# --- CUSTOM IMAGE MANAGEMENT ---

CUSTOM_PATH = TCG_LIBRARIES["custom"]

@app.route('/api/custom/folders')
def api_custom_folders():
    """List custom image folders with card counts."""
    if not os.path.exists(CUSTOM_PATH):
        return jsonify([])
    folders = []
    master = {}
    idx_path = os.path.join(CUSTOM_PATH, "master_index.json")
    if os.path.exists(idx_path):
        try:
            with open(idx_path, 'r') as f:
                master = json.load(f)
        except Exception:
            pass
    for d in sorted(os.listdir(CUSTOM_PATH)):
        dp = os.path.join(CUSTOM_PATH, d)
        if not os.path.isdir(dp):
            continue
        count = sum(1 for f in os.listdir(dp) if _is_card_image(f))
        info = master.get(d, {})
        folders.append({"id": d, "name": info.get("name", d.replace('_', ' ').replace('-', ' ').title()),
                        "card_count": count})
    return jsonify(folders)


@app.route('/api/custom/create_folder', methods=['POST'])
def api_custom_create_folder():
    """Create a new custom image folder."""
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    # Sanitize folder name
    safe = "".join(c if c.isalnum() or c in (' ', '-', '_') else '' for c in name).strip()
    safe = safe.replace(' ', '_').lower()
    if not safe:
        return jsonify({"error": "invalid name"}), 400
    folder = os.path.join(CUSTOM_PATH, safe)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        return jsonify({"error": f"Cannot create folder: {e}"}), 500
    # Update master_index
    with _custom_lock:
        idx_path = os.path.join(CUSTOM_PATH, "master_index.json")
        master = {}
        if os.path.exists(idx_path):
            try:
                with open(idx_path, 'r') as f:
                    master = json.load(f)
            except Exception:
                pass
        master[safe] = {"name": name, "year": ""}
        _atomic_write_json(idx_path, master)
    _cache_invalidate('sets_custom', 'storage')
    return jsonify({"ok": True, "id": safe, "name": name})


@app.route('/api/custom/rename_folder', methods=['POST'])
def api_custom_rename_folder():
    """Rename a custom set's display name."""
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400
    folder_id = data.get("id", "")
    new_name = data.get("name", "").strip()
    if not folder_id or not new_name:
        return jsonify({"error": "id and name required"}), 400
    with _custom_lock:
        idx_path = os.path.join(CUSTOM_PATH, "master_index.json")
        master = {}
        if os.path.exists(idx_path):
            try:
                with open(idx_path, 'r') as f:
                    master = json.load(f)
            except Exception:
                pass
        if folder_id not in master:
            master[folder_id] = {"name": new_name, "year": ""}
        else:
            master[folder_id]["name"] = new_name
        _atomic_write_json(idx_path, master)
    _cache_invalidate('sets_custom')
    return jsonify({"ok": True})


@app.route('/api/custom/folder/<name>', methods=['DELETE'])
def api_custom_delete_folder(name):
    """Delete an entire custom folder."""
    safe = os.path.basename(name)
    folder = os.path.join(CUSTOM_PATH, safe)
    with _custom_lock:
        # Update index FIRST so daemon won't try to read deleted folder
        idx_path = os.path.join(CUSTOM_PATH, "master_index.json")
        if os.path.exists(idx_path):
            try:
                with open(idx_path, 'r') as f:
                    master = json.load(f)
                master.pop(safe, None)
                _atomic_write_json(idx_path, master)
            except Exception:
                pass
        if os.path.isdir(folder):
            shutil.rmtree(folder)
    _cache_invalidate('sets_custom', 'storage')
    _signal_library_changed('custom')
    return jsonify({"ok": True})


@app.route('/api/custom/card/<folder>/<card_id>', methods=['DELETE'])
def api_custom_delete_card(folder, card_id):
    """Delete a single card from a custom folder."""
    safe_folder = os.path.basename(folder)
    safe_card = os.path.basename(card_id)
    folder_path = os.path.join(CUSTOM_PATH, safe_folder)
    with _custom_lock:
        # Update metadata first
        data_file = os.path.join(folder_path, "_data.json")
        if os.path.exists(data_file):
            try:
                with open(data_file, 'r') as f:
                    data = json.load(f)
                data.pop(safe_card, None)
                _atomic_write_json(data_file, data)
            except Exception:
                pass
        # Then remove image files
        for ext in IMAGE_EXTENSIONS:
            p = os.path.join(folder_path, safe_card + ext)
            try:
                os.remove(p)
            except OSError:
                pass
    _cache_invalidate('sets_custom', 'storage')
    _signal_library_changed('custom')
    return jsonify({"ok": True})


@app.route('/api/custom/upload', methods=['POST'])
def api_custom_upload():
    """Upload an image to a custom folder."""
    folder_id = request.form.get("folder", "")
    if not folder_id:
        return jsonify({"error": "folder required"}), 400
    safe_folder = os.path.basename(folder_id)
    folder_path = os.path.join(CUSTOM_PATH, safe_folder)
    if not os.path.isdir(folder_path):
        return jsonify({"error": "folder not found"}), 404
    if not _has_disk_space():
        return jsonify({"error": "Not enough storage space. Delete some cards first."}), 507
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "file required"}), 400
    # Validate extension
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in IMAGE_EXTENSIONS:
        return jsonify({"error": "Only PNG/JPG images allowed"}), 400
    # Sanitize filename
    base = os.path.splitext(f.filename)[0]
    safe_base = "".join(c if c.isalnum() or c in ('-', '_', ' ') else '' for c in base).strip()
    if not safe_base:
        safe_base = f"card_{int(time.time())}"
    safe_name = safe_base.replace(' ', '_') + ext
    filepath = os.path.join(folder_path, safe_name)
    # Avoid overwrite
    counter = 1
    while os.path.exists(filepath):
        filepath = os.path.join(folder_path, f"{safe_base}_{counter}{ext}")
        counter += 1
    # Save to temp file first, then rename (atomic)
    tmp_path = filepath + '.tmp'
    try:
        f.save(tmp_path)
        os.rename(tmp_path, filepath)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": "Upload failed. Try again."}), 500
    card_id = os.path.splitext(os.path.basename(filepath))[0]
    # Auto-add metadata (locked + atomic)
    with _custom_lock:
        data_file = os.path.join(folder_path, "_data.json")
        data = {}
        if os.path.exists(data_file):
            try:
                with open(data_file, 'r') as df:
                    data = json.load(df)
            except Exception:
                pass
        data[card_id] = {
            "name": safe_base.replace('_', ' ').replace('-', ' ').title(),
            "number": str(len(data) + 1),
            "rarity": "",
        }
        _atomic_write_json(data_file, data)
    _cache_invalidate('sets_custom', 'storage')
    _signal_library_changed('custom')
    return jsonify({"ok": True, "card_id": card_id})


@app.route('/api/custom/card_metadata', methods=['POST'])
def api_custom_card_metadata():
    """Edit a card's metadata (name, number, rarity)."""
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400
    folder_id = data.get("folder", "")
    card_id = data.get("card_id", "")
    if not folder_id or not card_id:
        return jsonify({"error": "folder and card_id required"}), 400
    safe_folder = os.path.basename(folder_id)
    folder_path = os.path.join(CUSTOM_PATH, safe_folder)
    if not os.path.isdir(folder_path):
        return jsonify({"error": "folder not found"}), 404
    data_file = os.path.join(folder_path, "_data.json")
    with _custom_lock:
        cards = {}
        if os.path.exists(data_file):
            try:
                with open(data_file, 'r') as f:
                    cards = json.load(f)
            except Exception:
                pass
        if card_id not in cards:
            cards[card_id] = {}
        if "name" in data:
            cards[card_id]["name"] = data["name"]
        if "number" in data:
            cards[card_id]["number"] = data["number"]
        if "rarity" in data:
            cards[card_id]["rarity"] = data["rarity"]
        _atomic_write_json(data_file, cards)
    return jsonify({"ok": True})


@app.route('/api/custom/set_metadata', methods=['POST'])
def api_custom_set_metadata():
    """Edit a set's display name and year."""
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400
    folder_id = data.get("id", "")
    if not folder_id:
        return jsonify({"error": "id required"}), 400
    idx_path = os.path.join(CUSTOM_PATH, "master_index.json")
    with _custom_lock:
        master = {}
        if os.path.exists(idx_path):
            try:
                with open(idx_path, 'r') as f:
                    master = json.load(f)
            except Exception:
                pass
        if folder_id not in master:
            master[folder_id] = {"name": folder_id, "year": ""}
        if "name" in data:
            master[folder_id]["name"] = data["name"]
        if "year" in data:
            master[folder_id]["year"] = data["year"]
        _atomic_write_json(idx_path, master)
    _cache_invalidate('sets_custom')
    return jsonify({"ok": True})


# --- DASHBOARD HTML ---

WIFI_SETUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>InkSlab WiFi Setup</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #132E3E; color: #D8E6E4; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; min-height: 100vh; display: flex; flex-direction: column; }
.header { background: #0d2230; padding: 16px; text-align: center; border-bottom: 1px solid #1F333F; }
.header h1 { font-size: 20px; color: #FCFDF0; font-weight: 700; }
.header p { font-size: 12px; color: #6BCCBD; margin-top: 4px; }
.content { flex: 1; padding: 16px; max-width: 480px; margin: 0 auto; width: 100%; }
.card { background: #1a3a4a; border-radius: 10px; padding: 16px; margin-bottom: 16px; border: 1px solid #1F333F; }
.welcome { text-align: center; padding: 20px 0; }
.welcome h2 { color: #FCFDF0; font-size: 22px; margin-bottom: 8px; }
.welcome p { color: #6BCCBD; font-size: 14px; }
.section-title { font-size: 14px; font-weight: 600; color: #6BCCBD; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
.btn { display: inline-block; padding: 10px 16px; border-radius: 6px; border: none; font-size: 14px; font-weight: 600; cursor: pointer; text-align: center; transition: opacity 0.2s; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary { background: #36A5CA; color: #010001; width: 100%; }
.btn-secondary { background: #1F333F; color: #D8E6E4; border: 1px solid #36A5CA44; }
.btn-sm { padding: 6px 12px; font-size: 12px; }
.network-list { max-height: 45vh; overflow-y: auto; border-radius: 8px; }
.network-item { display: flex; align-items: center; justify-content: space-between; padding: 12px; background: #132E3E; border-bottom: 1px solid #1F333F; cursor: pointer; transition: background 0.15s; }
.network-item:hover, .network-item:active { background: #1F333F; }
.network-item:first-child { border-radius: 8px 8px 0 0; }
.network-item:last-child { border-radius: 0 0 8px 8px; border-bottom: none; }
.network-ssid { font-size: 15px; color: #FCFDF0; font-weight: 500; flex: 1; }
.network-meta { display: flex; align-items: center; gap: 8px; }
.signal-bars { display: flex; gap: 2px; align-items: flex-end; height: 16px; }
.signal-bar { width: 4px; background: #6BCCBD; border-radius: 1px; }
.signal-bar.off { background: #1F333F; }
.lock { font-size: 12px; color: #6BCCBD; }
.connect-form { display: none; margin-top: 12px; }
.connect-form.open { display: block; }
.connect-form h3 { font-size: 15px; color: #FCFDF0; margin-bottom: 10px; }
.input-wrap { position: relative; margin-bottom: 12px; }
.input-wrap input { width: 100%; padding: 12px 44px 12px 12px; background: #132E3E; color: #FCFDF0; border: 1px solid #36A5CA44; border-radius: 6px; font-size: 15px; outline: none; }
.input-wrap input:focus { border-color: #36A5CA; }
.toggle-pw { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); background: none; border: none; color: #6BCCBD; font-size: 12px; cursor: pointer; padding: 4px 8px; }
.status-area { text-align: center; padding: 16px 0; }
.spinner { display: inline-block; width: 28px; height: 28px; border: 3px solid #1F333F; border-top-color: #36A5CA; border-radius: 50%; animation: spin 0.8s linear infinite; margin-bottom: 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.success-screen { text-align: center; padding: 32px 16px; }
.success-screen .check { font-size: 56px; color: #6BCCBD; margin-bottom: 12px; }
.success-screen h2 { color: #6BCCBD; margin-bottom: 10px; }
.success-screen .ip { font-size: 20px; font-weight: 700; color: #FCFDF0; margin: 16px 0; }
.success-screen p { color: #6BCCBD; font-size: 13px; }
.error-msg { color: #ff6b6b; font-size: 13px; text-align: center; padding: 8px; }
.footer { background: #0d2230; padding: 14px 16px; text-align: center; font-size: 10px; color: #1F333F55; border-top: 1px solid #1F333F; margin-top: auto; }
</style>
</head>
<body>
<div class="header">
  <h1>InkSlab</h1>
  <p>WiFi Setup</p>
</div>

<div class="content" id="setup-content">
  <div class="welcome">
    <h2>InkSlab WiFi Setup</h2>
    <p>Select your home WiFi network below and enter its password.</p>
    <p style="font-size:11px;color:#36A5CA;margin-top:8px">Page not loading? Open <b>10.42.0.1</b> in Safari or Chrome.</p>
  </div>

  <div class="card">
    <div class="section-title">
      <span>Available Networks</span>
      <button class="btn btn-secondary btn-sm" onclick="scanNetworks()">Scan</button>
    </div>
    <div class="network-list" id="network-list">
      <div style="text-align:center;padding:24px;color:#6BCCBD"><div class="spinner"></div><br>Loading networks...</div>
    </div>
  </div>

  <div class="card connect-form" id="connect-form">
    <h3>Connect to <span id="selected-ssid"></span></h3>
    <div id="password-section">
      <div class="input-wrap">
        <input type="password" id="wifi-password" placeholder="Enter WiFi password" autocomplete="off" autocapitalize="off">
        <button class="toggle-pw" onclick="togglePw()">show</button>
      </div>
    </div>
    <button class="btn btn-primary" id="btn-connect" onclick="doConnect()">Connect</button>
    <div style="text-align:center;margin-top:8px">
      <button class="btn btn-secondary btn-sm" onclick="cancelConnect()">Cancel</button>
    </div>
    <div id="status-area" class="status-area"></div>
  </div>
</div>

<div class="footer">Costa Mesa Tech Solutions</div>

<script>
var selectedSSID = '';
var selectedSecurity = '';
var _statusPoll = null;

function scanNetworks() {
  var el = document.getElementById('network-list');
  el.innerHTML = '<div style="text-align:center;padding:24px;color:#6BCCBD"><div class="spinner"></div><br>Scanning...</div>';
  var x = new XMLHttpRequest();
  x.open('GET', '/api/wifi/scan');
  x.timeout = 15000;
  x.onload = function() {
    try {
      var networks = JSON.parse(x.responseText);
      if (!networks.length) {
        el.innerHTML = '<div style="text-align:center;padding:24px;color:#6BCCBD">No networks found. Tap Scan to try again.</div>';
        return;
      }
      var html = '';
      for (var i = 0; i < networks.length; i++) {
        var n = networks[i];
        var bars = signalBars(n.signal);
        var lock = n.security ? '<span class="lock">&#128274;</span>' : '';
        var safe = n.ssid.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
        html += '<div class="network-item" onclick="selectNetwork(&#39;' + safe + '&#39;,&#39;' + (n.security || '').replace(/'/g, '&#39;') + '&#39;)">'
          + '<span class="network-ssid">' + safe + '</span>'
          + '<span class="network-meta">' + bars + lock + '</span>'
          + '</div>';
      }
      el.innerHTML = html;
    } catch(e) {
      el.innerHTML = '<div style="text-align:center;padding:24px;color:#ff6b6b">Scan failed. Tap Scan to retry.</div>';
    }
  };
  x.onerror = function() {
    el.innerHTML = '<div style="text-align:center;padding:24px;color:#ff6b6b">Scan failed. Tap Scan to retry.</div>';
  };
  x.ontimeout = x.onerror;
  x.send();
}

function signalBars(signal) {
  var bars = '';
  var heights = [4, 7, 10, 13, 16];
  for (var i = 0; i < 5; i++) {
    var on = signal >= (i + 1) * 20;
    bars += '<span class="signal-bar' + (on ? '' : ' off') + '" style="height:' + heights[i] + 'px"></span>';
  }
  return '<span class="signal-bars">' + bars + '</span>';
}

function selectNetwork(ssid, security) {
  selectedSSID = ssid;
  selectedSecurity = security;
  document.getElementById('selected-ssid').textContent = ssid;
  document.getElementById('connect-form').classList.add('open');
  document.getElementById('status-area').innerHTML = '';
  document.getElementById('btn-connect').disabled = false;
  var pwSection = document.getElementById('password-section');
  if (security) {
    pwSection.style.display = 'block';
    document.getElementById('wifi-password').value = '';
    document.getElementById('wifi-password').focus();
  } else {
    pwSection.style.display = 'none';
  }
}

function cancelConnect() {
  document.getElementById('connect-form').classList.remove('open');
  selectedSSID = '';
}

function togglePw() {
  var inp = document.getElementById('wifi-password');
  var btn = inp.nextElementSibling;
  if (inp.type === 'password') { inp.type = 'text'; btn.textContent = 'hide'; }
  else { inp.type = 'password'; btn.textContent = 'show'; }
}

function doConnect() {
  var password = document.getElementById('wifi-password').value;
  var btn = document.getElementById('btn-connect');
  var sa = document.getElementById('status-area');
  btn.disabled = true;
  sa.textContent = '';
  sa.className = 'status-area';
  var x = new XMLHttpRequest();
  x.open('POST', '/api/wifi/connect');
  x.setRequestHeader('Content-Type', 'application/json');
  x.timeout = 10000;
  x.onload = function() {
    try {
      var d = JSON.parse(x.responseText);
      if (!d.ok) {
        sa.textContent = d.error || 'Failed';
        sa.className = 'error-msg';
        btn.disabled = false;
        return;
      }
    } catch(e) {}
    /* Keep it simple — just update text, no innerHTML replacement.
       This avoids browser crashes when the network drops mid-render. */
    sa.innerHTML = '<strong style="color:#6BCCBD">Connecting\u2026</strong><br><br>\u26a0\ufe0f Your phone will disconnect from InkSlab-Setup in a moment \u2014 that is normal.<br><br>Watch the e\u2011ink display on your InkSlab:<br>\u2022 <b>If it shows your dashboard address</b> \u2014 reconnect your phone to your home WiFi and open that address.<br>\u2022 <b>If it shows \u201cConnection failed\u201d</b> \u2014 reconnect to InkSlab-Setup and try again with the correct password.';
    sa.style.color = '#6BCCBD';
    sa.style.fontSize = '14px';
    sa.style.lineHeight = '1.6';
  };
  x.onerror = function() {
    sa.textContent = 'Request failed. Try again.';
    sa.className = 'error-msg';
    btn.disabled = false;
  };
  x.ontimeout = x.onerror;
  x.send(JSON.stringify({ssid: selectedSSID, password: password}));
}

// On load: check for previous failure via XMLHttpRequest
var xs = new XMLHttpRequest();
xs.open('GET', '/api/wifi/status');
xs.timeout = 5000;
xs.onload = function() {
  try {
    var d = JSON.parse(xs.responseText);
    var cs = d.connect_status;
    if (cs && cs.status === 'failed') {
      selectNetwork(cs.ssid || '', '');
      var sa = document.getElementById('status-area');
      sa.textContent = cs.error || 'Connection failed. Check your password.';
      sa.className = 'error-msg';
      document.getElementById('btn-connect').disabled = false;
    }
  } catch(e) {}
};
xs.send();
scanNetworks();
</script>
</body>
</html>"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>InkSlab</title>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #010001; color: #D8E6E4; min-height: 100vh; display: flex; flex-direction: column; }
.header { background: #132E3E; padding: 18px 16px 14px; text-align: center; border-bottom: 2px solid #36A5CA; }
.header h1 { font-size: 22px; color: #FCFDF0; letter-spacing: 1px; }
.tabs { display: flex; background: #132E3E; border-bottom: 1px solid #1F333F; }
.tab { flex: 1; padding: 12px 8px; text-align: center; cursor: pointer; color: #6BCCBD; font-size: 13px; border-bottom: 2px solid transparent; transition: all 0.2s; opacity: 0.6; }
.tab.active { color: #36A5CA; border-bottom-color: #36A5CA; opacity: 1; }
.content { flex: 1; }
.panel { display: none; padding: 16px; }
.panel.active { display: block; }
.card { background: #16303E; border-radius: 8px; padding: 16px; margin-bottom: 12px; border: 1px solid #1F333F; }
.card h3 { color: #36A5CA; margin-bottom: 8px; font-size: 14px; }
.stat { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #1F333F; font-size: 14px; gap: 12px; align-items: baseline; }
.stat:last-child { border-bottom: none; }
.stat-label { color: #6BCCBD; white-space: nowrap; flex-shrink: 0; }
.stat-value { color: #FCFDF0; text-align: right; }
.btn { display: inline-block; padding: 10px 20px; border-radius: 6px; border: none; cursor: pointer; font-size: 14px; font-weight: 600; transition: all 0.2s; }
.btn-primary { background: #36A5CA; color: #FCFDF0; }
.btn-primary:hover { background: #2b8aaa; }
.btn-secondary { background: #1F333F; color: #D8E6E4; border: 1px solid #36A5CA33; }
.btn-secondary:hover { background: #263f4d; }
.btn-danger { background: #8b2020; color: #FCFDF0; }
.btn-danger:hover { background: #a52a2a; }
.btn-sm { padding: 6px 12px; font-size: 12px; }
.btn-block { display: block; width: 100%; text-align: center; }
.btn:active { transform: scale(0.95); }
.btn-primary:active { background: #1e7a99; transform: scale(0.95); }
.btn-secondary:active { background: #36A5CA; color: #FCFDF0; transform: scale(0.95); }
.btn-danger:active { background: #6b1515; transform: scale(0.95); }
.btn:disabled { opacity: 0.4; cursor: not-allowed; }
select, input[type=number] { background: #1F333F; color: #D8E6E4; border: 1px solid #36A5CA44; border-radius: 4px; padding: 8px; font-size: 14px; width: 100%; }
.form-group { margin-bottom: 12px; }
.form-group label { display: block; color: #6BCCBD; font-size: 12px; margin-bottom: 4px; }
.toggle { display: flex; align-items: center; gap: 8px; }
.toggle input[type=checkbox] { width: 18px; height: 18px; accent-color: #36A5CA; }
.set-item { background: #16303E; border-radius: 6px; margin-bottom: 4px; overflow: hidden; border: 1px solid #1F333F; }
.set-header { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; cursor: pointer; }
.set-header:hover { background: #1F333F; }
.set-name { font-size: 13px; font-weight: 600; color: #FCFDF0; }
.set-meta { font-size: 11px; color: #6BCCBD; }
.set-cards { display: none; padding: 0 12px 8px; }
.set-cards.open { display: block; }
.card-row { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; border-bottom: 1px solid #132E3E; font-size: 12px; }
.card-row label { flex: 1; cursor: pointer; display: flex; align-items: center; gap: 6px; color: #D8E6E4; }
.card-row input[type=checkbox] { accent-color: #36A5CA; }
.card-rarity { color: #6BCCBD; font-size: 11px; }
.rarity-chips { display: flex; flex-wrap: wrap; gap: 4px; padding: 4px 0 6px; }
.rarity-chip { padding: 3px 8px; border-radius: 12px; font-size: 11px; cursor: pointer; background: #1F333F; color: #6BCCBD; border: 1px solid #36A5CA33; transition: all 0.15s; }
.rarity-chip:hover { background: #263f4d; }
.rarity-chip:active { background: #36A5CA; color: #FCFDF0; transform: scale(0.95); }
.rarity-chip.active { background: #36A5CA; color: #FCFDF0; border-color: #36A5CA; }
.chip-count { font-size: 9px; opacity: 0.7; margin-left: 2px; }
/* Storage bar */
.storage-bar-wrap { margin: 8px 0 4px; }
.storage-bar-label { display: flex; justify-content: space-between; font-size: 11px; color: #6BCCBD; margin-bottom: 4px; }
.storage-bar { height: 22px; border-radius: 4px; overflow: hidden; display: flex; background: #1F333F; border: 1px solid #1F333F; }
.storage-seg { height: 100%; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 600; color: #FCFDF0; min-width: 0; overflow: hidden; white-space: nowrap; transition: width 0.3s; }
.storage-seg.seg-pokemon { background: #36A5CA; }
.storage-seg.seg-mtg { background: #6BCCBD; }
.storage-seg.seg-other { background: #E8786B; }
.storage-seg.seg-free { background: #1F333F; color: #6BCCBD; }
.storage-legend { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; font-size: 11px; }
.storage-legend-item { display: flex; align-items: center; gap: 4px; color: #D8E6E4; }
.storage-legend-dot { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
/* Rarity filter toggles */
.rarity-filter-wrap { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.rarity-toggle { display: inline-flex; align-items: center; gap: 4px; padding: 6px 10px; border-radius: 16px; font-size: 12px; cursor: pointer; border: 1px solid #36A5CA44; background: #1F333F; color: #D8E6E4; transition: all 0.15s; user-select: none; -webkit-user-select: none; }
.rarity-toggle:active { transform: scale(0.95); }
.rarity-toggle.selected { background: #36A5CA; color: #FCFDF0; border-color: #36A5CA; }
.rarity-toggle .rt-count { background: rgba(0,0,0,0.2); border-radius: 8px; padding: 0 5px; font-size: 10px; font-weight: 700; margin-left: 2px; }
.rarity-toggle.selected .rt-count { background: rgba(255,255,255,0.25); }
.rarity-toggle .rt-check { font-size: 10px; width: 12px; }
.rarity-filter-actions { display: flex; gap: 6px; margin-bottom: 8px; }
.card-preview-btn { cursor: pointer; color: #36A5CA; font-size: 11px; margin-left: 6px; text-decoration: underline; }
.log-box { background: #0a1a22; border-radius: 6px; padding: 10px; font-family: monospace; font-size: 11px; max-height: 300px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; color: #6BCCBD; border: 1px solid #1F333F; }
.badge { display: inline-block; background: #6BCCBD; color: #010001; border-radius: 10px; padding: 1px 7px; font-size: 10px; margin-left: 4px; font-weight: 700; }
.flex-row { display: flex; gap: 8px; flex-wrap: wrap; }
.flex-row > * { flex: 1 1 calc(50% - 4px); min-width: 0; box-sizing: border-box; }
.preview-img { display: block; max-width: 150px; border-radius: 6px; border: 2px solid #1F333F; width: 100%; cursor: pointer; }
@media (min-width: 600px) { .preview-img { max-width: 240px; } #st-preview-wrap { max-width: 240px !important; } }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
.preview-spin { animation: spin 1.2s linear infinite; display: inline-block; }
.footer { background: #132E3E; padding: 14px 16px; text-align: center; font-size: 10px; color: #1F333F; border-top: 1px solid #1F333F; margin-top: auto; }
.footer a { color: #36A5CA55; text-decoration: none; }
.footer .ip { color: #6BCCBD88; margin-top: 4px; }

/* Modal overlay */
.modal-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(1,0,1,0.85); z-index: 100; justify-content: center; align-items: center; }
.modal-overlay.open { display: flex; }
.modal-content { text-align: center; padding: 16px; max-width: 300px; }
.modal-content img { max-width: 260px; border-radius: 8px; border: 2px solid #36A5CA; }
.modal-content p { margin-top: 8px; color: #FCFDF0; font-size: 13px; }
.modal-close { margin-top: 12px; }

/* Card queue (prev/next) */
.q-section { margin-bottom: 10px; }
.q-label { font-size: 11px; color: #6BCCBD; font-weight: 600; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
.q-list { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 4px; }
.q-card { text-align: center; flex-shrink: 0; width: 70px; cursor: pointer; }
.q-thumb { width: 64px; height: auto; border-radius: 4px; border: 1.5px solid #1F333F; display: block; }
.q-num { font-size: 10px; color: #FCFDF0; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.q-rarity { font-size: 9px; color: #6BCCBD; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* iPod-style player controls */
.player-controls { display: flex; justify-content: center; align-items: center; gap: 12px; margin-bottom: 12px; }
.player-btn { width: 48px; height: 48px; border-radius: 50%; border: 2px solid #36A5CA; background: #16303E; color: #36A5CA; font-size: 20px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.15s; }
.player-btn:active { transform: scale(0.9); background: #36A5CA; color: #FCFDF0; }
.player-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.player-btn.play-pause { width: 56px; height: 56px; font-size: 24px; border-width: 3px; }
.player-btn.play-pause.paused { border-color: #6BCCBD; color: #6BCCBD; }

/* Countdown timer */
.countdown { text-align: center; font-size: 13px; color: #6BCCBD; margin-bottom: 12px; min-height: 18px; }
.countdown .time { color: #36A5CA; font-weight: 600; font-variant-numeric: tabular-nums; }
.countdown .paused-label { color: #6BCCBD; font-weight: 600; }

/* Search */
.search-wrap { position: relative; margin-bottom: 12px; }
.search-wrap input { width: 100%; background: #1F333F; color: #D8E6E4; border: 1px solid #36A5CA44; border-radius: 6px; padding: 10px 12px 10px 32px; font-size: 14px; }
.search-wrap input:focus { outline: none; border-color: #36A5CA; }
.search-icon { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: #6BCCBD; font-size: 14px; pointer-events: none; }
.search-results { max-height: 400px; overflow-y: auto; }
.search-result { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #1F333F; font-size: 12px; }
.search-result-name { color: #FCFDF0; font-weight: 600; }
.search-result-set { color: #6BCCBD; font-size: 11px; }
.search-result-rarity { color: #6BCCBD; font-size: 11px; }
.search-filters { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.search-filter-chip { display: inline-flex; align-items: center; gap: 4px; background: #36A5CA22; border: 1px solid #36A5CA88; color: #D8E6E4; border-radius: 16px; padding: 4px 8px 4px 10px; font-size: 12px; }
.search-filter-chip .sfc-count { color: #6BCCBD; font-size: 11px; }
.search-filter-chip .sfc-x { cursor: pointer; color: #6BCCBD; font-size: 14px; line-height: 1; margin-left: 2px; padding: 0 2px; border-radius: 50%; }
.search-filter-chip .sfc-x:hover { color: #ff6b6b; background: #ff6b6b22; }

/* Plugins */
.plugin-list { display: flex; flex-direction: column; gap: 8px; }
.plugin-item { border: 1px solid #1F333F; border-radius: 8px; padding: 10px 12px; background: #16303E; }
.plugin-item.active { border-color: #36A5CA; box-shadow: inset 0 0 0 1px #36A5CA33; }
.plugin-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 4px; }
.plugin-name { font-size: 14px; color: #FCFDF0; font-weight: 600; }
.plugin-badge { font-size: 10px; color: #010001; background: #6BCCBD; border-radius: 999px; padding: 2px 8px; text-transform: uppercase; letter-spacing: 0.4px; }
.plugin-meta { font-size: 11px; color: #8899a6; }
.schedule-list { display: flex; flex-direction: column; gap: 8px; }
.schedule-item { border: 1px solid #1F333F; border-radius: 8px; padding: 10px 12px; background: #16303E; }
.schedule-time { font-size: 12px; color: #6BCCBD; font-weight: 600; }
.schedule-title { font-size: 13px; color: #FCFDF0; font-weight: 600; margin-top: 2px; }
.plugin-controls { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 8px; }
.plugin-toggle { display: flex; align-items: center; gap: 6px; color: #D8E6E4; font-size: 12px; }
.plugin-note { font-size: 11px; color: #8899a6; margin-top: 6px; }
.plugin-settings { margin-top: 10px; padding-top: 10px; border-top: 1px solid #1F333F; display: flex; flex-direction: column; gap: 8px; }
.plugin-settings-row label { display: block; color: #6BCCBD; font-size: 11px; margin-bottom: 4px; }
.plugin-settings-row input, .plugin-settings-row select { width: 100%; }
.context-note { font-size: 12px; color: #6BCCBD; margin: 6px 0 10px; line-height: 1.45; }
.subtle-note { font-size: 11px; color: #8899a6; margin-top: 6px; line-height: 1.4; }
.schedule-editor { display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
.schedule-editor-item { border: 1px solid #1F333F; border-radius: 8px; padding: 10px; background: #132E3E; }
.schedule-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.plugin-checks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; margin-top: 8px; }
.plugin-checks label { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #D8E6E4; }
.pill-note { display: inline-flex; align-items: center; gap: 6px; background: #1F333F; border: 1px solid #36A5CA44; color: #D8E6E4; border-radius: 999px; padding: 4px 10px; font-size: 11px; margin-top: 8px; }
</style>
</head>
<body>

<div class="header">
  <h1>InkSlab</h1>
</div>

<div class="tabs">
  <div class="tab active" id="tab-btn-display" data-tab="display" onclick="showTab('display')">Display</div>
  <div class="tab" id="tab-btn-settings" data-tab="settings" onclick="showTab('settings')">Dashboard</div>
  <div class="tab" id="tab-btn-collection" data-tab="collection" onclick="showTab('collection')">Library</div>
  <div class="tab" id="tab-btn-downloads" data-tab="downloads" onclick="showTab('downloads')">Data</div>
</div>

<div class="content">

<!-- DISPLAY TAB -->
<div id="tab-display" class="panel active">
  <div class="card">
    <h3>Now Showing</h3>
    <div id="st-preview-wrap" style="position:relative;max-width:150px;margin:12px auto 0">
      <img id="st-preview" class="preview-img" style="margin:0" src="/api/card_image" onerror="this.style.display='none'" onload="this.style.display='block'" onclick="showCurrentPreview()">
      <div id="st-preview-loading" style="display:none;position:absolute;top:0;left:0;right:0;bottom:0;background:rgba(22,48,62,0.9);border-radius:6px;border:2px solid #36A5CA;flex-direction:column;justify-content:center;align-items:center;color:#36A5CA;font-size:12px;font-weight:600">
        <div style="font-size:24px;margin-bottom:6px">&#8635;</div>
        <div id="st-preview-loading-text">Loading...</div>
      </div>
    </div>
    <div style="margin-top:12px">
      <div class="stat"><span class="stat-label">Card</span><span class="stat-value" id="st-card">&mdash;</span></div>
      <div class="stat"><span class="stat-label">Set</span><span class="stat-value" id="st-set">&mdash;</span></div>
      <div class="stat"><span class="stat-label">Rarity</span><span class="stat-value" id="st-rarity">&mdash;</span></div>
      <div class="stat"><span class="stat-label">TCG</span><span class="stat-value" id="st-tcg">&mdash;</span></div>
      <div class="stat"><span class="stat-label">Cards in Deck</span><span class="stat-value" id="st-total">&mdash;</span></div>
      <div class="stat" id="st-error-row" style="display:none"><span class="stat-label" style="color:#ff6b6b">Status</span><span class="stat-value" style="color:#ff6b6b;font-size:12px" id="st-error"></span></div>
    </div>
  </div>
  <div class="countdown" id="countdown"></div>
  <div class="player-controls">
    <button class="player-btn" id="btn-prev" onclick="prevCard()" title="Previous Card">&#9664;</button>
    <button class="player-btn play-pause" id="btn-pause" onclick="togglePause()" title="Pause/Play">&#10074;&#10074;</button>
    <button class="player-btn" id="btn-next" onclick="nextCard()" title="Next Card">&#9654;</button>
  </div>
  <div class="card" id="queue-card" style="display:none">
    <div class="q-section" id="q-next-wrap" style="display:none">
      <div class="q-label">Up Next</div>
      <div class="q-list" id="q-next-list"></div>
    </div>
    <div class="q-section" id="q-prev-wrap" style="display:none;margin-top:10px">
      <div class="q-label">Previously</div>
      <div class="q-list" id="q-prev-list"></div>
    </div>
  </div>
  <div class="card" id="display-mode-card">
    <h3>Display Mode</h3>
    <p id="display-mode-note" class="context-note">Loading display behavior...</p>
    <div id="quick-switch-card" style="display:none">
      <p class="subtle-note">Manual TCG switching is useful for testing, but your saved display plan is the normal source of truth.</p>
      <div class="flex-row" id="quick-switch-btns"></div>
    </div>
  </div>
</div>

<!-- SETTINGS TAB -->
<div id="tab-settings" class="panel">
  <div class="card">
    <h3>Core Display</h3>
    <p class="context-note">These are the shared device settings that apply to the whole dashboard, no matter which plugins you enable.</p>
    <div class="form-group" id="default-tcg-group">
      <label>Default TCG Plugin</label>
      <select id="cfg-tcg"></select>
      <div class="subtle-note">Legacy compatibility control for the current card renderer. If you only use weather, traffic, or other ambient plugins later, you can ignore this.</div>
    </div>
    <div class="form-group">
      <label>Slab Header</label>
      <select id="cfg-header-mode">
        <option value="normal">Normal (white bg, black text)</option>
        <option value="inverted">Inverted (black bg, white text)</option>
        <option value="off">Off (full-screen card art)</option>
      </select>
    </div>
    <div class="form-group">
      <label>Rotation Angle</label>
      <select id="cfg-rotation"><option value="270">Default</option><option value="90">Upside Down</option></select>
    </div>
    <div class="form-group">
      <label>Day Interval (minutes)</label>
      <input type="number" id="cfg-day-interval" min="1" max="120" value="10">
    </div>
    <div class="form-group">
      <label>Night Interval (minutes)</label>
      <input type="number" id="cfg-night-interval" min="1" max="480" value="60">
    </div>
    <div class="form-group">
      <label>Timezone</label>
      <div style="background:#1F333F;border-radius:4px;padding:6px 10px;margin-bottom:6px;font-size:12px;color:#6BCCBD">
        Pi thinks it is: <span id="pi-time" style="font-weight:600;color:#D8E6E4">--:-- --</span>
        <span id="tz-hint" style="color:#8899a6;margin-left:6px"></span>
      </div>
      <div class="flex-row" style="gap:8px;align-items:center">
        <input type="number" id="cfg-tz-offset" min="-12" max="14" step="1" placeholder="Leave blank to use Pi system time" style="flex:2">
        <button class="btn btn-secondary btn-sm" onclick="autoDetectTimezone()" style="flex:1;white-space:nowrap">Auto-Detect</button>
      </div>
      <input type="hidden" id="cfg-tz-name" value="">
      <small style="color:#6BCCBD;font-size:11px">Auto-detected from your phone. Handles daylight saving automatically. You shouldn't need to change this.</small>
    </div>
    <div class="form-group">
      <label>Day Start (hour, 24h)</label>
      <input type="number" id="cfg-day-start" min="0" max="23" value="7">
    </div>
    <div class="form-group">
      <label>Day End (hour, 24h)</label>
      <input type="number" id="cfg-day-end" min="0" max="23" value="23">
    </div>
    <div class="form-group">
      <label>Color Saturation</label>
      <input type="number" id="cfg-saturation" min="0.5" max="5.0" step="0.1" value="2.5">
    </div>
    <div class="form-group">
      <div class="toggle">
        <input type="checkbox" id="cfg-collection">
        <label for="cfg-collection">TCG-only: show only My Cards</label>
      </div>
      <div class="subtle-note">Only affects card plugins. Ambient plugins like weather or traffic ignore this.</div>
    </div>
    <button class="btn btn-primary btn-block" onclick="saveSettings()">Save Settings</button>
  </div>
  <div class="card">
    <h3>Plugin Manager</h3>
    <p class="context-note">Enable only the plugins this device should actually use. A weather-only or traffic-only build should feel just as natural here as a TCG build.</p>
    <div id="plugin-summary" style="font-size:12px;color:#6BCCBD;margin-bottom:10px">Loading plugins...</div>
    <div id="plugin-list" class="plugin-list"></div>
    <button class="btn btn-primary btn-block" style="margin-top:10px" onclick="savePluginConfig()">Save Plugin Selection</button>
    <p style="font-size:11px;color:#8899a6;margin-top:10px;text-align:center">Built-in roadmap plugins already appear here so the architecture stays honest while we turn them into real modules.</p>
  </div>
  <div class="card">
    <h3>Display Plan</h3>
    <p class="context-note">Choose whether the screen stays on one plugin or rotates through a calm schedule like cards, weather, and news.</p>
    <div id="display-plan-summary" style="font-size:12px;color:#6BCCBD;margin-bottom:10px">Loading display plan...</div>
    <div id="display-plan-list" class="schedule-list"></div>
    <div class="form-group" style="margin-top:12px">
      <label>Display Mode</label>
      <select id="display-mode">
        <option value="single">Single Plugin</option>
        <option value="schedule">Scheduled Rotation</option>
      </select>
    </div>
    <div class="form-group">
      <label>Single Plugin</label>
      <select id="single-plugin"></select>
    </div>
    <div class="flex-row" style="margin-bottom:8px">
      <button class="btn btn-secondary btn-block" onclick="addDisplayPlanBlock()">Add Time Block</button>
      <button class="btn btn-primary btn-block" onclick="saveDisplayPlan()">Save Display Plan</button>
    </div>
    <div id="display-plan-editor" class="schedule-editor"></div>
    <p style="font-size:11px;color:#8899a6;margin-top:10px;text-align:center">The scheduler already understands rotation. As more plugins become runnable, this is where the dashboard starts feeling truly modular.</p>
  </div>
  <div class="card">
    <h3>Software Update</h3>
    <div id="update-info" style="margin-bottom:10px;font-size:13px;color:#6BCCBD;cursor:default;-webkit-user-select:none;user-select:none" onclick="adminTap()">Loading version...</div>
    <div class="form-group" style="margin-bottom:8px">
      <label>Update Branch (testing devices only)</label>
      <select id="update-branch" onchange="saveUpdateBranch()"></select>
      <div id="update-branch-hint" style="margin-top:6px;font-size:11px;color:#8899a6"></div>
    </div>
    <div class="flex-row" style="margin-bottom:8px">
      <button class="btn btn-secondary btn-block" onclick="checkUpdate()">Check for Updates</button>
      <button class="btn btn-primary btn-block" id="btn-update-now" style="display:none" onclick="startUpdate()">Update Now</button>
    </div>
    <div id="update-progress" style="display:none">
      <div style="background:#1F333F;border-radius:4px;height:8px;margin:8px 0"><div id="update-bar" style="height:100%;border-radius:4px;background:#36A5CA;width:0%;transition:width 0.5s"></div></div>
      <div id="update-stage" style="font-size:12px;color:#6BCCBD;text-align:center"></div>
    </div>
    <p style="font-size:11px;color:#8899a6;margin-top:10px;text-align:center">Buttons not responding? If you have this dashboard open in multiple tabs, close the extras — that's the most common cause. Or open a new tab and go to the same address. Ctrl+Shift+R (Win) / Cmd+Shift+R (Mac) force-refreshes the cache. On mobile, open a new tab or use private / incognito.</p>
  </div>
  <div class="card">
    <h3>WiFi Network</h3>
    <div id="wifi-info" style="font-size:13px;color:#6BCCBD;margin-bottom:10px">Checking WiFi...</div>
    <button class="btn btn-secondary btn-block" onclick="changeWifi()">Change WiFi Network</button>
    <p style="font-size:11px;color:#8899a6;margin-top:8px;text-align:center">Buttons not responding? If you have this dashboard open in multiple tabs, close the extras — that's the most common cause. Or open a new tab and go to the same address. Ctrl+Shift+R (Win) / Cmd+Shift+R (Mac) force-refreshes the cache. On mobile, open a new tab or use private / incognito.</p>
  </div>
  <div class="card" id="admin-panel" style="display:none;border:1px solid #ff6b6b33">
    <h3 style="color:#ff6b6b">Prepare for New Owner</h3>
    <p style="color:#6BCCBD;font-size:12px;margin-bottom:10px">Wipes everything (WiFi, cards, settings) and shows a welcome screen on the display. After it finishes, unplug the unit — it's ready to ship.</p>
    <button class="btn btn-block" style="background:#ff6b6b;color:#010001;font-weight:600" onclick="factoryReset(this)">Prepare for New Owner</button>
  </div>
</div>

<!-- COLLECTION TAB -->
<div id="tab-collection" class="panel">
  <div class="card">
    <h3>Plugin Library Tools</h3>
    <p class="context-note">This area is only for card-library plugins today. If your device only runs weather, traffic, news, or other ambient modules, you can ignore this whole tab.</p>
    <div class="pill-note">TCG-specific tools</div>
  </div>
  <div class="card">
    <h3>My Cards</h3>
    <p style="color:#6BCCBD;font-size:12px;margin-bottom:8px">Pick the cards you want to display. Use any criteria you like — favorites, a themed set, a wish list, or cards you collect. Enable "My Cards mode" in Settings to only show these.</p>
    <button class="btn btn-secondary btn-sm" onclick="clearCollection()">Clear All</button>
  </div>
  <div class="card">
    <h3>Search Cards</h3>
    <p style="color:#6BCCBD;font-size:12px;margin-bottom:8px">Find a card by name and add all versions to your list.</p>
    <div id="search-filters" class="search-filters" style="display:none"></div>
    <div class="search-wrap">
      <span class="search-icon">&#128269;</span>
      <input type="text" id="search-input" placeholder="Search by card name..." oninput="debounceSearch()">
    </div>
    <div id="search-results"></div>
  </div>
  <div class="card">
    <h3>Filter by Rarity</h3>
    <p style="color:#6BCCBD;font-size:12px;margin-bottom:8px">Toggle rarities on/off across all sets. Checked = cards of that rarity are in your list.</p>
    <div class="rarity-filter-actions">
      <button class="btn btn-secondary btn-sm" onclick="selectAllRarities(true)">Select All</button>
      <button class="btn btn-secondary btn-sm" onclick="selectAllRarities(false)">Deselect All</button>
    </div>
    <div class="rarity-filter-wrap" id="rarity-chips"></div>
    <div id="rarity-result" style="color:#6BCCBD;font-size:12px;margin-top:6px"></div>
  </div>
  <div id="sets-list"></div>
</div>

<!-- DOWNLOADS TAB -->
<div id="tab-downloads" class="panel">
  <div class="card">
    <h3>Data & Imports</h3>
    <p class="context-note">This tab currently manages card downloads and custom image sets. As plugins mature, this will become the home for plugin data sources and imports more broadly.</p>
    <div class="pill-note">Currently focused on TCG and image plugins</div>
  </div>
  <div class="card">
    <h3>Storage</h3>
    <div id="storage-info"></div>
  </div>
  <div class="card">
    <h3>Download Cards</h3>
    <div id="dl-buttons"></div>
    <div id="dl-mtg-since" style="display:none;margin-top:8px" class="form-group">
      <label>Download MTG since year:</label>
      <div class="flex-row">
        <input type="number" id="dl-since" min="1993" max="2030" value="2020" style="flex:2">
        <button class="btn btn-secondary" id="btn-dl-mtg-since" onclick="startDownload('mtg', document.getElementById('dl-since').value)" style="flex:1">Go</button>
      </div>
    </div>
    <button class="btn btn-danger btn-block" id="btn-dl-stop" style="display:none" onclick="stopDownload()">Stop Download</button>
  </div>
  <div class="card">
    <h3>Download Log</h3>
    <div id="dl-status" style="font-size:12px;margin-bottom:8px;color:#6BCCBD">Idle</div>
    <div id="dl-log" class="log-box">No download running.</div>
  </div>
  <div class="card">
    <h3>Custom Images</h3>
    <p style="color:#6BCCBD;font-size:12px;margin-bottom:8px">Upload your own images. Each folder is a set.</p>
    <div class="flex-row" style="margin-bottom:8px">
      <input type="text" id="custom-folder-name" placeholder="New folder name..." style="flex:2;background:#1F333F;color:#D8E6E4;border:1px solid #36A5CA44;border-radius:4px;padding:8px;font-size:14px">
      <button class="btn btn-primary" onclick="createCustomFolder()" style="flex:1">Create</button>
    </div>
    <div id="custom-folders"></div>
    <p style="font-size:11px;color:#8899a6;margin-top:10px;text-align:center">Buttons not responding? If you have this dashboard open in multiple tabs, close the extras — that's the most common cause. Or open a new tab and go to the same address. Ctrl+Shift+R (Win) / Cmd+Shift+R (Mac) force-refreshes the cache. On mobile, open a new tab or use private / incognito.</p>
  </div>
  <div class="card">
    <h3>Delete Data</h3>
    <p style="color:#6BCCBD;font-size:12px;margin-bottom:8px">Choose one data source to clear. This scales better as InkSlab gains more plugins and libraries.</p>
    <div class="form-group" style="margin-bottom:8px">
      <label>Data Source</label>
      <select id="delete-source"></select>
    </div>
    <button class="btn btn-danger btn-block" id="delete-source-btn" onclick="deleteSelectedData()">Delete Selected Data</button>
    <p id="delete-source-note" class="subtle-note">Select a source above to see what will be removed.</p>
    <p style="font-size:11px;color:#8899a6;margin-top:10px;text-align:center">Buttons not responding? If you have this dashboard open in multiple tabs, close the extras — that's the most common cause. Or open a new tab and go to the same address. Ctrl+Shift+R (Win) / Cmd+Shift+R (Mac) force-refreshes the cache. On mobile, open a new tab or use private / incognito.</p>
  </div>
</div>

</div><!-- /content -->

<!-- Card preview modal -->
<div class="modal-overlay" id="preview-modal" onclick="closePreview()">
  <div class="modal-content" onclick="event.stopPropagation()">
    <img id="preview-img" src="">
    <p id="preview-name"></p>
    <button class="btn btn-secondary btn-sm modal-close" onclick="closePreview()">Close</button>
  </div>
</div>

<div id="toast" style="display:none;position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:#6BCCBD;color:#010001;padding:10px 24px;border-radius:20px;font-size:13px;font-weight:600;z-index:200;opacity:0;transition:opacity 0.3s;pointer-events:none;"></div>

<div class="footer">
  <div>Costa Mesa Tech Solutions &mdash; a brand of Pine Heights Ventures LLC</div>
  <div class="ip" id="footer-ip"></div>
</div>

<script>
const API = '';

// --- HTML escaping for safe innerHTML ---
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function objectFromEntriesCompat(entries) {
  var out = {};
  (entries || []).forEach(function(entry) {
    if (!entry || entry.length < 2) return;
    out[entry[0]] = entry[1];
  });
  return out;
}

// --- Tab persistence ---
function showTab(name) {
  localStorage.setItem('inkslab_tab', name);
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'collection') { loadSets(); loadRarities(); loadFavorites(); }
  if (name === 'settings') { loadSettings(); loadWifiInfo(); }
  if (name === 'downloads') { loadStorage(); pollDownload(); loadCustomFolders(); }
  if (name === 'display') refreshStatus();
}

// --- Toast ---
function showToast(msg, duration) {
  duration = duration || 2000;
  var el = document.getElementById('toast');
  el.textContent = msg;
  el.style.display = 'block';
  el.offsetHeight; // force reflow
  el.style.opacity = '1';
  setTimeout(function() {
    el.style.opacity = '0';
    setTimeout(function() { el.style.display = 'none'; }, 300);
  }, duration);
}

// --- Display ---
var _lastStatus = {};
var _rapidPoll = null;
var _pendingAction = false;
var _mainPoll = null;
var _countdownTimer = null;
var _pluginUiState = null;

function startMainPoll() {
  if (_mainPoll) clearInterval(_mainPoll);
  _mainPoll = setInterval(refreshStatus, 10000);
}

function showPreviewLoading(msg) {
  var overlay = document.getElementById('st-preview-loading');
  document.getElementById('st-preview-loading-text').textContent = msg || 'Loading...';
  overlay.style.display = 'flex';
  overlay.querySelector('div').className = 'preview-spin';
}
function hidePreviewLoading() {
  var overlay = document.getElementById('st-preview-loading');
  overlay.style.display = 'none';
}

function updateCountdown() {
  var el = document.getElementById('countdown');
  if (_lastStatus.paused) {
    el.innerHTML = '<span class="paused-label">Paused</span>';
    return;
  }
  var nc = _lastStatus.next_change;
  if (!nc) { el.textContent = ''; return; }
  var remain = Math.max(0, nc - Math.floor(Date.now() / 1000));
  if (remain <= 0) { el.innerHTML = '<span class="time">Changing soon...</span>'; return; }
  var m = Math.floor(remain / 60);
  var s = remain % 60;
  el.innerHTML = 'Next card in <span class="time">' + m + ':' + (s < 10 ? '0' : '') + s + '</span>';
}

function startCountdown() {
  if (_countdownTimer) clearInterval(_countdownTimer);
  _countdownTimer = setInterval(updateCountdown, 1000);
  updateCountdown();
}

var _lastQueueKey = '';
function renderQueue(d) {
  var tcg = (d.tcg || '').toLowerCase();
  var prev = d.prev_cards || [];
  var next = d.next_cards || [];
  // Skip re-render if queue hasn't changed (avoids image flash on every poll)
  var queueKey = JSON.stringify(prev.map(function(c){return c.card_id})) + '|' + JSON.stringify(next.map(function(c){return c.card_id}));
  if (queueKey === _lastQueueKey) return;
  _lastQueueKey = queueKey;
  var queueCard = document.getElementById('queue-card');
  if (!prev.length && !next.length) { queueCard.style.display = 'none'; return; }
  queueCard.style.display = 'block';
  var nextWrap = document.getElementById('q-next-wrap');
  var prevWrap = document.getElementById('q-prev-wrap');
  if (next.length) {
    nextWrap.style.display = 'block';
    document.getElementById('q-next-list').innerHTML = next.map(function(c) {
      return '<div class="q-card card-preview-btn" data-set="' + esc(c.set_id) + '" data-card="' + esc(c.card_id) + '" data-label="' + esc(c.card_num) + ' ' + esc(c.set_info) + '">'
        + '<img class="q-thumb" src="/api/card_image/' + encodeURIComponent(tcg) + '/' + encodeURIComponent(c.set_id) + '/' + encodeURIComponent(c.card_id) + '" onerror="this.style.display=\\'none\\'">'
        + '<div class="q-num">' + esc(c.card_num) + '</div>'
        + '<div class="q-rarity">' + esc(c.rarity || '') + '</div></div>';
    }).join('');
  } else { nextWrap.style.display = 'none'; }
  if (prev.length) {
    prevWrap.style.display = 'block';
    document.getElementById('q-prev-list').innerHTML = prev.map(function(c) {
      return '<div class="q-card card-preview-btn" data-set="' + esc(c.set_id) + '" data-card="' + esc(c.card_id) + '" data-label="' + esc(c.card_num) + ' ' + esc(c.set_info) + '">'
        + '<img class="q-thumb" src="/api/card_image/' + encodeURIComponent(tcg) + '/' + encodeURIComponent(c.set_id) + '/' + encodeURIComponent(c.card_id) + '" onerror="this.style.display=\\'none\\'">'
        + '<div class="q-num">' + esc(c.card_num) + '</div>'
        + '<div class="q-rarity">' + esc(c.rarity || '') + '</div></div>';
    }).join('');
  } else { prevWrap.style.display = 'none'; }
}

function updatePauseBtn(paused) {
  var btn = document.getElementById('btn-pause');
  if (paused) {
    btn.innerHTML = '&#9654;';
    btn.classList.add('paused');
    btn.title = 'Resume';
  } else {
    btn.innerHTML = '&#10074;&#10074;';
    btn.classList.remove('paused');
    btn.title = 'Pause';
  }
}

function refreshStatus() {
  fetch(API + '/api/status').then(r => r.json()).then(d => {
    document.getElementById('st-tcg').textContent = (d.tcg || '\\u2014').toUpperCase();
    var errRow = document.getElementById('st-error-row');
    var errEl = document.getElementById('st-error');
    if (d.pending) {
      errEl.textContent = d.pending;
      errRow.style.display = 'flex';
      errEl.style.color = '#36A5CA';
      errRow.querySelector('.stat-label').style.color = '#36A5CA';
      errRow.querySelector('.stat-label').textContent = 'Status';
    } else if (d.display_updating) {
      errEl.textContent = 'Updating display...';
      errRow.style.display = 'flex';
      errEl.style.color = '#36A5CA';
      errRow.querySelector('.stat-label').style.color = '#36A5CA';
      errRow.querySelector('.stat-label').textContent = 'Status';
    } else if (d.error) {
      errEl.textContent = d.error;
      errRow.style.display = 'flex';
      errEl.style.color = '#ff6b6b';
      errRow.querySelector('.stat-label').style.color = '#ff6b6b';
      errRow.querySelector('.stat-label').textContent = 'Status';
    } else {
      errRow.style.display = 'none';
    }
    if (!d.pending) {
      document.getElementById('st-card').textContent = d.card_num || '\\u2014';
      document.getElementById('st-set').textContent = d.set_info || '\\u2014';
      document.getElementById('st-rarity').textContent = d.rarity || '\\u2014';
      document.getElementById('st-total').textContent = d.total_cards || '\\u2014';
      var img = document.getElementById('st-preview');
      if (d.card_path) {
        var needsReload = (d.card_path !== _lastStatus.card_path
          || d.tcg !== _lastStatus.tcg
          || (_lastStatus.pending && !d.pending));
        if (needsReload) {
          img.src = '/api/card_image?t=' + Date.now();
        }
      } else {
        img.style.display = 'none';
      }
      // Show/hide loading overlay based on display state
      if (d.display_updating) {
        showPreviewLoading('Updating display...');
      } else {
        hidePreviewLoading();
      }
      renderQueue(d);
    }
    // Update pause button and countdown
    updatePauseBtn(d.paused);
    updateCountdown();
    var startupBlocked = !!(d.pending && String(d.pending).toLowerCase().indexOf('starting up') === 0);
    var controlsBlocked = startupBlocked || (!!d.display_updating && !d.card_path);
    var nextBtn = document.getElementById('btn-next');
    var prevBtn = document.getElementById('btn-prev');
    var pauseBtn = document.getElementById('btn-pause');
    if (nextBtn) nextBtn.disabled = controlsBlocked;
    if (pauseBtn) pauseBtn.disabled = controlsBlocked;
    // Disable prev button if no history or if startup/display prep is still in progress
    if (prevBtn) prevBtn.disabled = controlsBlocked || !(d.prev_cards && d.prev_cards.length);
    // Stop rapid polling once fully settled (not pending AND not updating display)
    if (_rapidPoll && !d.pending && !d.display_updating) {
      clearInterval(_rapidPoll);
      _rapidPoll = null;
      _pendingAction = false;
      startMainPoll();
    }
    _lastStatus = d;
    var piTimeEl = document.getElementById('pi-time');
    if (piTimeEl && d.server_time) piTimeEl.textContent = d.server_time;
  }).catch(() => {});
}

function startRapidPoll() {
  _pendingAction = true;
  if (_mainPoll) { clearInterval(_mainPoll); _mainPoll = null; }
  if (_rapidPoll) clearInterval(_rapidPoll);
  _rapidPoll = setInterval(refreshStatus, 2000);
  setTimeout(function() {
    if (_rapidPoll) { clearInterval(_rapidPoll); _rapidPoll = null; _pendingAction = false; startMainPoll(); }
  }, 60000);
}

function setOptimisticLoading(msg) {
  showPreviewLoading(msg);
  document.getElementById('st-card').textContent = '\\u2014';
  document.getElementById('st-set').textContent = '\\u2014';
  document.getElementById('st-rarity').textContent = '\\u2014';
  var errRow = document.getElementById('st-error-row');
  errRow.style.display = 'flex';
  errRow.querySelector('.stat-label').textContent = 'Status';
  errRow.querySelector('.stat-label').style.color = '#36A5CA';
  var errEl = document.getElementById('st-error');
  errEl.textContent = msg;
  errEl.style.color = '#36A5CA';
}

function nextCard() {
  var btn = document.getElementById('btn-next');
  btn.disabled = true;
  fetch(API + '/api/next', {method:'POST'})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      btn.disabled = false;
      if (d && d.ok === false) { showToast(d.error || 'Not ready yet'); return; }
      showToast('Next card...');
      setOptimisticLoading('Loading next card...');
      startRapidPoll();
    })
    .catch(function() { btn.disabled = false; showToast('Failed'); });
}

function prevCard() {
  var btn = document.getElementById('btn-prev');
  btn.disabled = true;
  fetch(API + '/api/prev', {method:'POST'})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      btn.disabled = false;
      if (d && d.ok === false) { showToast(d.error || 'Not ready yet'); return; }
      showToast('Previous card...');
      setOptimisticLoading('Loading previous card...');
      startRapidPoll();
    })
    .catch(function() { btn.disabled = false; showToast('Failed'); });
}

function togglePause() {
  fetch(API + '/api/pause', {method:'POST'})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      updatePauseBtn(d.paused);
      _lastStatus.paused = d.paused;
      // Use server-computed next_change so resume preserves remaining time
      _lastStatus.next_change = d.next_change || 0;
      showToast(d.paused ? 'Paused' : 'Resumed');
      updateCountdown();
    });
}

function switchTCG(tcg, activeBtn) {
  var btns = document.getElementById('quick-switch-btns').querySelectorAll('.btn');
  btns.forEach(function(b) { b.disabled = true; });
  var orig = activeBtn.textContent;
  activeBtn.textContent = 'Switching...';
  fetch(API + '/api/config', {method:'POST', body: JSON.stringify({active_tcg: tcg}),
    headers:{'Content-Type':'application/json'}})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      activeBtn.textContent = orig;
      btns.forEach(function(b) { b.disabled = false; });
      if (d && d.error) { showToast(d.error || 'Switch failed'); return; }
      var name = (_tcgRegistry[tcg] && _tcgRegistry[tcg].name) || tcg.toUpperCase();
      showToast('Switching to ' + name + '...');
      document.getElementById('st-tcg').textContent = name;
      setOptimisticLoading('Switching to ' + name + '...');
      startRapidPoll();
    })
    .catch(function() {
      activeBtn.textContent = orig;
      btns.forEach(function(b) { b.disabled = false; });
      showToast('Switch failed');
    });
}

// --- Settings ---
function getBrowserUtcOffset() {
  return -(new Date().getTimezoneOffset() / 60);
}

function autoDetectTimezone() {
  var offset = getBrowserUtcOffset();
  document.getElementById('cfg-tz-offset').value = offset;
  var tzName = '';
  try { tzName = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch(e) {}
  document.getElementById('cfg-tz-name').value = tzName;
  var sign = offset >= 0 ? '+' : '';
  showToast('Set to ' + (tzName || 'UTC' + sign + offset) + ' — tap Save Settings to apply');
}

function updateTimezoneHint() {
  var hint = document.getElementById('tz-hint');
  if (!hint) return;
  var savedName = document.getElementById('cfg-tz-name').value;
  if (savedName) {
    hint.textContent = '— using ' + savedName + ' (auto DST)';
    hint.style.color = '#6BCCBD';
  } else {
    var browserOffset = getBrowserUtcOffset();
    var sign = browserOffset >= 0 ? '+' : '';
    var tzName = '';
    try { tzName = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch(e) {}
    hint.textContent = '— tap Auto-Detect to set ' + (tzName || 'UTC' + sign + browserOffset);
  }
}

function loadSettings() {
  fetch(API + '/api/config').then(r => r.json()).then(c => {
    document.getElementById('cfg-tcg').value = c.active_tcg;
    document.getElementById('cfg-header-mode').value = c.slab_header_mode || 'normal';
    document.getElementById('cfg-rotation').value = c.rotation_angle;
    document.getElementById('cfg-day-interval').value = Math.round(c.day_interval / 60);
    document.getElementById('cfg-night-interval').value = Math.round(c.night_interval / 60);
    document.getElementById('cfg-tz-offset').value = c.timezone_offset != null ? c.timezone_offset : '';
    document.getElementById('cfg-tz-name').value = c.timezone_name || '';
    document.getElementById('cfg-day-start').value = c.day_start;
    document.getElementById('cfg-day-end').value = c.day_end;
    document.getElementById('cfg-saturation').value = c.color_saturation;
    document.getElementById('cfg-collection').checked = c.collection_only;
    loadDisplayPlan();
    loadPlugins();
    loadUpdateBranches(c.update_branch);
    updateTimezoneHint();
    // Auto-detect timezone on first load if not set — just works for shipped units
    if (!c.timezone_name && !c.timezone_offset) {
      var tzName = '';
      try { tzName = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch(e) {}
      if (tzName) {
        var offset = getBrowserUtcOffset();
        document.getElementById('cfg-tz-offset').value = offset;
        document.getElementById('cfg-tz-name').value = tzName;
        // Auto-save so they don't have to tap anything
        var cfg = {timezone_name: tzName, timezone_offset: offset};
        fetch(API + '/api/config', {method:'POST', body: JSON.stringify(cfg)});
        updateTimezoneHint();
      }
    }
  });
}

function saveSettings() {
  const cfg = {
    active_tcg: document.getElementById('cfg-tcg').value,
    slab_header_mode: document.getElementById('cfg-header-mode').value,
    rotation_angle: parseInt(document.getElementById('cfg-rotation').value) || 270,
    day_interval: (parseInt(document.getElementById('cfg-day-interval').value) || 10) * 60,
    night_interval: (parseInt(document.getElementById('cfg-night-interval').value) || 60) * 60,
    timezone_offset: document.getElementById('cfg-tz-offset').value !== '' ? parseInt(document.getElementById('cfg-tz-offset').value) : null,
    timezone_name: document.getElementById('cfg-tz-name').value || null,
    day_start: parseInt(document.getElementById('cfg-day-start').value) || 7,
    day_end: parseInt(document.getElementById('cfg-day-end').value) || 23,
    color_saturation: parseFloat(document.getElementById('cfg-saturation').value) || 2.5,
    collection_only: document.getElementById('cfg-collection').checked,
  };
  fetch(API + '/api/config', {method:'POST', body: JSON.stringify(cfg)})
    .then(function() { showToast('Settings saved!'); startRapidPoll(); });
}

function getSelectedUpdateBranch() {
  var el = document.getElementById('update-branch');
  return el ? el.value : '';
}

function loadUpdateBranches(selectedFromConfig) {
  fetch(API + '/api/update/branches').then(r => r.json()).then(d => {
    var sel = document.getElementById('update-branch');
    var hint = document.getElementById('update-branch-hint');
    if (!sel) return;
    sel.innerHTML = (d.branches || []).map(function(branch) {
      return '<option value="' + branch + '">' + branch + '</option>';
    }).join('');
    sel.value = selectedFromConfig || d.selected || d.current || d.default || 'master';
    if (hint) {
      hint.textContent = 'Current code branch: ' + (d.current || '-') + ' | Default shipping branch: ' + (d.default || 'master');
    }
  }).catch(function() {
    var hint = document.getElementById('update-branch-hint');
    if (hint) hint.textContent = 'Could not load branch list.';
  });
}

function saveUpdateBranch() {
  var branch = getSelectedUpdateBranch();
  if (!branch) return;
  fetch(API + '/api/config', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({update_branch: branch})
  }).then(r => r.json()).then(function(d) {
    if (d && !d.error) showToast('Update branch set to ' + branch);
  });
}

function loadPlugins() {
  fetch(API + '/api/plugins').then(r => r.json()).then(function(d) {
    _pluginUiState = d;
    var summaryEl = document.getElementById('plugin-summary');
    var listEl = document.getElementById('plugin-list');
    if (!summaryEl || !listEl) return;
    var plugins = d.plugins || {};
    var enabledPlugins = Array.isArray(d.enabled_plugins) ? d.enabled_plugins : [];
    var pluginSettings = d.plugin_settings || {};
    var active = d.active_plugin || '';
    var entries = Object.entries(plugins);
    summaryEl.textContent = enabledPlugins.length + ' runnable plugin' + (enabledPlugins.length === 1 ? '' : 's') + ' enabled. Active: ' + active;
    listEl.innerHTML = entries.map(function(entry) {
      var id = entry[0];
      var plugin = entry[1] || {};
      var isRuntime = !!plugin.runtime_enabled;
      var isEnabled = enabledPlugins.indexOf(id) !== -1;
      var badge = id === active
        ? '<span class="plugin-badge">Active</span>'
        : (isRuntime
          ? '<span class="plugin-badge" style="background:#8899a6;color:#FCFDF0">' + (isEnabled ? 'Enabled' : 'Disabled') + '</span>'
          : '<span class="plugin-badge" style="background:#4B5563;color:#FCFDF0">Coming Soon</span>');
      var meta = [];
      if (plugin.kind) meta.push(plugin.kind.toUpperCase());
      if (plugin.download_script) meta.push('downloadable');
      if (plugin.settings_keys && plugin.settings_keys.length) meta.push(plugin.settings_keys.length + ' shared settings');
      if (plugin.source === 'local-manifest') meta.push('manifest only');
      var note = isRuntime
        ? 'This plugin can be enabled for rotation right now.'
        : 'This plugin is part of the modular roadmap and is not runnable yet.';
      var settingsHtml = '';
      if (plugin.config_schema && plugin.config_schema.length) {
        settingsHtml = '<div class="plugin-settings">' + plugin.config_schema.map(function(field) {
          var fieldKey = field.key || '';
          var fieldId = id + '__' + fieldKey;
          var pluginBucket = pluginSettings[id] || {};
          var fieldValue = pluginBucket[fieldKey] != null ? pluginBucket[fieldKey] : (field.default != null ? field.default : '');
          var inputHtml = '';
          if (field.type === 'select') {
            var options = Array.isArray(field.options) ? field.options : [
              {value:'imperial', label:'Imperial'},
              {value:'metric', label:'Metric'}
            ];
            inputHtml = '<select data-plugin-setting="' + id + '" data-setting-key="' + esc(fieldKey) + '" id="' + esc(fieldId) + '">' +
              options.map(function(opt) {
                var value = (opt && opt.value != null) ? String(opt.value) : '';
                var label = (opt && opt.label != null) ? String(opt.label) : value;
                return '<option value="' + esc(value) + '"' + (String(fieldValue) === value ? ' selected' : '') + '>' + esc(label) + '</option>';
              }).join('') +
            '</select>';
          } else {
            var inputType = field.type === 'number' ? 'number' : 'text';
            inputHtml = '<input type="' + inputType + '" data-plugin-setting="' + id + '" data-setting-key="' + esc(fieldKey) + '" id="' + esc(fieldId) + '" value="' + esc(fieldValue) + '">';
          }
          return '<div class="plugin-settings-row"><label for="' + esc(fieldId) + '">' + esc(field.label || fieldKey) + '</label>' + inputHtml + '</div>';
        }).join('') + '</div>';
      }
      return '<div class="plugin-item' + (id === active ? ' active' : '') + '">' +
        '<div class="plugin-top"><div class="plugin-name">' + esc(plugin.name || id) + '</div>' + badge + '</div>' +
        '<div style="font-size:12px;color:#D8E6E4">' + esc(plugin.description || '') + '</div>' +
        '<div class="plugin-meta" style="margin-top:6px">' + esc(meta.join(' • ')) + '</div>' +
        '<div class="plugin-controls">' +
          (isRuntime
            ? '<label class="plugin-toggle"><input type="checkbox" data-plugin-enabled="' + id + '"' + (isEnabled ? ' checked' : '') + '> Enable in display plan</label>'
            : '<div class="plugin-note">Will appear here as a real module once its renderer and data source are built.</div>') +
        '</div>' +
        settingsHtml +
        (isRuntime ? '<div class="plugin-note">' + esc(note) + '</div>' : '') +
      '</div>';
    }).join('');
    syncModularUI(d);
  }).catch(function() {
    var summaryEl = document.getElementById('plugin-summary');
    if (summaryEl) summaryEl.textContent = 'Could not load plugins.';
  });
}

function hasEnabledPluginKind(kind) {
  if (!_pluginUiState) return true;
  var plugins = _pluginUiState.plugins || {};
  var enabled = Array.isArray(_pluginUiState.enabled_plugins) ? _pluginUiState.enabled_plugins : [];
  return enabled.some(function(id) {
    return plugins[id] && plugins[id].kind === kind;
  });
}

function syncModularUI(d) {
  var state = d || _pluginUiState;
  if (!state) return;
  var hasTCG = hasEnabledPluginKind('tcg');
  var quickSwitch = document.getElementById('quick-switch-card');
  var collectionTabBtn = document.getElementById('tab-btn-collection');
  var downloadsTabBtn = document.getElementById('tab-btn-downloads');
  var defaultTcgGroup = document.getElementById('default-tcg-group');
  if (quickSwitch) {
    var showManualSwitch = hasTCG && _displayPlanState && _displayPlanState.display_mode === 'single';
    quickSwitch.style.display = showManualSwitch ? 'block' : 'none';
  }
  if (collectionTabBtn) collectionTabBtn.style.display = hasTCG ? '' : 'none';
  if (downloadsTabBtn) downloadsTabBtn.style.display = hasTCG ? '' : 'none';
  if (defaultTcgGroup) defaultTcgGroup.style.display = hasTCG ? 'block' : 'none';
  if (!hasTCG) {
    var activeTab = localStorage.getItem('inkslab_tab');
    if (activeTab === 'collection' || activeTab === 'downloads') {
      showTab('settings');
    }
  }
}

function collectEnabledPlugins() {
  return Array.from(document.querySelectorAll('[data-plugin-enabled]:checked')).map(function(el) {
    return el.getAttribute('data-plugin-enabled');
  });
}

function collectPluginSettings() {
  var out = {};
  Array.from(document.querySelectorAll('[data-plugin-setting]')).forEach(function(el) {
    var pluginId = el.getAttribute('data-plugin-setting');
    var key = el.getAttribute('data-setting-key');
    if (!pluginId || !key) return;
    if (!out[pluginId]) out[pluginId] = {};
    out[pluginId][key] = el.value;
  });
  return out;
}

function savePluginConfig() {
  fetch(API + '/api/plugins/config', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      enabled_plugins: collectEnabledPlugins(),
      plugin_settings: collectPluginSettings()
    })
  }).then(r => r.json()).then(function(d) {
    if (d && d.ok) {
      showToast('Plugin selection saved');
      loadPlugins();
      loadDisplayPlan();
      startRapidPoll();
    } else {
      showToast((d && d.error) || 'Failed to save plugin selection');
    }
  }).catch(function() {
    showToast('Failed to save plugin selection');
  });
}

var _displayPlanState = null;

function loadDisplayPlan() {
  fetch(API + '/api/display_plan').then(r => r.json()).then(function(d) {
    _displayPlanState = d;
    var summaryEl = document.getElementById('display-plan-summary');
    var listEl = document.getElementById('display-plan-list');
    var modeNoteEl = document.getElementById('display-mode-note');
    var modeEl = document.getElementById('display-mode');
    var singleEl = document.getElementById('single-plugin');
    if (!summaryEl || !listEl) return;
    var mode = d.display_mode || 'single';
    var schedule = d.display_schedule || [];
    var plugins = d.plugins || {};
    var enabledPlugins = Array.isArray(d.enabled_plugins) ? d.enabled_plugins : [];
    var selectablePlugins = objectFromEntriesCompat(Object.entries(plugins).filter(function(entry) {
      return enabledPlugins.indexOf(entry[0]) !== -1 && entry[1] && entry[1].runtime_enabled;
    }));
    _displayPlanState.selectable_plugins = selectablePlugins;
    if (modeEl) modeEl.value = mode;
    if (singleEl) {
      singleEl.innerHTML = Object.entries(selectablePlugins).map(function(entry) {
        return '<option value="' + entry[0] + '">' + esc((entry[1] && entry[1].name) || entry[0]) + '</option>';
      }).join('');
      if (!singleEl.innerHTML) {
        singleEl.innerHTML = '<option value="pokemon">Pokemon</option>';
      }
      singleEl.value = d.single_plugin || d.active_plugin || 'pokemon';
    }
    if (mode === 'schedule') {
      summaryEl.textContent = 'Schedule mode is enabled. Active right now: ' + (d.active_plugin || '-') + '.';
      if (modeNoteEl) modeNoteEl.textContent = 'This device is following a saved schedule. Active right now: ' + (d.active_plugin || '-') + '.';
    } else {
      summaryEl.textContent = 'Single mode is active. Current plugin: ' + (d.single_plugin || d.active_plugin || '-');
      if (modeNoteEl) modeNoteEl.textContent = 'This device stays on one plugin until the display plan changes. Current plugin: ' + (d.single_plugin || d.active_plugin || '-') + '.';
    }
    listEl.innerHTML = schedule.map(function(item) {
      var title = esc(item.label || item.plugin_id || '');
      var pluginIds = Array.isArray(item.plugin_ids) ? item.plugin_ids : (item.plugin_id ? [item.plugin_id] : []);
      var pluginLabel = esc(pluginIds.join(' → '));
      var timeLabel = String(item.start_hour).padStart(2, '0') + ':00 - ' + String(item.end_hour).padStart(2, '0') + ':00';
      var disabled = item.enabled ? '' : ' <span class="plugin-badge" style="background:#8899a6;color:#FCFDF0">Disabled</span>';
      var rotation = pluginIds.length > 1 ? 'Rotates every ' + (item.rotation_minutes || 10) + ' min' : 'Single plugin block';
      return '<div class="schedule-item">' +
        '<div class="schedule-time">' + esc(timeLabel) + disabled + '</div>' +
        '<div class="schedule-title">' + title + '</div>' +
        '<div class="plugin-meta" style="margin-top:4px">Plugins: ' + pluginLabel + '</div>' +
        '<div class="plugin-meta" style="margin-top:2px">' + esc(rotation) + '</div>' +
      '</div>';
    }).join('');
    renderDisplayPlanEditor(schedule, selectablePlugins);
    syncModularUI();
  }).catch(function() {
    var summaryEl = document.getElementById('display-plan-summary');
    if (summaryEl) summaryEl.textContent = 'Could not load display plan.';
  });
}

function renderDisplayPlanEditor(schedule, plugins) {
  var editor = document.getElementById('display-plan-editor');
  if (!editor) return;
  var pluginEntries = Object.entries(plugins || {});
  editor.innerHTML = (schedule || []).map(function(item, idx) {
    var pluginIds = Array.isArray(item.plugin_ids) ? item.plugin_ids : [];
    return '<div class="schedule-editor-item" data-index="' + idx + '">' +
      '<div class="flex-row" style="margin-bottom:8px">' +
        '<input type="text" data-field="label" value="' + esc(item.label || '') + '" placeholder="Block name" style="flex:1">' +
        '<button class="btn btn-danger btn-sm" onclick="removeDisplayPlanBlock(' + idx + ')">Remove</button>' +
      '</div>' +
      '<div class="schedule-grid">' +
        '<div><label>Start Hour</label><input type="number" min="0" max="23" data-field="start_hour" value="' + esc(item.start_hour) + '"></div>' +
        '<div><label>End Hour</label><input type="number" min="1" max="24" data-field="end_hour" value="' + esc(item.end_hour) + '"></div>' +
        '<div><label>Rotate Every (min)</label><input type="number" min="1" max="1440" data-field="rotation_minutes" value="' + esc(item.rotation_minutes || 10) + '"></div>' +
        '<div><label>Enabled</label><input type="checkbox" data-field="enabled"' + (item.enabled ? ' checked' : '') + '></div>' +
      '</div>' +
      '<div style="margin-top:8px"><label>Plugins In Rotation</label><div class="plugin-checks">' +
        pluginEntries.map(function(entry) {
          var checked = pluginIds.indexOf(entry[0]) !== -1 ? ' checked' : '';
          return '<label><input type="checkbox" data-plugin-id="' + entry[0] + '"' + checked + '><span>' + esc((entry[1] && entry[1].name) || entry[0]) + '</span></label>';
        }).join('') +
      '</div></div>' +
    '</div>';
  }).join('');
}

function addDisplayPlanBlock() {
  if (!_displayPlanState) return;
  var schedule = Array.isArray(_displayPlanState.display_schedule) ? _displayPlanState.display_schedule.slice() : [];
  schedule.push({
    label: 'New Block',
    start_hour: 8,
    end_hour: 12,
    rotation_minutes: 10,
    enabled: true,
    plugin_ids: [_displayPlanState.single_plugin || _displayPlanState.active_plugin || 'pokemon']
  });
  _displayPlanState.display_schedule = schedule;
  renderDisplayPlanEditor(schedule, _displayPlanState.selectable_plugins || {});
}

function removeDisplayPlanBlock(index) {
  if (!_displayPlanState || !Array.isArray(_displayPlanState.display_schedule)) return;
  _displayPlanState.display_schedule = _displayPlanState.display_schedule.filter(function(_, idx) { return idx !== index; });
  renderDisplayPlanEditor(_displayPlanState.display_schedule, _displayPlanState.selectable_plugins || {});
}

function collectDisplayPlanSchedule() {
  var editor = document.getElementById('display-plan-editor');
  if (!editor) return [];
  return Array.from(editor.querySelectorAll('.schedule-editor-item')).map(function(item) {
    var selectedPlugins = Array.from(item.querySelectorAll('[data-plugin-id]:checked')).map(function(cb) {
      return cb.getAttribute('data-plugin-id');
    });
    return {
      label: item.querySelector('[data-field="label"]').value.trim() || 'Block',
      start_hour: parseInt(item.querySelector('[data-field="start_hour"]').value, 10) || 0,
      end_hour: parseInt(item.querySelector('[data-field="end_hour"]').value, 10) || 24,
      rotation_minutes: parseInt(item.querySelector('[data-field="rotation_minutes"]').value, 10) || 10,
      enabled: !!item.querySelector('[data-field="enabled"]').checked,
      plugin_ids: selectedPlugins.length ? selectedPlugins : [document.getElementById('single-plugin').value || 'pokemon']
    };
  });
}

function saveDisplayPlan() {
  var mode = document.getElementById('display-mode').value || 'single';
  var singlePlugin = document.getElementById('single-plugin').value || 'pokemon';
  var body = {
    display_mode: mode,
    single_plugin: singlePlugin,
    display_schedule: collectDisplayPlanSchedule()
  };
  fetch(API + '/api/display_plan', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)
  }).then(r => r.json()).then(function(d) {
    if (d && d.ok) {
      showToast('Display plan saved');
      loadDisplayPlan();
      startRapidPoll();
    } else {
      showToast((d && d.error) || 'Failed to save display plan');
    }
  }).catch(function() {
    showToast('Failed to save display plan');
  });
}

// --- Admin (hidden) ---
var _adminTaps = 0;
var _adminTimer = null;
function adminTap() {
  _adminTaps++;
  if (_adminTimer) clearTimeout(_adminTimer);
  _adminTimer = setTimeout(function() { _adminTaps = 0; }, 2000);
  if (_adminTaps >= 5) {
    _adminTaps = 0;
    var panel = document.getElementById('admin-panel');
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
    if (panel.style.display === 'block') showToast('Admin mode');
  }
}

// --- WiFi ---
function loadWifiInfo() {
  var el = document.getElementById('wifi-info');
  fetch(API + '/api/wifi/status').then(r => r.json()).then(function(d) {
    if (d.connected && d.ssid) {
      var safeSSID = (d.ssid||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      el.innerHTML = 'Connected to <strong>' + safeSSID + '</strong>' + (d.ip ? ' &mdash; IP: ' + d.ip : '');
    } else if (d.hotspot_active) {
      el.textContent = 'Setup mode — broadcasting ' + (d.hotspot_ssid || 'InkSlab-Setup');
    } else {
      el.textContent = 'Not connected';
    }
  }).catch(function() { el.textContent = 'Could not check WiFi status'; });
}

function factoryReset(btn) {
  if (!confirm('PREPARE FOR NEW OWNER\\n\\nThis will:\\n- Forget WiFi credentials\\n- Delete ALL downloaded cards\\n- Reset all settings\\n- Show a welcome screen on the display\\n\\nAfter it finishes, wait ~30 seconds for the display to update, then unplug. The unit is ready to ship.\\n\\nAre you sure?')) return;
  if (!confirm('This cannot be undone. Continue?')) return;
  btn.disabled = true;
  btn.textContent = 'Resetting...';
  fetch(API + '/api/factory_reset', {method:'POST'}).then(r => r.json()).then(function(d) {
    if (d.ok) {
      showToast('Done! Wait ~30s for the display to update, then unplug to ship.', 8000);
      document.getElementById('wifi-info').innerHTML = '<strong style="color:#ff6b6b">Ready to ship</strong> — Wait for the display to update, then unplug.';
      btn.textContent = 'Done — unplug when display updates';
    } else {
      showToast('Reset failed: ' + (d.error || 'unknown'));
      btn.disabled = false;
      btn.textContent = 'Prepare for New Owner';
    }
  }).catch(function() {
    showToast('Reset in progress — connection lost because WiFi was disconnected. Wait ~30s for display to update, then unplug.');
    btn.textContent = 'Done — unplug when display updates';
  });
}

function changeWifi() {
  if (!confirm('This will disconnect WiFi and start the setup hotspot.\\n\\nAfter this:\\n1. On your phone, go to Settings > WiFi\\n2. Connect to "InkSlab-Setup"\\n3. Open 10.42.0.1 in your browser\\n\\nContinue?')) return;
  // Show feedback IMMEDIATELY — don't wait for response (WiFi drops any moment)
  var el = document.getElementById('wifi-info');
  el.innerHTML = '<strong style="color:#36A5CA">Switching to setup mode...</strong><br><br>1. Connect to <strong>InkSlab-Setup</strong> WiFi<br>2. Open <strong>10.42.0.1</strong> in your browser';
  showToast('Starting setup hotspot...', 5000);
  fetch(API + '/api/wifi/disconnect', {method:'POST'}).catch(function() {});
}

// --- Collection ---
function loadSets() {
  const el = document.getElementById('sets-list');
  el.innerHTML = '<div style="color:#6BCCBD;padding:16px;text-align:center">Loading sets...</div>';
  fetch(API + '/api/sets').then(r => r.json()).then(sets => {
    if (!sets.length) { el.innerHTML = '<div style="color:#6BCCBD;padding:16px;text-align:center">No cards downloaded yet.</div>'; return; }
    el.innerHTML = sets.map(s => `
      <div class="set-item">
        <div class="set-header" onclick="toggleSet('${esc(s.id)}')">
          <span>
            <span class="set-name">${esc(s.name)}</span>
            ${s.owned_count > 0 ? '<span class="badge">' + s.owned_count + '</span>' : ''}
          </span>
          <span class="set-meta">${esc(s.year)} &middot; ${s.downloaded_count != null && s.downloaded_count !== s.card_count ? s.downloaded_count + ' / ' + s.card_count : s.card_count} cards</span>
        </div>
        <div class="set-cards" id="set-${esc(s.id)}"></div>
      </div>
    `).join('');
  });
}

function toggleSet(setId) {
  const el = document.getElementById('set-' + setId);
  if (el.classList.contains('open')) { el.classList.remove('open'); return; }
  el.classList.add('open');
  if (el.dataset.loaded) return;
  el.innerHTML = '<div style="padding:8px;color:#6BCCBD;font-size:12px">Loading...</div>';
  fetch(API + '/api/sets/' + setId + '/cards').then(r => r.json()).then(cards => {
    el.dataset.loaded = '1';
    // Extract unique rarities for chips
    var rarities = [];
    var seen = {};
    cards.forEach(function(c) { if (c.rarity && !seen[c.rarity]) { seen[c.rarity] = 1; rarities.push(c.rarity); } });
    let html = '<div style="padding:4px 0 6px;display:flex;gap:4px;flex-wrap:wrap">';
    html += `<button class="btn btn-secondary btn-sm" onclick="toggleSetAll('${setId}',true)">Select All</button>`;
    html += `<button class="btn btn-secondary btn-sm" onclick="toggleSetAll('${setId}',false)">Deselect All</button>`;
    html += '</div>';
    // Per-set rarity chips with counts and toggle state
    if (rarities.length > 1) {
      html += '<div class="rarity-chips">';
      rarities.forEach(function(r) {
        var total = 0, ownedCt = 0;
        cards.forEach(function(c) { if (c.rarity === r) { total++; if (c.owned) ownedCt++; } });
        var isActive = ownedCt > 0;
        html += '<span class="rarity-chip' + (isActive ? ' active' : '') + '" data-rarity="' + esc(r) + '" onclick="toggleSetRarityChip(this,\\'' + esc(setId) + '\\',\\'' + esc(r) + '\\',' + (isActive ? 'false' : 'true') + ')">'
          + esc(r) + '<span class="chip-count">(' + ownedCt + '/' + total + ')</span></span>';
      });
      html += '</div>';
    }
    html += cards.map(c => `
      <div class="card-row" data-rarity="${esc(c.rarity)}">
        <label>
          <input type="checkbox" ${c.owned ? 'checked' : ''} onchange="toggleCard('${esc(c.id)}')">
          <span class="card-preview-btn" data-set="${esc(c.set_id)}" data-card="${esc(c.id)}" data-label="${esc(c.name)} #${esc(c.number)}">#${esc(c.number)} ${esc(c.name)}</span>
        </label>
        <span class="card-rarity">${esc(c.rarity)}</span>
      </div>
    `).join('');
    el.innerHTML = html;
  });
}

function toggleCard(cardId) {
  fetch(API + '/api/collection/toggle', {method:'POST', body: JSON.stringify({card_id: cardId})});
}

function toggleSetAll(setId, owned) {
  fetch(API + '/api/collection/toggle_set', {method:'POST', body: JSON.stringify({set_id: setId, owned: owned})})
    .then(() => {
      const el = document.getElementById('set-' + setId);
      el.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = owned);
    });
}

function clearCollection() {
  if (!confirm('Clear your entire card list for the active TCG?')) return;
  fetch(API + '/api/collection/clear', {method:'POST'}).then(() => { loadSets(); loadRarities(); });
}

// --- Rarity filtering ---
var _rarityData = [];

function loadRarities() {
  fetch(API + '/api/rarities').then(function(r) { return r.json(); }).then(function(rarities) {
    _rarityData = rarities;
    renderRarityChips();
  });
}

function renderRarityChips() {
  var el = document.getElementById('rarity-chips');
  if (!_rarityData.length) { el.innerHTML = '<span style="color:#6BCCBD;font-size:12px">No cards downloaded yet</span>'; return; }
  el.innerHTML = _rarityData.map(function(r) {
    var sel = r.owned > 0;
    var safeR = r.name.replace(/'/g, "\\\\'");
    return '<span class="rarity-toggle' + (sel ? ' selected' : '') + '" onclick="toggleRarityChip(this,\\'' + safeR + '\\',' + (sel ? 'false' : 'true') + ')">'
      + '<span class="rt-check">' + (sel ? '&#10003;' : '') + '</span>'
      + r.name
      + '<span class="rt-count">' + r.owned + '/' + r.count + '</span>'
      + '</span>';
  }).join('');
}

function toggleRarityChip(chipEl, rarity, owned) {
  var resultEl = document.getElementById('rarity-result');
  resultEl.textContent = (owned ? 'Selecting' : 'Deselecting') + ' all ' + rarity + '...';
  chipEl.style.opacity = '0.5';
  fetch(API + '/api/collection/toggle_rarity', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({rarity: rarity, owned: owned})})
    .then(function(r) { return r.json(); }).then(function(d) {
      resultEl.textContent = (owned ? 'Selected ' : 'Deselected ') + (d.count || 0) + ' ' + rarity + ' cards';
      showToast((owned ? 'Selected ' : 'Deselected ') + (d.count || 0) + ' cards');
      loadRarities();
      // Clear set loaded state so they refresh checkboxes
      document.querySelectorAll('.set-cards').forEach(function(sc) { sc.removeAttribute('data-loaded'); });
      loadSets();
    }).catch(function() { resultEl.textContent = 'Error'; chipEl.style.opacity = '1'; });
}

function selectAllRarities(owned) {
  var resultEl = document.getElementById('rarity-result');
  resultEl.textContent = (owned ? 'Selecting' : 'Deselecting') + ' all...';
  fetch(API + '/api/collection/toggle_all', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({owned: owned})})
    .then(function(r) { return r.json(); }).then(function(d) {
      resultEl.textContent = (owned ? 'Selected ' : 'Deselected ') + (d.count || 0) + ' cards';
      showToast((owned ? 'Selected ' : 'Deselected ') + (d.count || 0) + ' cards');
      loadRarities();
      document.querySelectorAll('.set-cards').forEach(function(sc) { sc.removeAttribute('data-loaded'); });
      loadSets();
    }).catch(function() { resultEl.textContent = 'Error'; });
}

function toggleSetRarityChip(chipEl, setId, rarity, owned) {
  chipEl.style.opacity = '0.5';
  fetch(API + '/api/collection/toggle_rarity', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({set_id: setId, rarity: rarity, owned: owned})})
    .then(function(r) { return r.json(); }).then(function(d) {
      if (d.count !== undefined) {
        showToast((owned ? 'Selected ' : 'Deselected ') + d.count + ' ' + rarity + ' cards');
        var el = document.getElementById('set-' + setId);
        var total = 0, newOwned = 0;
        el.querySelectorAll('.card-row').forEach(function(row) {
          if (row.dataset.rarity === rarity) {
            row.querySelector('input[type=checkbox]').checked = owned;
            total++;
            if (owned) newOwned++;
          }
        });
        chipEl.classList.toggle('active', owned);
        chipEl.style.opacity = '1';
        var cs = chipEl.querySelector('.chip-count');
        if (cs) cs.textContent = '(' + newOwned + '/' + total + ')';
        var safeR = rarity.replace(/'/g, "\\\\'");
        chipEl.setAttribute('onclick', "toggleSetRarityChip(this,\\'" + setId + "\\',\\'" + safeR + "\\'," + (!owned) + ")");
      }
    }).catch(function() { chipEl.style.opacity = '1'; });
}

// --- Card preview modal ---
function showPreview(setId, cardId, label, tcg) {
  var t = tcg || (_lastStatus && _lastStatus.tcg) || 'pokemon';
  document.getElementById('preview-img').src = '/api/card_image/' + t + '/' + setId + '/' + cardId;
  document.getElementById('preview-name').textContent = label;
  document.getElementById('preview-modal').classList.add('open');
}
function showCurrentPreview() {
  if (!_lastStatus || !_lastStatus.set_id) return;
  showPreview(_lastStatus.set_id, _lastStatus.card_id, (_lastStatus.card_num || '') + ' ' + (_lastStatus.set_info || ''), _lastStatus.tcg);
}
function closePreview() {
  document.getElementById('preview-modal').classList.remove('open');
}
// Delegated click handler for all card preview buttons (uses data attributes
// instead of inline onclick to avoid quote-escaping issues with card names)
document.addEventListener('click', function(e) {
  var btn = e.target.closest('.card-preview-btn[data-set]');
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  showPreview(btn.dataset.set, btn.dataset.card, btn.dataset.label, btn.dataset.tcg);
});

// --- Search ---
var _searchTimer = null;

function debounceSearch() {
  if (_searchTimer) clearTimeout(_searchTimer);
  _searchTimer = setTimeout(doSearch, 350);
}

function loadFavorites() {
  fetch(API + '/api/collection/favorites').then(function(r) { return r.json(); }).then(function(favs) {
    var el = document.getElementById('search-filters');
    if (!favs.length) { el.style.display = 'none'; el.innerHTML = ''; return; }
    el.style.display = 'flex';
    el.innerHTML = favs.map(function(name) {
      var safeN = name.replace(/'/g, "\\\\'");
      return '<span class="search-filter-chip">' + name + '<span class="sfc-x" onclick="removeFavorite(\\'' + safeN + '\\', event)">&times;</span></span>';
    }).join('');
  });
}

function removeFavorite(name, e) {
  var chip = e.target.parentElement;
  chip.style.opacity = '0.5';
  fetch(API + '/api/collection/favorites', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name: name, owned: false})})
    .then(function(r) { return r.json(); }).then(function(d) {
      showToast('Removed ' + (d.count || 0) + ' ' + name + ' cards');
      loadFavorites();
      doSearch();
      loadRarities();
    }).catch(function() { chip.style.opacity = '1'; });
}

function doSearch() {
  var q = document.getElementById('search-input').value.trim();
  var el = document.getElementById('search-results');
  if (q.length < 2) { el.innerHTML = ''; return; }
  el.innerHTML = '<div style="color:#6BCCBD;font-size:12px;padding:8px">Searching...</div>';
  fetch(API + '/api/search?q=' + encodeURIComponent(q)).then(function(r) { return r.json(); }).then(function(data) {
    var results = data.results;
    if (!results.length) { el.innerHTML = '<div style="color:#6BCCBD;font-size:12px;padding:8px">No results found (searched ' + data.sets_searched + ' sets)</div>'; return; }
    var groups = {};
    results.forEach(function(c) {
      var key = c.name.toLowerCase();
      if (!groups[key]) groups[key] = {name: c.name, cards: []};
      groups[key].cards.push(c);
    });
    var header = '<div style="font-size:11px;color:#6BCCBD;margin-bottom:6px">' + data.total + ' results across ' + data.sets_searched + ' sets';
    if (data.total > results.length) header += ' (showing ' + results.length + ')';
    header += '</div>';
    var html = header;
    Object.values(groups).forEach(function(g) {
      var allOwned = g.cards.every(function(c) { return c.owned; });
      var ownedCount = g.cards.filter(function(c) { return c.owned; }).length;
      html += '<div style="border-bottom:1px solid #1F333F;padding:6px 0">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center">';
      html += '<span class="search-result-name">' + esc(g.name) + ' <span style="color:#6BCCBD;font-size:11px;font-weight:400">' + ownedCount + '/' + g.cards.length + ' selected</span></span>';
      html += '<button class="btn btn-secondary btn-sm" onclick="toggleSearchGroup(this,\\'' + esc(g.name) + '\\',' + (!allOwned) + ')">' + (allOwned ? 'Remove All' : 'Add All') + '</button>';
      html += '</div>';
      html += '<div style="margin-top:4px">';
      g.cards.forEach(function(c) {
        html += '<div class="search-result"><label style="display:flex;align-items:center;gap:6px;flex:1;cursor:pointer">';
        html += '<input type="checkbox" ' + (c.owned ? 'checked' : '') + ' onchange="toggleCard(\\'' + esc(c.id) + '\\')" style="accent-color:#36A5CA">';
        html += '<span><span class="card-preview-btn" data-set="' + esc(c.set_id) + '" data-card="' + esc(c.id) + '" data-label="' + esc(c.name) + ' #' + esc(c.number) + '">#' + esc(c.number) + '</span>';
        html += ' <span class="search-result-set">' + esc(c.set_name) + '</span></span>';
        html += '</label><span class="search-result-rarity">' + esc(c.rarity) + '</span></div>';
      });
      html += '</div></div>';
    });
    el.innerHTML = html;
  }).catch(function() { el.innerHTML = '<div style="color:#ff6b6b;font-size:12px;padding:8px">Search failed</div>'; });
}

function toggleSearchGroup(btn, name, owned) {
  btn.disabled = true;
  btn.textContent = owned ? 'Adding...' : 'Removing...';
  fetch(API + '/api/collection/favorites', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name: name, owned: owned})})
    .then(function(r) { return r.json(); }).then(function(d) {
      showToast((owned ? 'Added ' : 'Removed ') + (d.count || 0) + ' ' + name + ' cards');
      loadFavorites();
      doSearch();
      loadRarities();
    }).catch(function() { btn.disabled = false; btn.textContent = owned ? 'Add All' : 'Remove All'; });
}

// --- Downloads ---
function fmtSize(gb, mb) {
  if (gb >= 0.1) return gb.toFixed(1) + ' GB';
  if (mb > 0) return mb + ' MB';
  return '0 MB';
}
function fmtSizeShort(gb, mb) {
  if (gb >= 0.1) return gb.toFixed(1) + 'G';
  if (mb > 0) return mb + 'M';
  return '';
}
function loadStorage() {
  fetch(API + '/api/storage').then(function(r) { return r.json(); }).then(function(info) {
    _storageInfo = info;
    var el = document.getElementById('storage-info');
    if (info._computing) {
      el.innerHTML = '<div style="color:#6BCCBD;text-align:center;padding:12px"><span class="preview-spin" style="display:inline-block;font-size:18px">&#8635;</span><div style="margin-top:6px">Calculating storage...</div></div>';
      setTimeout(loadStorage, 3000);
      return;
    }
    if (!info._disk) { el.innerHTML = '<div style="color:#6BCCBD">Loading...</div>'; setTimeout(loadStorage, 3000); return; }
    var totalGb = info._disk.total_gb || 1;
    var freeGb = info._disk.free_gb || 0;
    // Dynamic: sum up all TCG sizes
    var tcgEntries = Object.entries(info).filter(function(e) { return !e[0].startsWith('_'); });
    var tcgTotalGb = 0;
    tcgEntries.forEach(function(e) { tcgTotalGb += (e[1].size_gb || 0); });
    var usedGb = Math.round((totalGb - freeGb) * 100) / 100;
    var otherGb = Math.max(0, Math.round((usedGb - tcgTotalGb) * 100) / 100);
    var otherPct = (otherGb / totalGb * 100);
    var freePct = (freeGb / totalGb * 100);
    var html = '<div class="storage-bar-wrap">';
    html += '<div class="storage-bar-label"><span>' + usedGb.toFixed(1) + ' GB used</span><span>' + freeGb.toFixed(1) + ' GB free / ' + totalGb.toFixed(0) + ' GB</span></div>';
    html += '<div class="storage-bar">';
    tcgEntries.forEach(function(e) {
      var tcg = e[0], d = e[1];
      var gb = d.size_gb || 0;
      if (gb <= 0) return;
      var pct = Math.max(gb / totalGb * 100, 1.5);
      var color = (_tcgRegistry[tcg] && _tcgRegistry[tcg].color) || '#888';
      html += '<div class="storage-seg" style="width:' + pct.toFixed(1) + '%;background:' + color + '">' + (pct > 8 ? fmtSizeShort(gb, d.size_mb || 0) : '') + '</div>';
    });
    if (otherPct > 0.5) html += '<div class="storage-seg seg-other" style="width:' + otherPct.toFixed(1) + '%">' + (otherPct > 8 ? otherGb.toFixed(1) + 'G' : '') + '</div>';
    html += '<div class="storage-seg seg-free" style="width:' + Math.max(freePct, 1).toFixed(1) + '%">' + (freePct > 12 ? freeGb.toFixed(1) + 'G' : '') + '</div>';
    html += '</div>';
    html += '<div class="storage-legend">';
    tcgEntries.forEach(function(e) {
      var tcg = e[0];
      var color = (_tcgRegistry[tcg] && _tcgRegistry[tcg].color) || '#888';
      var name = (_tcgRegistry[tcg] && _tcgRegistry[tcg].name) || tcg.toUpperCase();
      html += '<div class="storage-legend-item"><span class="storage-legend-dot" style="background:' + color + '"></span>' + name + '</div>';
    });
    html += '<div class="storage-legend-item"><span class="storage-legend-dot" style="background:#E8786B"></span>System</div>';
    html += '<div class="storage-legend-item"><span class="storage-legend-dot" style="background:#1F333F;border:1px solid #36A5CA44"></span>Free</div>';
    html += '</div></div>';
    var freeMb = freeGb * 1024;
    if (freeMb < 100) {
      html += '<div style="background:#E8786B22;border:1px solid #E8786B;border-radius:6px;padding:8px 12px;margin:8px 0;color:#E8786B;font-size:12px;font-weight:600">&#9888; Storage almost full — delete some cards to free space</div>';
    }
    tcgEntries.forEach(function(e) {
      var tcg = e[0], d = e[1];
      var name = (_tcgRegistry[tcg] && _tcgRegistry[tcg].name) || tcg.toUpperCase();
      html += '<div class="stat"><span class="stat-label">' + name + '</span><span class="stat-value">' + d.card_count + ' cards &middot; ' + d.set_count + ' sets &middot; ' + fmtSize(d.size_gb || 0, d.size_mb || 0) + '</span></div>';
    });
    el.innerHTML = html;
    loadDlButtons();
  });
}

function setDownloadUI(running, tcg) {
  const btns = document.getElementById('dl-buttons');
  const stopBtn = document.getElementById('btn-dl-stop');
  const sinceBtn = document.getElementById('btn-dl-mtg-since');
  if (running) {
    btns.querySelectorAll('.btn').forEach(b => b.disabled = true);
    if (sinceBtn) sinceBtn.disabled = true;
    stopBtn.style.display = 'block';
    stopBtn.textContent = 'Stop ' + (tcg || '').toUpperCase() + ' Download';
  } else {
    btns.querySelectorAll('.btn').forEach(b => b.disabled = false);
    if (sinceBtn) sinceBtn.disabled = false;
    stopBtn.style.display = 'none';
  }
}

function startDownload(tcg, since) {
  var existingCount = (_storageInfo && _storageInfo[tcg] && _storageInfo[tcg].card_count) || 0;
  if (existingCount > 0 && !since) {
    if (!confirm('You already have ' + existingCount.toLocaleString() + ' ' + tcg.toUpperCase() + ' cards downloaded.\\n\\nRe-downloading will check for new cards and skip existing ones — it won\\'t delete what you have.\\n\\nContinue?')) return;
  }
  const body = {tcg: tcg};
  if (since) body.since = parseInt(since);
  fetch(API + '/api/download/start', {method:'POST', body: JSON.stringify(body)})
    .then(r => r.json()).then(d => {
      if (d.ok) {
        document.getElementById('dl-status').textContent = 'Downloading ' + tcg.toUpperCase() + '...';
        setDownloadUI(true, tcg);
        pollDownload();
      } else {
        alert(d.error || 'Failed to start download');
      }
    });
}

function stopDownload() {
  fetch(API + '/api/download/stop', {method:'POST'}).then(r => r.json()).then(d => {
    if (d.ok) {
      document.getElementById('dl-status').textContent = 'Download stopped.';
      setDownloadUI(false);
      loadStorage();
    }
  });
}

let _dlPoll = null;
function pollDownload() {
  if (_dlPoll) clearInterval(_dlPoll);
  checkDownload();
  _dlPoll = setInterval(checkDownload, 2000);
}
function checkDownload() {
  fetch(API + '/api/download/status').then(r => r.json()).then(d => {
    const logEl = document.getElementById('dl-log');
    logEl.textContent = d.lines.join('\\n') || 'No output yet.';
    logEl.scrollTop = logEl.scrollHeight;
    if (d.running) {
      document.getElementById('dl-status').textContent = 'Downloading ' + (d.tcg || '').toUpperCase() + '...';
      setDownloadUI(true, d.tcg);
    } else {
      document.getElementById('dl-status').textContent = 'Idle';
      setDownloadUI(false);
      if (_dlPoll) { clearInterval(_dlPoll); _dlPoll = null; loadStorage(); }
    }
  });
}

function deleteData(tcg, btn) {
  if (!confirm('Delete ALL ' + tcg.toUpperCase() + ' card images? This cannot be undone.')) return;
  var origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Deleting...';
  fetch(API + '/api/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({tcg: tcg})})
    .then(r => r.json()).then(d => {
      btn.disabled = false;
      btn.textContent = origText;
      if (d.ok) { showToast(tcg.toUpperCase() + ' data deleted'); loadStorage(); }
      else showToast(d.error || 'Delete failed');
    }).catch(function() {
      btn.disabled = false;
      btn.textContent = origText;
      showToast('Delete failed');
    });
}

function updateDeleteSourceUI() {
  var sel = document.getElementById('delete-source');
  var note = document.getElementById('delete-source-note');
  var btn = document.getElementById('delete-source-btn');
  if (!sel || !note || !btn) return;
  var tcg = sel.value;
  var info = _tcgRegistry[tcg];
  if (!tcg || !info) {
    note.textContent = 'No deletable data sources are available right now.';
    btn.disabled = true;
    return;
  }
  var label = info.name || tcg.toUpperCase();
  note.textContent = 'This clears the local ' + label + ' library from the device. Plugin code and settings stay intact.';
  btn.disabled = false;
}

function deleteSelectedData() {
  var sel = document.getElementById('delete-source');
  var btn = document.getElementById('delete-source-btn');
  if (!sel || !btn) return;
  var tcg = sel.value;
  if (!tcg) return;
  deleteData(tcg, btn);
}

// --- OTA Update ---
function checkUpdate() {
  var el = document.getElementById('update-info');
  el.textContent = 'Checking...';
  var branch = getSelectedUpdateBranch();
  fetch(API + '/api/update/check', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({branch: branch})
  }).then(r => r.json()).then(d => {
    if (!d.ok) { el.textContent = 'Error: ' + (d.error || 'unknown'); return; }
    if (d.up_to_date) {
      el.innerHTML = 'Up to date on <span style="color:#6BCCBD">' + d.branch + '</span>! <span style="color:#6BCCBD">Version: ' + d.local + '</span>';
      document.getElementById('btn-update-now').style.display = 'none';
    } else {
      el.innerHTML = d.behind + ' update' + (d.behind > 1 ? 's' : '') + ' available on <span style="color:#6BCCBD">' + d.branch + '</span>. <span style="color:#6BCCBD">Current: ' + d.local + ' &rarr; Latest: ' + d.remote + '</span>';
      document.getElementById('btn-update-now').style.display = 'block';
    }
  }).catch(function() { el.textContent = 'Failed to check. Is the Pi online?'; });
}

function startUpdate() {
  var branch = getSelectedUpdateBranch();
  if (!confirm('Update InkSlab from branch "' + branch + '"? The display and web dashboard will restart.')) return;
  document.getElementById('update-progress').style.display = 'block';
  document.getElementById('update-stage').textContent = 'Starting update from ' + branch + '...';
  document.getElementById('update-bar').style.width = '10%';
  document.getElementById('btn-update-now').style.display = 'none';
  fetch(API + '/api/update/start', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({branch: branch})
  }).then(r => r.json()).then(d => {
    if (d.ok) { pollUpdate(); }
    else { document.getElementById('update-stage').textContent = 'Error: ' + (d.error || 'unknown'); }
  });
}

var _updatePoll = null;
function pollUpdate() {
  if (_updatePoll) clearInterval(_updatePoll);
  _updatePoll = setInterval(checkUpdateStatus, 2000);
}
function checkUpdateStatus() {
  fetch(API + '/api/update/status').then(r => r.json()).then(d => {
    var bar = document.getElementById('update-bar');
    var stage = document.getElementById('update-stage');
    var stages = {fetching: 20, pulling: 40, restarting_display: 60, restarting_web: 80, complete: 100};
    bar.style.width = (stages[d.stage] || 10) + '%';
    stage.textContent = d.message || d.stage || 'Working...';
    if (d.stage === 'complete') {
      clearInterval(_updatePoll); _updatePoll = null;
      stage.textContent = 'Done! Reloading...';
      // Force cache-bypass reload so new JS is always loaded after an update
      setTimeout(function() { location.href = location.origin + '/?updated=' + Date.now(); }, 2000);
    } else if (d.error) {
      clearInterval(_updatePoll); _updatePoll = null;
      stage.textContent = d.message || 'Update failed';
      bar.style.background = '#ff6b6b';
    }
  }).catch(function() {
    document.getElementById('update-stage').textContent = 'Reconnecting...';
  });
}

// --- Custom Images ---
function loadCustomFolders() {
  fetch(API + '/api/custom/folders').then(r => r.json()).then(folders => {
    var el = document.getElementById('custom-folders');
    if (!folders.length) { el.innerHTML = '<div style="color:#6BCCBD;font-size:12px">No custom folders yet. Create one above.</div>'; return; }
    el.innerHTML = folders.map(f => {
      return '<div class="set-item"><div class="set-header" onclick="toggleCustomFolder(\\'' + esc(f.id) + '\\')">'
        + '<span><span class="set-name">' + esc(f.name) + '</span></span>'
        + '<span class="set-meta">' + f.card_count + ' cards</span>'
        + '</div><div class="set-cards" id="cf-' + esc(f.id) + '"></div></div>';
    }).join('');
  });
}

function refreshCustomFolder(folderId) {
  var el = document.getElementById('cf-' + folderId);
  if (!el) return;
  el.removeAttribute('data-loaded');
  el.classList.add('open');
  _loadCustomFolderContent(folderId, el);
}

function toggleCustomFolder(folderId) {
  var el = document.getElementById('cf-' + folderId);
  if (el.classList.contains('open')) { el.classList.remove('open'); return; }
  el.classList.add('open');
  if (el.dataset.loaded) return;
  _loadCustomFolderContent(folderId, el);
}

function _loadCustomFolderContent(folderId, el) {
  el.innerHTML = '<div style="padding:8px;color:#6BCCBD;font-size:12px">Loading...</div>';
  fetch(API + '/api/sets/' + folderId + '/cards?tcg=custom').then(r => r.json()).then(cards => {
    el.dataset.loaded = '1';
    var html = '<div style="padding:6px 0;display:flex;gap:4px;flex-wrap:wrap;align-items:center">';
    html += '<label class="btn btn-secondary btn-sm" style="cursor:pointer">Upload <input type="file" accept="image/png,image/jpeg" multiple style="display:none" onchange="uploadCustomCards(\\'' + esc(folderId) + '\\',this.files)"></label>';
    html += '<button class="btn btn-secondary btn-sm" onclick="renameCustomFolder(\\'' + esc(folderId) + '\\')">Rename</button>';
    html += '<button class="btn btn-danger btn-sm" onclick="deleteCustomFolder(\\'' + esc(folderId) + '\\')">Delete Set</button>';
    html += '</div>';
    if (cards.length) {
      cards.forEach(c => {
        html += '<div class="card-row"><label style="flex:1;cursor:pointer">';
        html += '<span class="card-preview-btn" data-set="' + esc(c.set_id) + '" data-card="' + esc(c.id) + '" data-label="' + esc(c.name||'') + '" data-tcg="custom">#' + esc(c.number) + ' ' + esc(c.name) + '</span>';
        html += '</label>';
        html += '<span style="display:flex;gap:4px;align-items:center">';
        html += '<span class="card-rarity">' + esc(c.rarity || '') + '</span>';
        html += '<span style="cursor:pointer;color:#6BCCBD;font-size:11px" onclick="editCustomCard(\\'' + esc(folderId) + '\\',\\'' + esc(c.id) + '\\',\\'' + esc(c.name||'') + '\\',\\'' + esc(c.number) + '\\',\\'' + esc(c.rarity||'') + '\\')">edit</span>';
        html += '<span style="cursor:pointer;color:#ff6b6b;font-size:11px" onclick="deleteCustomCard(\\'' + esc(folderId) + '\\',\\'' + esc(c.id) + '\\')">x</span>';
        html += '</span></div>';
      });
    } else {
      html += '<div style="color:#6BCCBD;font-size:12px;padding:8px">No images yet. Upload some!</div>';
    }
    el.innerHTML = html;
  });
}

function createCustomFolder() {
  var name = document.getElementById('custom-folder-name').value.trim();
  if (!name) { showToast('Enter a folder name'); return; }
  fetch(API + '/api/custom/create_folder', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name: name})})
    .then(r => r.json()).then(d => {
      if (d.ok) { document.getElementById('custom-folder-name').value = ''; loadCustomFolders(); showToast('Created ' + name); }
      else showToast(d.error || 'Failed');
    });
}

function uploadCustomCards(folderId, files) {
  if (!files.length) return;
  var done = 0, failed = 0;
  showToast('Uploading ' + files.length + ' file(s)...');
  Array.from(files).forEach(function(file) {
    var fd = new FormData();
    fd.append('folder', folderId);
    fd.append('file', file);
    fetch(API + '/api/custom/upload', {method:'POST', body: fd}).then(function(r) {
      return r.json().then(function(d) {
        if (d.error) { failed++; showToast(d.error, 4000); }
        else { done++; }
        if (done + failed >= files.length) {
          if (done > 0) showToast('Uploaded ' + done + ' file(s)' + (failed ? ', ' + failed + ' failed' : ''));
          refreshCustomFolder(folderId);
          loadCustomFolders();
          loadStorage();
        }
      });
    }).catch(function() {
      failed++;
      if (done + failed >= files.length) {
        showToast('Upload error — ' + failed + ' file(s) failed', 4000);
        refreshCustomFolder(folderId);
        loadCustomFolders();
        loadStorage();
      }
    });
  });
}

function renameCustomFolder(folderId) {
  var newName = prompt('New name for this set:');
  if (!newName) return;
  fetch(API + '/api/custom/rename_folder', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: folderId, name: newName})})
    .then(r => r.json()).then(d => { if (d.ok) { loadCustomFolders(); showToast('Renamed'); } });
}

function deleteCustomFolder(folderId) {
  if (!confirm('Delete this entire custom set and all its images?')) return;
  fetch(API + '/api/custom/folder/' + folderId, {method:'DELETE'}).then(r => r.json()).then(d => {
    if (d.ok) { loadCustomFolders(); loadStorage(); showToast('Deleted'); }
  });
}

function deleteCustomCard(folderId, cardId) {
  if (!confirm('Delete this image?')) return;
  fetch(API + '/api/custom/card/' + folderId + '/' + cardId, {method:'DELETE'}).then(r => r.json()).then(d => {
    if (d.ok) {
      refreshCustomFolder(folderId);
      loadCustomFolders();
      showToast('Deleted');
    }
  });
}

function editCustomCard(folderId, cardId, name, number, rarity) {
  var newName = prompt('Card name:', name);
  if (newName === null) return;
  var newNum = prompt('Card number:', number);
  if (newNum === null) return;
  var newRarity = prompt('Rarity (optional):', rarity);
  if (newRarity === null) return;
  fetch(API + '/api/custom/card_metadata', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({folder: folderId, card_id: cardId, name: newName, number: newNum, rarity: newRarity})})
    .then(r => r.json()).then(d => {
      if (d.ok) {
        refreshCustomFolder(folderId);
        showToast('Updated');
      }
    });
}

// --- Dynamic TCG UI ---
var _storageInfo = null;
var _tcgRegistry = {};

function loadDlButtons() {
  var dlEl = document.getElementById('dl-buttons');
  if (!dlEl || !Object.keys(_tcgRegistry).length) return;
  var storageInfo = _storageInfo || {};
  dlEl.innerHTML = Object.entries(_tcgRegistry).filter(function(e) { return e[1].download_script; }).map(function(e) {
    var tcgKey = e[0], tcgName = e[1].name;
    var existingCount = (storageInfo[tcgKey] && storageInfo[tcgKey].card_count) || 0;
    var btnLabel = existingCount > 0 ? 'Update ' + tcgName + ' (' + existingCount.toLocaleString() + ' cards already downloaded)' : 'Download ' + tcgName;
    return '<div style="margin-bottom:6px"><button class="btn btn-primary btn-block" onclick="startDownload(\\'' + tcgKey + '\\')">'+btnLabel+'</button></div>';
  }).join('');
  // MTG since-year filter
  var mtgSince = document.getElementById('dl-mtg-since');
  if (mtgSince) mtgSince.style.display = _tcgRegistry.mtg ? 'block' : 'none';
  // Delete source selector
  var delSel = document.getElementById('delete-source');
  if (delSel) {
    var deleteEntries = Object.entries(_tcgRegistry).filter(function(entry) {
      return !!entry[1].path;
    });
    delSel.innerHTML = deleteEntries.map(function(entry) {
      return '<option value="' + entry[0] + '">' + entry[1].name + '</option>';
    }).join('');
    delSel.onchange = updateDeleteSourceUI;
    updateDeleteSourceUI();
  }
}

function buildDynamicUI(registry) {
  _tcgRegistry = registry;
  // Quick Switch buttons
  var qsEl = document.getElementById('quick-switch-btns');
  qsEl.innerHTML = Object.entries(registry).map(function(e) {
    return '<button class="btn btn-secondary btn-block" onclick="switchTCG(\\'' + e[0] + '\\', this)">' + e[1].name + '</button>';
  }).join('');
  // Settings TCG dropdown
  var sel = document.getElementById('cfg-tcg');
  sel.innerHTML = Object.entries(registry).map(function(e) {
    return '<option value="' + e[0] + '">' + e[1].name + '</option>';
  }).join('');
  // Download and delete buttons
  loadDlButtons();
}

// --- Init ---
(function() {
  // Load TCG registry first, then build UI
  fetch(API + '/api/tcg_list').then(r => r.json()).then(function(registry) {
    buildDynamicUI(registry);
    // Now do everything else
    const saved = localStorage.getItem('inkslab_tab');
    if (saved && document.getElementById('tab-' + saved)) {
      showTab(saved);
    } else {
      refreshStatus();
    }
    startMainPoll();
    startCountdown();
    fetch(API + '/api/ip').then(r => r.json()).then(d => {
      if (d.ip) document.getElementById('footer-ip').textContent = 'http://' + d.ip;
    }).catch(() => {});
    fetch(API + '/api/version').then(r => r.json()).then(d => {
      var el = document.getElementById('update-info');
      if (d.version && d.version !== 'unknown') el.textContent = 'Version: ' + d.version + ' — Click below to check for updates.';
      else el.textContent = 'Click below to check for updates.';
    }).catch(() => { document.getElementById('update-info').textContent = 'Click below to check for updates.'; });
  }).catch(function() {
    // Fallback if tcg_list fails
    refreshStatus();
    startMainPoll();
    startCountdown();
  });
})();
// Reload if browser restores this page from bfcache (back-forward cache).
// bfcache freezes JS state, so buttons can silently lose their handlers
// after navigating away and back. Forcing a reload fixes this transparently.
window.addEventListener('pageshow', function(e) { if (e.persisted) location.reload(); });
</script>
</body>
</html>"""


@app.after_request
def add_no_cache(response):
    """Prevent browser from caching ANY response — fixes stale JS after OTA updates."""
    if 'Cache-Control' not in response.headers:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


@app.route('/')
def dashboard():
    global _wifi_setup_mode
    # Daemon watchdog may have auto-started hotspot — pick up its flag
    if not _wifi_setup_mode and os.path.exists(WATCHDOG_SETUP_FLAG):
        _wifi_setup_mode = True
    html = WIFI_SETUP_HTML if _wifi_setup_mode else DASHBOARD_HTML
    resp = make_response(html)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _logger = logging.getLogger(__name__)
    _logger.info("InkSlab Web Dashboard starting...")

    # Give NetworkManager time to initialize on slow Pi Zero
    import time as _time
    _time.sleep(3)

    # Check WiFi: saved profile is the primary signal. If a profile exists,
    # NEVER start the hotspot — WiFi will auto-connect within seconds.
    # Only enter setup mode if there's genuinely no saved profile.
    try:
        has_profile = wifi_manager.has_saved_wifi_profile()
        is_connected = wifi_manager.is_wifi_connected()
        _logger.info("WiFi check: connected=%s, has_profile=%s", is_connected, has_profile)

        if has_profile:
            _logger.info("WiFi profile exists — serving dashboard (connected=%s)", is_connected)
        elif not is_connected:
            _wifi_setup_mode = True
            _logger.info("No WiFi profile found — entering setup mode")
            # Retry hotspot startup — critical for first boot, user has no other way in
            for _hotspot_attempt in range(1, 4):
                if wifi_manager.start_hotspot():
                    break
                _logger.warning("Hotspot start attempt %d/3 failed, retrying in 5s...", _hotspot_attempt)
                time.sleep(5)
            else:
                _logger.error("Hotspot failed after 3 attempts — user may need to power cycle")
        else:
            _logger.info("WiFi connected — serving dashboard")
    except Exception as e:
        _logger.warning("WiFi check failed, skipping setup mode: %s", e)

    def _graceful_shutdown(signum, frame):
        """Clean up resources on shutdown."""
        _logger.info("Received signal %s, shutting down...", signum)
        _close_download_log()
        with _download_lock:
            if _download_proc and _download_proc.poll() is None:
                try:
                    _download_proc.terminate()
                except Exception:
                    pass
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _graceful_shutdown)

    try:
        app.run(host='0.0.0.0', port=80, debug=False, threaded=True)
    except (SystemExit, KeyboardInterrupt):
        _logger.info("Web server stopped")
    except Exception as e:
        _logger.error("Web server crashed: %s", e, exc_info=True)
    finally:
        _close_download_log()
