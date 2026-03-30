#!/usr/bin/python3
"""
Shared plugin registry for InkSlab.

This is the first step toward a modular architecture without rewriting the
existing TCG product. The current built-in TCG modes are represented as
internal plugins so the daemon and web UI can share a single source of truth.
"""

from dataclasses import asdict, dataclass
import json
import os
import re
import time
from typing import Dict, List, Optional


@dataclass(frozen=True)
class PluginDefinition:
    """Defines an installable/enableable InkSlab mode."""
    plugin_id: str
    name: str
    kind: str
    card_library_path: Optional[str] = None
    accent_color: Optional[str] = None
    download_script: Optional[str] = None
    description: str = ""
    builtin: bool = True
    settings_keys: Optional[List[str]] = None
    source: str = "builtin"
    manifest_path: Optional[str] = None
    runtime_enabled: bool = True

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


BUILTIN_PLUGINS: Dict[str, PluginDefinition] = {
    "pokemon": PluginDefinition(
        plugin_id="pokemon",
        name="Pokemon",
        kind="tcg",
        card_library_path="/home/pi/pokemon_cards",
        accent_color="#36A5CA",
        download_script="download_cards_pokemon.py",
        description="Pokemon card slideshow mode.",
        settings_keys=["collection_only", "slab_header_mode", "rotation_angle", "color_saturation"],
    ),
    "mtg": PluginDefinition(
        plugin_id="mtg",
        name="Magic: The Gathering",
        kind="tcg",
        card_library_path="/home/pi/mtg_cards",
        accent_color="#6BCCBD",
        download_script="download_cards_mtg.py",
        description="Magic: The Gathering card slideshow mode.",
        settings_keys=["collection_only", "slab_header_mode", "rotation_angle", "color_saturation"],
    ),
    "lorcana": PluginDefinition(
        plugin_id="lorcana",
        name="Disney Lorcana",
        kind="tcg",
        card_library_path="/home/pi/lorcana_cards",
        accent_color="#C084FC",
        download_script="download_cards_lorcana.py",
        description="Disney Lorcana card slideshow mode.",
        settings_keys=["collection_only", "slab_header_mode", "rotation_angle", "color_saturation"],
    ),
    "custom": PluginDefinition(
        plugin_id="custom",
        name="Custom",
        kind="tcg",
        card_library_path="/home/pi/custom_cards",
        accent_color="#F59E0B",
        description="User-uploaded custom image slideshow mode.",
        settings_keys=["collection_only", "slab_header_mode", "rotation_angle", "color_saturation"],
    ),
}

PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
PLUGIN_DIRS = [
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "plugins"),
    "/home/pi/inkslab_plugins",
]


def _safe_manifest_value(value, default=""):
    return str(value).strip() if value is not None else default


def _load_external_plugin_manifest(manifest_path: str) -> Optional[PluginDefinition]:
    """Load a plugin manifest safely without executing plugin code."""
    try:
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
    except Exception:
        return None
    if not isinstance(manifest, dict):
        return None

    plugin_id = _safe_manifest_value(manifest.get("plugin_id"))
    if not PLUGIN_ID_RE.match(plugin_id):
        return None
    name = _safe_manifest_value(manifest.get("name"))[:48]
    if not name:
        return None
    kind = _safe_manifest_value(manifest.get("kind"), "module").lower()
    if kind not in ("module", "tcg", "weather", "news", "market", "calendar", "reminders", "transit", "traffic", "sports"):
        kind = "module"

    settings_keys = manifest.get("settings_keys")
    if isinstance(settings_keys, list):
        settings_keys = [str(k).strip() for k in settings_keys[:20] if str(k).strip()]
    else:
        settings_keys = []

    return PluginDefinition(
        plugin_id=plugin_id,
        name=name,
        kind=kind,
        accent_color=_safe_manifest_value(manifest.get("accent_color")) or None,
        description=_safe_manifest_value(manifest.get("description"))[:160],
        builtin=False,
        settings_keys=settings_keys,
        source="local-manifest",
        manifest_path=manifest_path,
        runtime_enabled=False,
    )


def discover_external_plugins() -> Dict[str, PluginDefinition]:
    """Discover local plugin manifests without executing plugin code."""
    discovered: Dict[str, PluginDefinition] = {}
    for base_dir in PLUGIN_DIRS:
        if not os.path.isdir(base_dir):
            continue
        try:
            entries = sorted(os.listdir(base_dir))
        except OSError:
            continue
        for entry in entries:
            plugin_dir = os.path.join(base_dir, entry)
            manifest_path = os.path.join(plugin_dir, "manifest.json")
            if not os.path.isdir(plugin_dir) or not os.path.isfile(manifest_path):
                continue
            plugin = _load_external_plugin_manifest(manifest_path)
            if not plugin or plugin.plugin_id in BUILTIN_PLUGINS or plugin.plugin_id in discovered:
                continue
            discovered[plugin.plugin_id] = plugin
    return discovered


def get_plugins() -> Dict[str, PluginDefinition]:
    """Return built-in plugins plus safe-discovered local plugin manifests."""
    plugins = dict(BUILTIN_PLUGINS)
    plugins.update(discover_external_plugins())
    return plugins


def get_plugin(plugin_id: str) -> Optional[PluginDefinition]:
    """Return a plugin definition by ID, or None if missing."""
    return get_plugins().get(plugin_id)


def get_plugin_payload() -> Dict[str, Dict[str, object]]:
    """Return plugin metadata in a JSON-friendly shape for the web UI."""
    return {plugin_id: plugin.to_dict() for plugin_id, plugin in get_plugins().items()}


def get_card_libraries() -> Dict[str, str]:
    """Return plugin -> card library path for TCG-backed plugins."""
    return {
        plugin_id: plugin.card_library_path
        for plugin_id, plugin in get_plugins().items()
        if plugin.card_library_path
    }


def get_plugin_ids() -> List[str]:
    """Return valid plugin IDs."""
    return list(get_plugins().keys())


def normalize_active_plugin(value: Optional[str], default: str = "pokemon") -> str:
    """Normalize a requested active plugin ID to a known built-in plugin."""
    if value in get_plugins():
        return str(value)
    return default


def default_display_schedule(default_plugin: str = "pokemon") -> List[Dict[str, object]]:
    """Return a simple all-day schedule seeded with one plugin."""
    plugin_id = normalize_active_plugin(default_plugin)
    return [{
        "plugin_ids": [plugin_id],
        "label": "All Day",
        "start_hour": 0,
        "end_hour": 24,
        "enabled": True,
        "rotation_minutes": 10,
    }]


def normalize_display_schedule(schedule, default_plugin: str = "pokemon") -> List[Dict[str, object]]:
    """Normalize saved schedule data into a safe, minimal structure."""
    normalized = []
    if isinstance(schedule, list):
        for item in schedule[:8]:
            if not isinstance(item, dict):
                continue
            raw_ids = item.get("plugin_ids")
            if not isinstance(raw_ids, list):
                legacy_single = item.get("plugin_id")
                raw_ids = [legacy_single] if legacy_single else [default_plugin]
            plugin_ids = []
            for raw_id in raw_ids[:8]:
                normalized_id = normalize_active_plugin(raw_id, default_plugin)
                if normalized_id not in plugin_ids:
                    plugin_ids.append(normalized_id)
            if not plugin_ids:
                plugin_ids = [normalize_active_plugin(default_plugin)]
            try:
                start_hour = max(0, min(23, int(item.get("start_hour", 0))))
            except (TypeError, ValueError):
                start_hour = 0
            try:
                end_hour = max(1, min(24, int(item.get("end_hour", 24))))
            except (TypeError, ValueError):
                end_hour = 24
            if end_hour <= start_hour:
                end_hour = min(24, start_hour + 1)
            try:
                rotation_minutes = max(1, min(1440, int(item.get("rotation_minutes", 10))))
            except (TypeError, ValueError):
                rotation_minutes = 10
            label = str(item.get("label", "")).strip()[:40] or plugin_ids[0].replace("_", " ").title()
            normalized.append({
                "plugin_ids": plugin_ids,
                "label": label,
                "start_hour": start_hour,
                "end_hour": end_hour,
                "enabled": bool(item.get("enabled", True)),
                "rotation_minutes": rotation_minutes,
            })
    if not normalized:
        return default_display_schedule(default_plugin)
    return normalized


def resolve_active_plugin(config: Dict[str, object], now_struct=None) -> str:
    """Resolve the currently active plugin from single/scheduled display config."""
    single_plugin = normalize_active_plugin(
        config.get("single_plugin") or config.get("active_plugin") or config.get("active_tcg"),
        "pokemon",
    )
    mode = str(config.get("display_mode") or "single").strip().lower()
    if mode != "schedule":
        return single_plugin

    schedule = normalize_display_schedule(config.get("display_schedule"), single_plugin)
    now_struct = now_struct or time.localtime()
    current_hour = now_struct.tm_hour
    current_minute = now_struct.tm_hour * 60 + now_struct.tm_min
    for item in schedule:
        if not item.get("enabled", True):
            continue
        if item["start_hour"] <= current_hour < item["end_hour"]:
            plugin_ids = item.get("plugin_ids") or [single_plugin]
            if len(plugin_ids) == 1:
                return plugin_ids[0]
            rotation_minutes = max(1, int(item.get("rotation_minutes", 10)))
            slot = (current_minute // rotation_minutes) % len(plugin_ids)
            return plugin_ids[slot]
    return single_plugin


def normalize_display_config(config: Dict[str, object]) -> Dict[str, object]:
    """Normalize display planning config while preserving current behavior."""
    normalized = dict(config)
    single_plugin = normalize_active_plugin(
        normalized.get("single_plugin") or normalized.get("active_plugin") or normalized.get("active_tcg"),
        "pokemon",
    )
    mode = str(normalized.get("display_mode") or "single").strip().lower()
    if mode not in ("single", "schedule"):
        mode = "single"
    schedule = normalize_display_schedule(normalized.get("display_schedule"), single_plugin)
    normalized["single_plugin"] = single_plugin
    normalized["display_mode"] = mode
    normalized["display_schedule"] = schedule
    resolved_plugin = resolve_active_plugin(normalized)
    normalized["active_plugin"] = resolved_plugin
    normalized["active_tcg"] = resolved_plugin
    return normalized
