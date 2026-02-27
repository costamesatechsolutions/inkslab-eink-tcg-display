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
_download_lock = threading.Lock()


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
    return jsonify(config)


@app.route('/api/next', methods=['POST'])
def api_next():
    try:
        with open(NEXT_TRIGGER, 'w') as f:
            f.write('1')
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


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
                return send_file(card_path, mimetype='image/png')
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
    return jsonify({"set_id": set_id, "owned": owned, "count": len(card_ids)})


@app.route('/api/collection/clear', methods=['POST'])
def api_collection_clear():
    config = load_config()
    tcg = config["active_tcg"]
    collection = load_collection()
    collection[tcg] = []
    save_collection(collection)
    return jsonify({"ok": True})


@app.route('/api/download/start', methods=['POST'])
def api_download_start():
    global _download_proc, _download_tcg

    with _download_lock:
        if _download_proc and _download_proc.poll() is None:
            return jsonify({"ok": False, "error": "Download already running"})

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

        try:
            open(DOWNLOAD_LOG, 'w').close()
        except Exception:
            pass

        log_file = open(DOWNLOAD_LOG, 'w')
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        _download_proc = subprocess.Popen(
            cmd, stdout=log_file, stderr=subprocess.STDOUT,
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
        else:
            tcg = None

    lines = []
    if os.path.exists(DOWNLOAD_LOG):
        try:
            with open(DOWNLOAD_LOG, 'r') as f:
                all_lines = f.readlines()
                lines = [l.rstrip() for l in all_lines[-30:]]
        except Exception:
            pass

    return jsonify({"running": running, "tcg": tcg, "lines": lines})


@app.route('/api/storage')
def api_storage():
    info = {}
    for tcg, path in TCG_LIBRARIES.items():
        if os.path.exists(path):
            total_size = 0
            card_count = 0
            set_count = 0
            for root, dirs, files in os.walk(path):
                if root == path:
                    set_count = len([d for d in dirs])
                for f in files:
                    fp = os.path.join(root, f)
                    total_size += os.path.getsize(fp)
                    if f.endswith('.png') and not f.startswith('_'):
                        card_count += 1
            info[tcg] = {
                "path": path,
                "size_mb": round(total_size / (1024 * 1024)),
                "card_count": card_count,
                "set_count": set_count,
            }
        else:
            info[tcg] = {"path": path, "size_mb": 0, "card_count": 0, "set_count": 0}
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
.card-preview-btn { cursor: pointer; color: #36A5CA; font-size: 11px; margin-left: 6px; text-decoration: underline; }
.log-box { background: #0a1a22; border-radius: 6px; padding: 10px; font-family: monospace; font-size: 11px; max-height: 300px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; color: #6BCCBD; border: 1px solid #1F333F; }
.badge { display: inline-block; background: #6BCCBD; color: #010001; border-radius: 10px; padding: 1px 7px; font-size: 10px; margin-left: 4px; font-weight: 700; }
.flex-row { display: flex; gap: 8px; }
.flex-row > * { flex: 1; }
.preview-img { display: block; margin: 12px auto 0; max-width: 150px; border-radius: 6px; border: 2px solid #1F333F; }
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
    <img id="st-preview" class="preview-img" src="/api/card_image" onerror="this.style.display='none'" onload="this.style.display='block'">
    <div style="margin-top:12px">
      <div class="stat"><span class="stat-label">Card</span><span class="stat-value" id="st-card">&mdash;</span></div>
      <div class="stat"><span class="stat-label">Set</span><span class="stat-value" id="st-set">&mdash;</span></div>
      <div class="stat"><span class="stat-label">Rarity</span><span class="stat-value" id="st-rarity">&mdash;</span></div>
      <div class="stat"><span class="stat-label">TCG</span><span class="stat-value" id="st-tcg">&mdash;</span></div>
      <div class="stat"><span class="stat-label">Cards in Deck</span><span class="stat-value" id="st-total">&mdash;</span></div>
    </div>
  </div>
  <div class="flex-row" style="margin-bottom:12px">
    <button class="btn btn-primary btn-block" onclick="nextCard()">Next Card</button>
  </div>
  <div class="card">
    <h3>Quick Switch</h3>
    <div class="flex-row">
      <button class="btn btn-secondary btn-block" onclick="switchTCG('pokemon')">Pokemon</button>
      <button class="btn btn-secondary btn-block" onclick="switchTCG('mtg')">MTG</button>
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
    <p style="color:#6BCCBD;font-size:12px;margin-bottom:8px">Mark the cards you own. Enable "collection mode" in Settings to only display owned cards. Tap a card name to preview it.</p>
    <button class="btn btn-secondary btn-sm" onclick="clearCollection()">Clear All</button>
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
      <button class="btn btn-danger btn-block btn-sm" onclick="deleteData('pokemon')">Delete Pokemon</button>
      <button class="btn btn-danger btn-block btn-sm" onclick="deleteData('mtg')">Delete MTG</button>
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
  if (name === 'collection') loadSets();
  if (name === 'settings') loadSettings();
  if (name === 'downloads') { loadStorage(); pollDownload(); }
  if (name === 'display') refreshStatus();
}

// --- Display ---
function refreshStatus() {
  fetch(API + '/api/status').then(r => r.json()).then(d => {
    document.getElementById('st-card').textContent = d.card_num || '\\u2014';
    document.getElementById('st-set').textContent = d.set_info || '\\u2014';
    document.getElementById('st-rarity').textContent = d.rarity || '\\u2014';
    document.getElementById('st-tcg').textContent = (d.tcg || '\\u2014').toUpperCase();
    document.getElementById('st-total').textContent = d.total_cards || '\\u2014';
    // Refresh preview image with cache buster
    const img = document.getElementById('st-preview');
    img.src = '/api/card_image?t=' + Date.now();
  }).catch(() => {});
}

function nextCard() {
  fetch(API + '/api/next', {method:'POST'}).then(() => {
    setTimeout(refreshStatus, 3000);
  });
}

function switchTCG(tcg) {
  fetch(API + '/api/config', {method:'POST', body: JSON.stringify({active_tcg: tcg})})
    .then(() => setTimeout(refreshStatus, 2000));
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
    .then(() => alert('Settings saved! Changes take effect within 30 seconds.'));
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
    let html = '<div style="padding:4px 0 6px;display:flex;gap:4px">';
    html += `<button class="btn btn-secondary btn-sm" onclick="toggleSetAll('${setId}',true)">Select All</button>`;
    html += `<button class="btn btn-secondary btn-sm" onclick="toggleSetAll('${setId}',false)">Deselect All</button>`;
    html += '</div>';
    html += cards.map(c => `
      <div class="card-row">
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
  fetch(API + '/api/collection/clear', {method:'POST'}).then(() => loadSets());
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

// --- Downloads ---
function loadStorage() {
  fetch(API + '/api/storage').then(r => r.json()).then(info => {
    const el = document.getElementById('storage-info');
    el.innerHTML = Object.entries(info).map(([tcg, d]) =>
      `<div class="stat"><span class="stat-label">${tcg.toUpperCase()}</span><span class="stat-value">${d.card_count} cards (${d.set_count} sets, ${d.size_mb} MB)</span></div>`
    ).join('');
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

function deleteData(tcg) {
  if (!confirm('Delete ALL ' + tcg.toUpperCase() + ' card images? This cannot be undone.')) return;
  fetch(API + '/api/delete', {method:'POST', body: JSON.stringify({tcg: tcg})})
    .then(r => r.json()).then(d => {
      if (d.ok) loadStorage();
      else alert(d.error || 'Delete failed');
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
  setInterval(refreshStatus, 30000);
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
