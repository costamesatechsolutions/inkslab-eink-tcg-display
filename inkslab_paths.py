#!/usr/bin/python3
"""
Shared filesystem and runtime paths for InkSlab.
"""

import os


PROJECT_ROOT = os.path.dirname(os.path.realpath(__file__))
HOME_DIR = os.environ.get("INKSLAB_HOME_DIR", "/home/pi")
APP_DIR = os.environ.get("INKSLAB_APP_DIR", PROJECT_ROOT)
RUNTIME_DIR = os.environ.get("INKSLAB_RUNTIME_DIR", "/tmp")

CONFIG_FILE = os.path.join(HOME_DIR, "inkslab_config.json")
COLLECTION_FILE = os.path.join(HOME_DIR, "inkslab_collection.json")

STATUS_FILE = os.path.join(RUNTIME_DIR, "inkslab_status.json")
DOWNLOAD_LOG = os.path.join(RUNTIME_DIR, "inkslab_download.log")
UPDATE_STATUS_FILE = os.path.join(RUNTIME_DIR, "inkslab_update_status.json")
UPDATE_LOCK_FILE = os.path.join(RUNTIME_DIR, "inkslab_update.lock")
WEATHER_CACHE_FILE = os.path.join(RUNTIME_DIR, "inkslab_weather_cache.json")
NEWS_CACHE_FILE = os.path.join(RUNTIME_DIR, "inkslab_news_cache.json")
CALENDAR_CACHE_FILE = os.path.join(RUNTIME_DIR, "inkslab_calendar_cache.json")
MARKET_CACHE_FILE = os.path.join(RUNTIME_DIR, "inkslab_market_cache.json")
CURRENT_PREVIEW_FILE = os.path.join(RUNTIME_DIR, "inkslab_current_preview.png")
PLUGIN_RUNTIME_CACHE_DIR = os.path.join(RUNTIME_DIR, "inkslab_plugin_runtime")
USER_PLUGIN_DIR = os.path.join(HOME_DIR, "inkslab_plugins")

NEXT_TRIGGER = os.path.join(RUNTIME_DIR, "inkslab_next")
PREV_TRIGGER = os.path.join(RUNTIME_DIR, "inkslab_prev")
PAUSE_FILE = os.path.join(RUNTIME_DIR, "inkslab_pause")
COLLECTION_TRIGGER = os.path.join(RUNTIME_DIR, "inkslab_collection_changed")
LIBRARY_TRIGGER = os.path.join(RUNTIME_DIR, "inkslab_library_changed")
WIFI_CONNECTED_TRIGGER = os.path.join(RUNTIME_DIR, "inkslab_wifi_connected")
WIFI_SETUP_TRIGGER = os.path.join(RUNTIME_DIR, "inkslab_wifi_setup")
WIFI_FAILED_TRIGGER = os.path.join(RUNTIME_DIR, "inkslab_wifi_failed")
UNBOX_TRIGGER = os.path.join(RUNTIME_DIR, "inkslab_unbox")
WATCHDOG_SETUP_FLAG = os.path.join(RUNTIME_DIR, "inkslab_watchdog_setup")


def card_library_path(dirname: str) -> str:
    return os.path.join(HOME_DIR, dirname)


def plugin_search_dirs():
    return [
        os.path.join(PROJECT_ROOT, "plugins"),
        USER_PLUGIN_DIR,
    ]
