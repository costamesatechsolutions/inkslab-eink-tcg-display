#!/usr/bin/python3
"""
InkSlab Web Dashboard
https://github.com/costamesatechsolutions/inkslab-eink-tcg-display

A lightweight Flask web UI for managing InkSlab from your phone or browser.
Access at http://inkslab.local after enabling the systemd service.

By Costa Mesa Tech Solutions (a brand of Pine Heights Ventures LLC)
"""

import os
import json
import shutil
import signal
import subprocess
import time
import threading
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

# --- PATHS ---
CONFIG_FILE = "/home/pi/inkslab_config.json"
COLLECTION_FILE = "/home/pi/inkslab_collection.json"
STATUS_FILE = "/tmp/inkslab_status.json"
NEXT_TRIGGER = "/tmp/inkslab_next"
COLLECTION_TRIGGER = "/tmp/inkslab_collection_changed"
DOWNLOAD_LOG = "/tmp/inkslab_download.log"

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

TCG_LIBRARIES = {
    "pokemon": "/home/pi/pokemon_cards",
    "mtg": "/home/pi/mtg_cards",
}

DEFAULTS = {
    "active_tcg": "pokemon",
    "rotation_angle": 270,
    "day_interval": 600,
    "night_interval": 3600,
    "day_start": 7,
    "day_end": 23,
    "color_saturation": 2.5,
    "collection_only": False,
}

# Track running download process
_download_proc = None
_download_tcg = None
_download_log_fh = None
_download_lock = threading.Lock()

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


# --- HELPERS ---

def load_config():
    config = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config.update(json.load(f))
        except Exception:
            pass
    return config


def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def load_collection():
    if os.path.exists(COLLECTION_FILE):
        try:
            with open(COLLECTION_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_collection(data):
    with open(COLLECTION_FILE, 'w') as f:
        json.dump(data, f)
    # Signal daemon that collection changed so it can rebuild its deck
    try:
        with open(COLLECTION_TRIGGER, 'w') as f:
            f.write('1')
    except OSError:
        pass


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
    }
    return order.get(rarity.lower().strip(), 15)


def get_local_ip():
    try:
        result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5)
        ip = result.stdout.strip().split()[0]
        return ip
    except Exception:
        return None


# --- API ROUTES ---

@app.route('/api/status')
def api_status():
    status = {}
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r') as f:
                status = json.load(f)
        except Exception:
            pass
    # Auto-clear stale pending status (e.g. if daemon crashed mid-update)
    if status.get('pending') and time.time() - status.get('timestamp', 0) > 60:
        status.pop('pending', None)
    return jsonify(status)


@app.route('/api/config', methods=['GET'])
def api_get_config():
    return jsonify(load_config())


@app.route('/api/config', methods=['POST'])
def api_set_config():
    config = load_config()
    updates = request.get_json(force=True)
    for key in DEFAULTS:
        if key in updates:
            config[key] = updates[key]
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
            with open(STATUS_FILE, 'w') as f:
                json.dump(status, f)
        except Exception:
            pass
    # Wake the display daemon immediately so it picks up the change within ~1 second
    try:
        with open(NEXT_TRIGGER, 'w') as f:
            f.write('1')
    except OSError:
        pass
    return jsonify(config)


@app.route('/api/next', methods=['POST'])
def api_next():
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
            with open(STATUS_FILE, 'w') as f:
                json.dump(status, f)
        except Exception:
            pass
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


PREV_TRIGGER = "/tmp/inkslab_prev"
PAUSE_FILE = "/tmp/inkslab_pause"


@app.route('/api/prev', methods=['POST'])
def api_prev():
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
            with open(STATUS_FILE, 'w') as f:
                json.dump(status, f)
        except Exception:
            pass
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/pause', methods=['POST'])
def api_pause():
    """Toggle pause state. Returns new paused state."""
    if os.path.exists(PAUSE_FILE):
        try:
            os.remove(PAUSE_FILE)
        except OSError:
            pass
        paused = False
    else:
        try:
            with open(PAUSE_FILE, 'w') as f:
                f.write('1')
        except OSError:
            pass
        paused = True
    # Update status file so web UI reflects immediately
    try:
        status = {}
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, 'r') as f:
                status = json.load(f)
        status['paused'] = paused
        if not paused and status.get('interval'):
            status['next_change'] = int(time.time()) + status['interval']
        elif paused:
            status['next_change'] = 0
        with open(STATUS_FILE, 'w') as f:
            json.dump(status, f)
    except Exception:
        pass
    return jsonify({"ok": True, "paused": paused})


@app.route('/api/ip')
def api_ip():
    return jsonify({"ip": get_local_ip()})


@app.route('/api/card_image')
def api_current_card_image():
    """Serve the current card image from the display status."""
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r') as f:
                status = json.load(f)
            card_path = status.get("card_path")
            if card_path and os.path.exists(card_path):
                resp = send_file(card_path, mimetype='image/png')
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
    card_path = os.path.join(library, safe_set, safe_card + '.png')
    if os.path.exists(card_path):
        return send_file(card_path, mimetype='image/png')
    return '', 404


@app.route('/api/sets')
def api_sets():
    config = load_config()
    tcg = request.args.get('tcg', config['active_tcg'])
    library = TCG_LIBRARIES.get(tcg)
    if not library or not os.path.exists(library):
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

    sets = []
    for d in sorted(os.listdir(library)):
        set_path = os.path.join(library, d)
        if not os.path.isdir(set_path):
            continue
        cards = [f for f in os.listdir(set_path) if f.endswith('.png') and not f.startswith('_')]
        card_ids = [os.path.splitext(f)[0] for f in cards]
        owned_count = sum(1 for cid in card_ids if cid in owned_ids)
        info = master.get(d, {})
        sets.append({
            "id": d,
            "name": info.get("name", d),
            "year": info.get("year", ""),
            "card_count": len(cards),
            "owned_count": owned_count,
        })

    sets.sort(key=lambda x: x["year"], reverse=True)
    return jsonify(sets)


@app.route('/api/sets/<set_id>/cards')
def api_set_cards(set_id):
    config = load_config()
    tcg = request.args.get('tcg', config['active_tcg'])
    library = TCG_LIBRARIES.get(tcg)
    if not library:
        return jsonify([])

    set_path = os.path.join(library, set_id)
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
        if not f.endswith('.png') or f.startswith('_'):
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
    card_id = data.get("card_id")
    if not card_id:
        return jsonify({"error": "card_id required"}), 400

    config = load_config()
    tcg = data.get("tcg", config["active_tcg"])

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
    set_id = data.get("set_id")
    owned = data.get("owned", True)
    if not set_id:
        return jsonify({"error": "set_id required"}), 400

    config = load_config()
    tcg = data.get("tcg", config["active_tcg"])
    library = TCG_LIBRARIES.get(tcg)
    if not library:
        return jsonify({"error": "invalid tcg"}), 400

    set_path = os.path.join(library, set_id)
    if not os.path.isdir(set_path):
        return jsonify({"error": "set not found"}), 404

    card_ids = [os.path.splitext(f)[0] for f in os.listdir(set_path)
                if f.endswith('.png') and not f.startswith('_')]

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
            data_file = os.path.join(library, d, "_data.json")
            if not os.path.exists(data_file):
                continue
            try:
                with open(data_file, 'r') as f:
                    data = json.load(f)
                for card_id, card in data.items():
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
    owned = body.get("owned", True)
    config = load_config()
    tcg = body.get("tcg", config["active_tcg"])
    library = TCG_LIBRARIES.get(tcg)
    if not library or not os.path.isdir(library):
        return jsonify({"error": "invalid tcg"}), 400

    collection = load_collection()
    if tcg not in collection:
        collection[tcg] = []

    if not owned:
        count = len(collection[tcg])
        collection[tcg] = []
    else:
        all_ids = set()
        for d in os.listdir(library):
            set_path = os.path.join(library, d)
            if not os.path.isdir(set_path):
                continue
            for f in os.listdir(set_path):
                if f.endswith('.png') and not f.startswith('_'):
                    all_ids.add(os.path.splitext(f)[0])
        collection[tcg] = list(all_ids)
        count = len(all_ids)

    save_collection(collection)
    _cache_invalidate('rarities_' + tcg)
    return jsonify({"owned": owned, "count": count})


@app.route('/api/collection/toggle_batch', methods=['POST'])
def api_collection_toggle_batch():
    """Add or remove a specific list of card_ids in one request."""
    body = request.get_json(force=True)
    card_ids = body.get("card_ids", [])
    owned = body.get("owned", True)
    config = load_config()
    tcg = body.get("tcg", config["active_tcg"])

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
        dirs_to_scan = [set_id]
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
        data_file = os.path.join(library, d, "_data.json")
        if not os.path.exists(data_file):
            continue
        sets_searched += 1
        try:
            with open(data_file, 'r') as f:
                data = json.load(f)
            set_info = master.get(d, {})
            for card_id, card in data.items():
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
    name = body.get("name", "").strip()
    owned = body.get("owned", True)
    if not name:
        return jsonify({"error": "name required"}), 400

    config = load_config()
    tcg = body.get("tcg", config["active_tcg"])
    collection = load_collection()

    # Manage favorites list
    if "_favorites" not in collection:
        collection["_favorites"] = {}
    if tcg not in collection["_favorites"]:
        collection["_favorites"][tcg] = []

    favs = collection["_favorites"][tcg]
    key = name.lower()

    if owned:
        # Add to favorites if not already there
        if not any(f.lower() == key for f in favs):
            favs.append(name)
    else:
        # Remove from favorites
        collection["_favorites"][tcg] = [f for f in favs if f.lower() != key]

    # Also add/remove all matching card IDs
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
                    if card.get("name", "").lower() == key:
                        matching_ids.append(card_id)
            except Exception:
                pass

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

        data = request.get_json(force=True) if request.data else {}
        tcg = data.get("tcg", "pokemon")
        since = data.get("since")

        if tcg == "pokemon":
            cmd = ["python3", os.path.join(SCRIPT_DIR, "scripts", "download_cards_pokemon.py")]
        elif tcg == "mtg":
            cmd = ["python3", os.path.join(SCRIPT_DIR, "scripts", "download_cards_mtg.py")]
            if since:
                cmd.extend(["--since", str(since)])
        else:
            return jsonify({"ok": False, "error": "Unknown TCG"})

        _download_log_fh = open(DOWNLOAD_LOG, 'w')
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        _download_proc = subprocess.Popen(
            cmd, stdout=_download_log_fh, stderr=subprocess.STDOUT,
            cwd=SCRIPT_DIR, env=env
        )
        _download_tcg = tcg

        return jsonify({"ok": True, "tcg": tcg, "pid": _download_proc.pid})


@app.route('/api/download/stop', methods=['POST'])
def api_download_stop():
    global _download_proc, _download_tcg

    with _download_lock:
        if _download_proc and _download_proc.poll() is None:
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
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "No download running"})


@app.route('/api/download/status')
def api_download_status():
    global _download_proc, _download_tcg

    running = False
    tcg = _download_tcg
    with _download_lock:
        if _download_proc and _download_proc.poll() is None:
            running = True
        elif _download_proc:
            # Process finished — close log file handle
            _close_download_log()
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


@app.route('/api/storage')
def api_storage():
    cached = _cache_get('storage', ttl=30)
    if cached:
        return jsonify(cached)

    info = {}
    for tcg, path in TCG_LIBRARIES.items():
        if os.path.exists(path):
            total_size = 0
            card_count = 0
            set_count = 0
            for root, dirs, files in os.walk(path):
                if root == path:
                    set_count = len(dirs)
                for f in files:
                    try:
                        total_size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
                    if f.endswith('.png') and not f.startswith('_'):
                        card_count += 1
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

    _cache_set('storage', info)
    return jsonify(info)


@app.route('/api/delete', methods=['POST'])
def api_delete():
    data = request.get_json(force=True)
    tcg = data.get("tcg")
    if not tcg or tcg not in TCG_LIBRARIES:
        return jsonify({"ok": False, "error": "Invalid TCG"}), 400

    path = TCG_LIBRARIES[tcg]
    if os.path.exists(path):
        try:
            shutil.rmtree(path)
            _cache_invalidate('storage', 'rarities_' + tcg)
            return jsonify({"ok": True, "tcg": tcg})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "tcg": tcg})


# --- DASHBOARD HTML ---

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>InkSlab</title>
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
.stat { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #1F333F; font-size: 14px; }
.stat:last-child { border-bottom: none; }
.stat-label { color: #6BCCBD; }
.stat-value { color: #FCFDF0; }
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
.storage-seg.seg-other { background: #8b6bbf; }
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
.flex-row { display: flex; gap: 8px; }
.flex-row > * { flex: 1; }
.preview-img { display: block; max-width: 150px; border-radius: 6px; border: 2px solid #1F333F; width: 100%; }
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
</style>
</head>
<body>

<div class="header">
  <h1>InkSlab</h1>
</div>

<div class="tabs">
  <div class="tab active" data-tab="display" onclick="showTab('display')">Display</div>
  <div class="tab" data-tab="settings" onclick="showTab('settings')">Settings</div>
  <div class="tab" data-tab="collection" onclick="showTab('collection')">Collection</div>
  <div class="tab" data-tab="downloads" onclick="showTab('downloads')">Downloads</div>
</div>

<div class="content">

<!-- DISPLAY TAB -->
<div id="tab-display" class="panel active">
  <div class="card">
    <h3>Now Showing</h3>
    <div id="st-preview-wrap" style="position:relative;max-width:150px;margin:12px auto 0">
      <img id="st-preview" class="preview-img" style="margin:0" src="/api/card_image" onerror="this.style.display='none'" onload="this.style.display='block'">
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
  <div class="card">
    <h3>Quick Switch</h3>
    <div class="flex-row">
      <button class="btn btn-secondary btn-block" onclick="switchTCG('pokemon', this)">Pokemon</button>
      <button class="btn btn-secondary btn-block" onclick="switchTCG('mtg', this)">MTG</button>
    </div>
  </div>
</div>

<!-- SETTINGS TAB -->
<div id="tab-settings" class="panel">
  <div class="card">
    <h3>Display Settings</h3>
    <div class="form-group">
      <label>Active TCG</label>
      <select id="cfg-tcg"><option value="pokemon">Pokemon</option><option value="mtg">Magic: The Gathering</option></select>
    </div>
    <div class="form-group">
      <label>Rotation Angle</label>
      <select id="cfg-rotation"><option value="0">0</option><option value="90">90</option><option value="180">180</option><option value="270">270</option></select>
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
        <label for="cfg-collection">Show only owned cards (collection mode)</label>
      </div>
    </div>
    <button class="btn btn-primary btn-block" onclick="saveSettings()">Save Settings</button>
  </div>
</div>

<!-- COLLECTION TAB -->
<div id="tab-collection" class="panel">
  <div class="card">
    <h3>My Collection</h3>
    <p style="color:#6BCCBD;font-size:12px;margin-bottom:8px">Mark the cards you own. Enable "collection mode" in Settings to only display owned cards.</p>
    <button class="btn btn-secondary btn-sm" onclick="clearCollection()">Clear All</button>
  </div>
  <div class="card">
    <h3>Search Cards</h3>
    <p style="color:#6BCCBD;font-size:12px;margin-bottom:8px">Find a Pokemon or card by name and add all versions to your collection.</p>
    <div id="search-filters" class="search-filters" style="display:none"></div>
    <div class="search-wrap">
      <span class="search-icon">&#128269;</span>
      <input type="text" id="search-input" placeholder="Search by name (e.g. Pikachu)" oninput="debounceSearch()">
    </div>
    <div id="search-results"></div>
  </div>
  <div class="card">
    <h3>Filter by Rarity</h3>
    <p style="color:#6BCCBD;font-size:12px;margin-bottom:8px">Toggle rarities on/off across all sets. Checked = cards of that rarity are in your collection.</p>
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
    <h3>Storage</h3>
    <div id="storage-info"></div>
  </div>
  <div class="card">
    <h3>Download Cards</h3>
    <div id="dl-buttons">
      <div class="flex-row" style="margin-bottom:8px">
        <button class="btn btn-primary btn-block" id="btn-dl-pokemon" onclick="startDownload('pokemon')">Download Pokemon</button>
      </div>
      <div class="flex-row" style="margin-bottom:8px">
        <button class="btn btn-primary btn-block" id="btn-dl-mtg" onclick="startDownload('mtg')">Download MTG (All)</button>
      </div>
      <div class="form-group">
        <label>Or download MTG since year:</label>
        <div class="flex-row">
          <input type="number" id="dl-since" min="1993" max="2030" value="2020" style="flex:2">
          <button class="btn btn-secondary" id="btn-dl-mtg-since" onclick="startDownload('mtg', document.getElementById('dl-since').value)" style="flex:1">Go</button>
        </div>
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
    <h3>Delete Data</h3>
    <p style="color:#6BCCBD;font-size:12px;margin-bottom:8px">Remove all downloaded card images for a TCG.</p>
    <div class="flex-row">
      <button class="btn btn-danger btn-block btn-sm" onclick="deleteData('pokemon', this)">Delete Pokemon</button>
      <button class="btn btn-danger btn-block btn-sm" onclick="deleteData('mtg', this)">Delete MTG</button>
    </div>
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

// --- Tab persistence ---
function showTab(name) {
  localStorage.setItem('inkslab_tab', name);
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'collection') { loadSets(); loadRarities(); loadFavorites(); }
  if (name === 'settings') loadSettings();
  if (name === 'downloads') { loadStorage(); pollDownload(); }
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

function renderQueue(d) {
  var tcg = (d.tcg || '').toLowerCase();
  var prev = d.prev_cards || [];
  var next = d.next_cards || [];
  var queueCard = document.getElementById('queue-card');
  if (!prev.length && !next.length) { queueCard.style.display = 'none'; return; }
  queueCard.style.display = 'block';
  var nextWrap = document.getElementById('q-next-wrap');
  var prevWrap = document.getElementById('q-prev-wrap');
  if (next.length) {
    nextWrap.style.display = 'block';
    document.getElementById('q-next-list').innerHTML = next.map(function(c) {
      return '<div class="q-card" onclick="showPreview(\\'' + c.set_id + '\\',\\'' + c.card_id + '\\',\\'' + c.card_num + ' ' + c.set_info.replace(/'/g,"\\\\'") + '\\')">'
        + '<img class="q-thumb" src="/api/card_image/' + tcg + '/' + c.set_id + '/' + c.card_id + '" onerror="this.style.display=\\'none\\'">'
        + '<div class="q-num">' + c.card_num + '</div>'
        + '<div class="q-rarity">' + (c.rarity || '') + '</div></div>';
    }).join('');
  } else { nextWrap.style.display = 'none'; }
  if (prev.length) {
    prevWrap.style.display = 'block';
    document.getElementById('q-prev-list').innerHTML = prev.map(function(c) {
      return '<div class="q-card" onclick="showPreview(\\'' + c.set_id + '\\',\\'' + c.card_id + '\\',\\'' + c.card_num + ' ' + c.set_info.replace(/'/g,"\\\\'") + '\\')">'
        + '<img class="q-thumb" src="/api/card_image/' + tcg + '/' + c.set_id + '/' + c.card_id + '" onerror="this.style.display=\\'none\\'">'
        + '<div class="q-num">' + c.card_num + '</div>'
        + '<div class="q-rarity">' + (c.rarity || '') + '</div></div>';
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
          hidePreviewLoading();
        }
      } else {
        img.style.display = 'none';
        hidePreviewLoading();
      }
      renderQueue(d);
    }
    // Update pause button and countdown
    updatePauseBtn(d.paused);
    updateCountdown();
    // Disable prev button if no history
    document.getElementById('btn-prev').disabled = !(d.prev_cards && d.prev_cards.length);
    // Stop rapid polling once daemon has processed
    if (_rapidPoll && !d.pending && (d.card_path !== _lastStatus.card_path || d.tcg !== _lastStatus.tcg || (_lastStatus.pending && !d.pending))) {
      clearInterval(_rapidPoll);
      _rapidPoll = null;
      _pendingAction = false;
      startMainPoll();
    }
    _lastStatus = d;
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
    .then(function() {
      btn.disabled = false;
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
    .then(function() {
      btn.disabled = false;
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
      if (d.paused) {
        _lastStatus.next_change = 0;
        showToast('Paused');
      } else {
        _lastStatus.next_change = Math.floor(Date.now() / 1000) + (_lastStatus.interval || 600);
        showToast('Resumed');
      }
      updateCountdown();
    });
}

function switchTCG(tcg, activeBtn) {
  var btns = document.querySelectorAll('[onclick^="switchTCG"]');
  btns.forEach(function(b) { b.disabled = true; });
  var orig = activeBtn.textContent;
  activeBtn.textContent = 'Switching...';
  fetch(API + '/api/config', {method:'POST', body: JSON.stringify({active_tcg: tcg}),
    headers:{'Content-Type':'application/json'}})
    .then(function() {
      activeBtn.textContent = orig;
      btns.forEach(function(b) { b.disabled = false; });
      showToast('Switching to ' + tcg.toUpperCase() + '...');
      document.getElementById('st-tcg').textContent = tcg.toUpperCase();
      setOptimisticLoading('Switching to ' + tcg.toUpperCase() + '...');
      startRapidPoll();
    })
    .catch(function() {
      activeBtn.textContent = orig;
      btns.forEach(function(b) { b.disabled = false; });
      showToast('Switch failed');
    });
}

// --- Settings ---
function loadSettings() {
  fetch(API + '/api/config').then(r => r.json()).then(c => {
    document.getElementById('cfg-tcg').value = c.active_tcg;
    document.getElementById('cfg-rotation').value = c.rotation_angle;
    document.getElementById('cfg-day-interval').value = Math.round(c.day_interval / 60);
    document.getElementById('cfg-night-interval').value = Math.round(c.night_interval / 60);
    document.getElementById('cfg-day-start').value = c.day_start;
    document.getElementById('cfg-day-end').value = c.day_end;
    document.getElementById('cfg-saturation').value = c.color_saturation;
    document.getElementById('cfg-collection').checked = c.collection_only;
  });
}

function saveSettings() {
  const cfg = {
    active_tcg: document.getElementById('cfg-tcg').value,
    rotation_angle: parseInt(document.getElementById('cfg-rotation').value),
    day_interval: parseInt(document.getElementById('cfg-day-interval').value) * 60,
    night_interval: parseInt(document.getElementById('cfg-night-interval').value) * 60,
    day_start: parseInt(document.getElementById('cfg-day-start').value),
    day_end: parseInt(document.getElementById('cfg-day-end').value),
    color_saturation: parseFloat(document.getElementById('cfg-saturation').value),
    collection_only: document.getElementById('cfg-collection').checked,
  };
  fetch(API + '/api/config', {method:'POST', body: JSON.stringify(cfg)})
    .then(function() { showToast('Settings saved!'); startRapidPoll(); });
}

// --- Collection ---
function loadSets() {
  const el = document.getElementById('sets-list');
  el.innerHTML = '<div style="color:#6BCCBD;padding:16px;text-align:center">Loading sets...</div>';
  fetch(API + '/api/sets').then(r => r.json()).then(sets => {
    if (!sets.length) { el.innerHTML = '<div style="color:#6BCCBD;padding:16px;text-align:center">No cards downloaded yet.</div>'; return; }
    el.innerHTML = sets.map(s => `
      <div class="set-item">
        <div class="set-header" onclick="toggleSet('${s.id}')">
          <span>
            <span class="set-name">${s.name}</span>
            ${s.owned_count > 0 ? '<span class="badge">' + s.owned_count + '</span>' : ''}
          </span>
          <span class="set-meta">${s.year} &middot; ${s.card_count} cards</span>
        </div>
        <div class="set-cards" id="set-${s.id}"></div>
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
        var safeR = r.replace(/'/g, "\\\\'");
        html += '<span class="rarity-chip' + (isActive ? ' active' : '') + '" data-rarity="' + r + '" onclick="toggleSetRarityChip(this,\\'' + setId + '\\',\\'' + safeR + '\\',' + (isActive ? 'false' : 'true') + ')">'
          + r + '<span class="chip-count">(' + ownedCt + '/' + total + ')</span></span>';
      });
      html += '</div>';
    }
    html += cards.map(c => `
      <div class="card-row" data-rarity="${c.rarity}">
        <label>
          <input type="checkbox" ${c.owned ? 'checked' : ''} onchange="toggleCard('${c.id}')">
          <span class="card-preview-btn" onclick="event.preventDefault();showPreview('${c.set_id}','${c.id}','${c.name.replace(/'/g,"\\\\'")} #${c.number}')">#${c.number} ${c.name}</span>
        </label>
        <span class="card-rarity">${c.rarity}</span>
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
  if (!confirm('Clear your entire collection for the active TCG?')) return;
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
function showPreview(setId, cardId, label) {
  fetch(API + '/api/config').then(r => r.json()).then(cfg => {
    document.getElementById('preview-img').src = '/api/card_image/' + cfg.active_tcg + '/' + setId + '/' + cardId;
    document.getElementById('preview-name').textContent = label;
    document.getElementById('preview-modal').classList.add('open');
  });
}
function closePreview() {
  document.getElementById('preview-modal').classList.remove('open');
}

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
      return '<span class="search-filter-chip">' + name + '<span class="sfc-x" onclick="removeFavorite(\\'' + safeN + '\\')">&times;</span></span>';
    }).join('');
  });
}

function removeFavorite(name) {
  var chip = event.target.parentElement;
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
      var safeN = g.name.replace(/'/g, "\\\\'");
      html += '<div style="border-bottom:1px solid #1F333F;padding:6px 0">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center">';
      html += '<span class="search-result-name">' + g.name + ' <span style="color:#6BCCBD;font-size:11px;font-weight:400">' + ownedCount + '/' + g.cards.length + ' owned</span></span>';
      html += '<button class="btn btn-secondary btn-sm" onclick="toggleSearchGroup(this,\\'' + safeN + '\\',' + (!allOwned) + ')">' + (allOwned ? 'Remove All' : 'Add All') + '</button>';
      html += '</div>';
      html += '<div style="margin-top:4px">';
      g.cards.forEach(function(c) {
        var safeId = c.id.replace(/'/g,"\\\\'");
        html += '<div class="search-result"><label style="display:flex;align-items:center;gap:6px;flex:1;cursor:pointer">';
        html += '<input type="checkbox" ' + (c.owned ? 'checked' : '') + ' onchange="toggleCard(\\'' + safeId + '\\')" style="accent-color:#36A5CA">';
        html += '<span><span class="card-preview-btn" onclick="event.preventDefault();showPreview(\\'' + c.set_id + '\\',\\'' + safeId + '\\',\\'' + c.name.replace(/'/g,"\\\\'") + ' #' + c.number + '\\')">#' + c.number + '</span>';
        html += ' <span class="search-result-set">' + c.set_name + '</span></span>';
        html += '</label><span class="search-result-rarity">' + c.rarity + '</span></div>';
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
    var el = document.getElementById('storage-info');
    if (!info._disk) { el.innerHTML = '<div style="color:#6BCCBD">Loading...</div>'; return; }
    var totalGb = info._disk.total_gb || 1;
    var freeGb = info._disk.free_gb || 0;
    var pokGb = (info.pokemon && info.pokemon.size_gb) || 0;
    var pokMb = (info.pokemon && info.pokemon.size_mb) || 0;
    var mtgGb = (info.mtg && info.mtg.size_gb) || 0;
    var mtgMb = (info.mtg && info.mtg.size_mb) || 0;
    var usedGb = Math.round((totalGb - freeGb) * 100) / 100;
    var otherGb = Math.max(0, Math.round((usedGb - pokGb - mtgGb) * 100) / 100);
    var pokPct = (pokGb / totalGb * 100);
    var mtgPct = (mtgGb / totalGb * 100);
    var otherPct = (otherGb / totalGb * 100);
    var freePct = (freeGb / totalGb * 100);
    // Ensure non-zero segments have minimum visible width
    if (pokGb > 0 && pokPct < 1.5) pokPct = 1.5;
    if (mtgGb > 0 && mtgPct < 1.5) mtgPct = 1.5;
    var html = '<div class="storage-bar-wrap">';
    html += '<div class="storage-bar-label"><span>' + usedGb.toFixed(1) + ' GB used</span><span>' + freeGb.toFixed(1) + ' GB free / ' + totalGb.toFixed(0) + ' GB</span></div>';
    html += '<div class="storage-bar">';
    if (pokGb > 0) html += '<div class="storage-seg seg-pokemon" style="width:' + pokPct.toFixed(1) + '%">' + (pokPct > 8 ? fmtSizeShort(pokGb, pokMb) : '') + '</div>';
    if (mtgGb > 0) html += '<div class="storage-seg seg-mtg" style="width:' + mtgPct.toFixed(1) + '%">' + (mtgPct > 8 ? fmtSizeShort(mtgGb, mtgMb) : '') + '</div>';
    if (otherPct > 0.5) html += '<div class="storage-seg seg-other" style="width:' + otherPct.toFixed(1) + '%">' + (otherPct > 8 ? otherGb.toFixed(1) + 'G' : '') + '</div>';
    html += '<div class="storage-seg seg-free" style="width:' + Math.max(freePct, 1).toFixed(1) + '%">' + (freePct > 12 ? freeGb.toFixed(1) + 'G' : '') + '</div>';
    html += '</div>';
    html += '<div class="storage-legend">';
    html += '<div class="storage-legend-item"><span class="storage-legend-dot" style="background:#36A5CA"></span>Pokemon</div>';
    html += '<div class="storage-legend-item"><span class="storage-legend-dot" style="background:#6BCCBD"></span>MTG</div>';
    html += '<div class="storage-legend-item"><span class="storage-legend-dot" style="background:#8b6bbf"></span>System</div>';
    html += '<div class="storage-legend-item"><span class="storage-legend-dot" style="background:#1F333F;border:1px solid #36A5CA44"></span>Free</div>';
    html += '</div></div>';
    Object.entries(info).filter(function(e) { return !e[0].startsWith('_'); }).forEach(function(e) {
      var tcg = e[0], d = e[1];
      html += '<div class="stat"><span class="stat-label">' + tcg.toUpperCase() + '</span><span class="stat-value">' + d.card_count + ' cards &middot; ' + d.set_count + ' sets &middot; ' + fmtSize(d.size_gb || 0, d.size_mb || 0) + '</span></div>';
    });
    el.innerHTML = html;
  });
}

function setDownloadUI(running, tcg) {
  const btns = document.getElementById('dl-buttons');
  const stopBtn = document.getElementById('btn-dl-stop');
  if (running) {
    btns.querySelectorAll('.btn').forEach(b => b.disabled = true);
    stopBtn.style.display = 'block';
    stopBtn.textContent = 'Stop ' + (tcg || '').toUpperCase() + ' Download';
  } else {
    btns.querySelectorAll('.btn').forEach(b => b.disabled = false);
    stopBtn.style.display = 'none';
  }
}

function startDownload(tcg, since) {
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

// --- Init ---
(function() {
  const saved = localStorage.getItem('inkslab_tab');
  if (saved && document.getElementById('tab-' + saved)) {
    showTab(saved);
  } else {
    refreshStatus();
  }
  startMainPoll();
  startCountdown();
  // Load IP for footer
  fetch(API + '/api/ip').then(r => r.json()).then(d => {
    if (d.ip) document.getElementById('footer-ip').textContent = 'Also available at http://' + d.ip;
  }).catch(() => {});
})();
</script>
</body>
</html>"""


@app.route('/')
def dashboard():
    return DASHBOARD_HTML


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=False)
