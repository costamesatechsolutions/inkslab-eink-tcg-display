#!/usr/bin/python3
"""
Shared state helpers for the InkSlab web dashboard.
"""

import json
import os
import tempfile

from inkslab_paths import COLLECTION_FILE, CONFIG_FILE, STATUS_FILE
from inkslab_plugins import default_enabled_plugins, default_display_schedule, normalize_display_config
from inkslab_update_helpers import normalize_update_branch


WEB_DEFAULTS = {
    "active_tcg": "pokemon",
    "enabled_plugins": default_enabled_plugins("pokemon"),
    "single_plugin": "pokemon",
    "display_mode": "single",
    "display_schedule": default_display_schedule("pokemon"),
    "rotation_angle": 270,
    "day_interval": 600,
    "night_interval": 3600,
    "day_start": 7,
    "day_end": 23,
    "color_saturation": 2.5,
    "collection_only": False,
    "slab_header_mode": "normal",
    "timezone_offset": None,
    "timezone_name": None,
    "update_branch": None,
}


def atomic_write_json(path, data, indent=None):
    dir_name = os.path.dirname(path) or '.'
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_config(script_dir: str):
    config = dict(WEB_DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config.update(json.load(f))
        except Exception:
            pass
    config = normalize_display_config(config)
    config["update_branch"] = normalize_update_branch(script_dir, config.get("update_branch"))
    return config


def save_config(config):
    atomic_write_json(CONFIG_FILE, config, indent=2)


def load_collection():
    if os.path.exists(COLLECTION_FILE):
        try:
            with open(COLLECTION_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_collection(data):
    atomic_write_json(COLLECTION_FILE, data)


def write_status(data):
    try:
        tmp = STATUS_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, STATUS_FILE)
    except Exception:
        pass


def read_status():
    if not os.path.exists(STATUS_FILE):
        return {}
    try:
        with open(STATUS_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def manual_control_block_reason():
    status = read_status()
    pending = str(status.get('pending') or '').strip().lower()
    if pending.startswith('starting up'):
        return "InkSlab is still starting up. Give it a moment, then try again."
    if pending.startswith('updating display plan'):
        return "InkSlab is applying a display plan change. Try again in a moment."
    if status.get('display_updating') and not status.get('card_path'):
        return "InkSlab is still preparing the display. Try again in a moment."
    return None
