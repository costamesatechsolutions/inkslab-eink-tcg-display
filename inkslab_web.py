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
import subprocess
import time
import threading
from flask import Flask, request, jsonify

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
    # Only allow known keys
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


@app.route('/api/sets')
def api_sets():
    config = load_config()
    tcg = request.args.get('tcg', config['active_tcg'])
    library = TCG_LIBRARIES.get(tcg)
    if not library or not os.path.exists(library):
        return jsonify([])

    # Load master index for set names
    master = {}
    index_path = os.path.join(library, "master_index.json")
    if os.path.exists(index_path):
        try:
            with open(index_path, 'r') as f:
                master = json.load(f)
        except Exception:
            pass

    # Load collection for owned counts
    collection = load_collection()
    owned_ids = set(collection.get(tcg, []))

    sets = []
    for d in sorted(os.listdir(library)):
        set_path = os.path.join(library, d)
        if not os.path.isdir(set_path):
            continue

        # Count cards
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

    # Sort by year descending
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

    # Load _data.json for metadata
    metadata = {}
    data_file = os.path.join(set_path, "_data.json")
    if os.path.exists(data_file):
        try:
            with open(data_file, 'r') as f:
                metadata = json.load(f)
        except Exception:
            pass

    # Load collection
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
        })

    # Sort by number
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
    """Toggle all cards in a set on or off."""
    data = request.get_json(force=True)
    set_id = data.get("set_id")
    owned = data.get("owned", True)  # True = select all, False = deselect all
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

    # Get all card IDs in the set
    card_ids = [os.path.splitext(f)[0] for f in os.listdir(set_path)
                if f.endswith('.png') and not f.startswith('_')]

    collection = load_collection()
    if tcg not in collection:
        collection[tcg] = []

    if owned:
        # Add all cards not already in collection
        existing = set(collection[tcg])
        for cid in card_ids:
            if cid not in existing:
                collection[tcg].append(cid)
    else:
        # Remove all cards in this set
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
    global _download_proc

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

        # Clear old log
        try:
            open(DOWNLOAD_LOG, 'w').close()
        except Exception:
            pass

        log_file = open(DOWNLOAD_LOG, 'w')
        _download_proc = subprocess.Popen(
            cmd, stdout=log_file, stderr=subprocess.STDOUT,
            cwd=SCRIPT_DIR
        )

        return jsonify({"ok": True, "tcg": tcg, "pid": _download_proc.pid})


@app.route('/api/download/status')
def api_download_status():
    global _download_proc

    running = False
    with _download_lock:
        if _download_proc and _download_proc.poll() is None:
            running = True

    # Read last 20 lines of log
    lines = []
    if os.path.exists(DOWNLOAD_LOG):
        try:
            with open(DOWNLOAD_LOG, 'r') as f:
                all_lines = f.readlines()
                lines = [l.rstrip() for l in all_lines[-20:]]
        except Exception:
            pass

    return jsonify({"running": running, "lines": lines})


@app.route('/api/storage')
def api_storage():
    """Get storage info for each TCG library."""
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


# --- DASHBOARD HTML ---

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>InkSlab Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f0f0f; color: #e0e0e0; }
.header { background: #1a1a2e; padding: 16px; text-align: center; border-bottom: 2px solid #e94560; }
.header h1 { font-size: 20px; color: #fff; }
.header small { color: #888; font-size: 11px; }
.tabs { display: flex; background: #16213e; border-bottom: 1px solid #333; }
.tab { flex: 1; padding: 12px 8px; text-align: center; cursor: pointer; color: #888; font-size: 13px; border-bottom: 2px solid transparent; transition: all 0.2s; }
.tab.active { color: #e94560; border-bottom-color: #e94560; }
.panel { display: none; padding: 16px; }
.panel.active { display: block; }
.card { background: #1a1a2e; border-radius: 8px; padding: 16px; margin-bottom: 12px; }
.card h3 { color: #e94560; margin-bottom: 8px; font-size: 14px; }
.stat { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #222; font-size: 14px; }
.stat:last-child { border-bottom: none; }
.stat-label { color: #888; }
.btn { display: inline-block; padding: 10px 20px; border-radius: 6px; border: none; cursor: pointer; font-size: 14px; font-weight: 600; transition: all 0.2s; }
.btn-primary { background: #e94560; color: #fff; }
.btn-primary:hover { background: #c73e54; }
.btn-secondary { background: #333; color: #e0e0e0; }
.btn-secondary:hover { background: #444; }
.btn-sm { padding: 6px 12px; font-size: 12px; }
.btn-block { display: block; width: 100%; text-align: center; }
select, input[type=number], input[type=range] { background: #222; color: #e0e0e0; border: 1px solid #444; border-radius: 4px; padding: 8px; font-size: 14px; width: 100%; }
.form-group { margin-bottom: 12px; }
.form-group label { display: block; color: #888; font-size: 12px; margin-bottom: 4px; }
.toggle { display: flex; align-items: center; gap: 8px; }
.toggle input[type=checkbox] { width: 18px; height: 18px; }
.set-item { background: #1a1a2e; border-radius: 6px; margin-bottom: 4px; overflow: hidden; }
.set-header { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; cursor: pointer; }
.set-header:hover { background: #222; }
.set-name { font-size: 13px; font-weight: 600; }
.set-meta { font-size: 11px; color: #888; }
.set-cards { display: none; padding: 0 12px 8px; }
.set-cards.open { display: block; }
.card-row { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; border-bottom: 1px solid #1a1a1a; font-size: 12px; }
.card-row label { flex: 1; cursor: pointer; display: flex; align-items: center; gap: 6px; }
.card-rarity { color: #888; font-size: 11px; }
.log-box { background: #111; border-radius: 6px; padding: 10px; font-family: monospace; font-size: 11px; max-height: 300px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; color: #aaa; }
.badge { display: inline-block; background: #e94560; color: #fff; border-radius: 10px; padding: 1px 7px; font-size: 10px; margin-left: 4px; }
.flex-row { display: flex; gap: 8px; }
.flex-row > * { flex: 1; }
</style>
</head>
<body>

<div class="header">
  <h1>InkSlab</h1>
  <small>Costa Mesa Tech Solutions</small>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('display')">Display</div>
  <div class="tab" onclick="showTab('settings')">Settings</div>
  <div class="tab" onclick="showTab('collection')">Collection</div>
  <div class="tab" onclick="showTab('downloads')">Downloads</div>
</div>

<!-- DISPLAY TAB -->
<div id="tab-display" class="panel active">
  <div class="card">
    <h3>Now Showing</h3>
    <div class="stat"><span class="stat-label">Card</span><span id="st-card">—</span></div>
    <div class="stat"><span class="stat-label">Set</span><span id="st-set">—</span></div>
    <div class="stat"><span class="stat-label">Rarity</span><span id="st-rarity">—</span></div>
    <div class="stat"><span class="stat-label">TCG</span><span id="st-tcg">—</span></div>
    <div class="stat"><span class="stat-label">Cards in Deck</span><span id="st-total">—</span></div>
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
    <p style="color:#888;font-size:12px;margin-bottom:8px">Mark the cards you own. Enable "collection mode" in Settings to only display owned cards.</p>
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
    <div class="flex-row" style="margin-bottom:8px">
      <button class="btn btn-primary btn-block" onclick="startDownload('pokemon')">Download Pokemon</button>
    </div>
    <div class="flex-row" style="margin-bottom:8px">
      <button class="btn btn-primary btn-block" onclick="startDownload('mtg')">Download MTG (All)</button>
    </div>
    <div class="form-group">
      <label>Or download MTG since year:</label>
      <div class="flex-row">
        <input type="number" id="dl-since" min="1993" max="2030" value="2020" style="flex:2">
        <button class="btn btn-secondary" onclick="startDownload('mtg', document.getElementById('dl-since').value)" style="flex:1">Go</button>
      </div>
    </div>
  </div>
  <div class="card">
    <h3>Download Log</h3>
    <div id="dl-status" style="font-size:12px;margin-bottom:8px;color:#888">Idle</div>
    <div id="dl-log" class="log-box">No download running.</div>
  </div>
</div>

<script>
const API = '';

function showTab(name) {
  document.querySelectorAll('.tab').forEach((t, i) => {
    t.classList.toggle('active', t.textContent.toLowerCase().includes(name.slice(0,4)));
  });
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
    document.getElementById('st-card').textContent = d.card_num || '—';
    document.getElementById('st-set').textContent = d.set_info || '—';
    document.getElementById('st-rarity').textContent = d.rarity || '—';
    document.getElementById('st-tcg').textContent = (d.tcg || '—').toUpperCase();
    document.getElementById('st-total').textContent = d.total_cards || '—';
  }).catch(() => {});
}

function nextCard() {
  fetch(API + '/api/next', {method:'POST'}).then(() => {
    setTimeout(refreshStatus, 3000);
  });
}

function switchTCG(tcg) {
  fetch(API + '/api/config', {method:'POST', body: JSON.stringify({active_tcg: tcg})})
    .then(() => refreshStatus());
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
  el.innerHTML = '<div style="color:#888;padding:16px;text-align:center">Loading sets...</div>';
  fetch(API + '/api/sets').then(r => r.json()).then(sets => {
    if (!sets.length) { el.innerHTML = '<div style="color:#888;padding:16px;text-align:center">No cards downloaded yet.</div>'; return; }
    el.innerHTML = sets.map(s => `
      <div class="set-item">
        <div class="set-header" onclick="toggleSet('${s.id}', this)">
          <span>
            <span class="set-name">${s.name}</span>
            ${s.owned_count > 0 ? '<span class=\\'badge\\'>' + s.owned_count + '</span>' : ''}
          </span>
          <span class="set-meta">${s.year} &middot; ${s.card_count} cards</span>
        </div>
        <div class="set-cards" id="set-${s.id}"></div>
      </div>
    `).join('');
  });
}

function toggleSet(setId, headerEl) {
  const el = document.getElementById('set-' + setId);
  if (el.classList.contains('open')) {
    el.classList.remove('open');
    return;
  }
  el.classList.add('open');
  if (el.dataset.loaded) return;
  el.innerHTML = '<div style="padding:8px;color:#888;font-size:12px">Loading...</div>';
  fetch(API + '/api/sets/' + setId + '/cards').then(r => r.json()).then(cards => {
    el.dataset.loaded = '1';
    let html = '<div style="padding:4px 0 6px;display:flex;gap:4px">';
    html += '<button class="btn btn-secondary btn-sm" onclick="toggleSetAll(\\'' + setId + '\\',true)">Select All</button>';
    html += '<button class="btn btn-secondary btn-sm" onclick="toggleSetAll(\\'' + setId + '\\',false)">Deselect All</button>';
    html += '</div>';
    html += cards.map(c => `
      <div class="card-row">
        <label><input type="checkbox" ${c.owned ? 'checked' : ''} onchange="toggleCard('${c.id}', this)"> #${c.number} ${c.name}</label>
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

// --- Downloads ---
function loadStorage() {
  fetch(API + '/api/storage').then(r => r.json()).then(info => {
    const el = document.getElementById('storage-info');
    el.innerHTML = Object.entries(info).map(([tcg, d]) =>
      `<div class="stat"><span class="stat-label">${tcg.toUpperCase()}</span><span>${d.card_count} cards (${d.set_count} sets, ${d.size_mb} MB)</span></div>`
    ).join('');
  });
}

function startDownload(tcg, since) {
  const body = {tcg: tcg};
  if (since) body.since = parseInt(since);
  fetch(API + '/api/download/start', {method:'POST', body: JSON.stringify(body)})
    .then(r => r.json()).then(d => {
      if (d.ok) {
        document.getElementById('dl-status').textContent = 'Downloading ' + tcg + '...';
        pollDownload();
      } else {
        alert(d.error || 'Failed to start download');
      }
    });
}

let _dlPoll = null;
function pollDownload() {
  if (_dlPoll) clearInterval(_dlPoll);
  _dlPoll = setInterval(() => {
    fetch(API + '/api/download/status').then(r => r.json()).then(d => {
      const logEl = document.getElementById('dl-log');
      logEl.textContent = d.lines.join('\\n') || 'No output yet.';
      logEl.scrollTop = logEl.scrollHeight;
      document.getElementById('dl-status').textContent = d.running ? 'Downloading...' : 'Idle';
      if (!d.running && _dlPoll) { clearInterval(_dlPoll); _dlPoll = null; loadStorage(); }
    });
  }, 3000);
}

// --- Init ---
refreshStatus();
setInterval(refreshStatus, 30000);
</script>
</body>
</html>"""


@app.route('/')
def dashboard():
    return DASHBOARD_HTML


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=False)
