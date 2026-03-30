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
from inkslab_paths import card_library_path, plugin_search_dirs


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
    config_schema: Optional[List[Dict[str, str]]] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


BUILTIN_PLUGINS: Dict[str, PluginDefinition] = {
    "pokemon": PluginDefinition(
        plugin_id="pokemon",
        name="Pokemon",
        kind="tcg",
        card_library_path=card_library_path("pokemon_cards"),
        accent_color="#36A5CA",
        download_script="download_cards_pokemon.py",
        description="Pokemon card slideshow mode.",
        settings_keys=["collection_only", "slab_header_mode", "rotation_angle", "color_saturation"],
    ),
    "mtg": PluginDefinition(
        plugin_id="mtg",
        name="Magic: The Gathering",
        kind="tcg",
        card_library_path=card_library_path("mtg_cards"),
        accent_color="#6BCCBD",
        download_script="download_cards_mtg.py",
        description="Magic: The Gathering card slideshow mode.",
        settings_keys=["collection_only", "slab_header_mode", "rotation_angle", "color_saturation"],
    ),
    "lorcana": PluginDefinition(
        plugin_id="lorcana",
        name="Disney Lorcana",
        kind="tcg",
        card_library_path=card_library_path("lorcana_cards"),
        accent_color="#C084FC",
        download_script="download_cards_lorcana.py",
        description="Disney Lorcana card slideshow mode.",
        settings_keys=["collection_only", "slab_header_mode", "rotation_angle", "color_saturation"],
    ),
    "custom": PluginDefinition(
        plugin_id="custom",
        name="Custom",
        kind="tcg",
        card_library_path=card_library_path("custom_cards"),
        accent_color="#F59E0B",
        description="User-uploaded custom image slideshow mode.",
        settings_keys=["collection_only", "slab_header_mode", "rotation_angle", "color_saturation"],
    ),
    "weather": PluginDefinition(
        plugin_id="weather",
        name="Weather",
        kind="weather",
        accent_color="#5AA9E6",
        description="Ambient weather snapshot for e-ink dashboards.",
        builtin=True,
        runtime_enabled=True,
        settings_keys=["location_name", "weather_units", "weather_refresh_minutes"],
        config_schema=[
            {"key": "location_name", "label": "Location", "type": "text", "default": ""},
            {
                "key": "weather_units",
                "label": "Units",
                "type": "select",
                "default": "imperial",
                "options": [
                    {"value": "imperial", "label": "Imperial"},
                    {"value": "metric", "label": "Metric"},
                ],
            },
            {"key": "weather_refresh_minutes", "label": "Refresh Minutes", "type": "number", "default": 30},
        ],
    ),
    "news": PluginDefinition(
        plugin_id="news",
        name="News Headlines",
        kind="news",
        accent_color="#F28482",
        description="Top RSS headlines in a calm, summary-first layout. Use a preset or paste your own public RSS feed URL.",
        builtin=True,
        runtime_enabled=True,
        settings_keys=["news_feed_preset", "news_feed_url", "news_refresh_minutes", "news_headline_count"],
        config_schema=[
            {
                "key": "news_feed_preset",
                "label": "Feed Preset",
                "type": "select",
                "default": "npr_top",
                "options": [
                    {"value": "npr_top", "label": "NPR Top News"},
                    {"value": "google_top", "label": "Google News Top Stories"},
                    {"value": "custom", "label": "Custom RSS URL"},
                ],
            },
            {"key": "news_feed_url", "label": "Custom RSS Feed URL", "type": "text", "default": ""},
            {"key": "news_refresh_minutes", "label": "Refresh Minutes", "type": "number", "default": 30},
            {"key": "news_headline_count", "label": "Headline Count", "type": "number", "default": 4},
        ],
    ),
    "market": PluginDefinition(
        plugin_id="market",
        name="Market Snapshot",
        kind="market",
        accent_color="#84A98C",
        description="A simple stocks or crypto overview. Demo mode works immediately, and free no-key live quotes can use Yahoo Finance.",
        builtin=True,
        runtime_enabled=True,
        settings_keys=["market_demo_mode", "market_symbols", "market_refresh_minutes"],
        config_schema=[
            {
                "key": "market_demo_mode",
                "label": "Demo Mode",
                "type": "select",
                "default": "on",
                "options": [
                    {"value": "on", "label": "On"},
                    {"value": "off", "label": "Off"},
                ],
            },
            {"key": "market_symbols", "label": "Symbols (comma separated)", "type": "text", "default": "SPY,QQQ,BTC-USD"},
            {"key": "market_refresh_minutes", "label": "Refresh Minutes", "type": "number", "default": 30},
        ],
    ),
    "calendar": PluginDefinition(
        plugin_id="calendar",
        name="Calendar Agenda",
        kind="calendar",
        accent_color="#B8C0FF",
        description="Upcoming events from a private iCal / ICS calendar feed in a low-distraction view.",
        builtin=True,
        runtime_enabled=True,
        settings_keys=["calendar_ics_url", "calendar_demo_mode", "calendar_refresh_minutes", "calendar_days_ahead"],
        config_schema=[
            {"key": "calendar_ics_url", "label": "Private ICS / iCal URL", "type": "text", "default": ""},
            {
                "key": "calendar_demo_mode",
                "label": "Demo Mode",
                "type": "select",
                "default": "on",
                "options": [
                    {"value": "on", "label": "On"},
                    {"value": "off", "label": "Off"},
                ],
            },
            {"key": "calendar_refresh_minutes", "label": "Refresh Minutes", "type": "number", "default": 30},
            {"key": "calendar_days_ahead", "label": "Days Ahead", "type": "number", "default": 2},
        ],
    ),
    "reminders": PluginDefinition(
        plugin_id="reminders",
        name="Reminders",
        kind="reminders",
        accent_color="#F6BD60",
        description="A compact local to-do board for a few key tasks, with no account setup required.",
        builtin=True,
        runtime_enabled=True,
        settings_keys=["reminders_title", "reminders_items", "reminders_demo_mode", "reminders_refresh_minutes"],
        config_schema=[
            {"key": "reminders_title", "label": "Board Title", "type": "text", "default": "Today's Focus"},
            {"key": "reminders_items", "label": "Reminder Lines", "type": "textarea", "default": ""},
            {
                "key": "reminders_demo_mode",
                "label": "Demo Mode",
                "type": "select",
                "default": "on",
                "options": [
                    {"value": "on", "label": "On"},
                    {"value": "off", "label": "Off"},
                ],
            },
            {"key": "reminders_refresh_minutes", "label": "Refresh Minutes", "type": "number"},
        ],
    ),
    "sports": PluginDefinition(
        plugin_id="sports",
        name="Sports Schedule",
        kind="sports",
        accent_color="#90BE6D",
        description="Schedule-only sports plugin designed for slow-refresh e-ink screens.",
        builtin=True,
        runtime_enabled=False,
        settings_keys=["sports_team", "sports_refresh_minutes"],
        config_schema=[
            {"key": "sports_team", "label": "Team", "type": "text"},
            {"key": "sports_refresh_minutes", "label": "Refresh Minutes", "type": "number"},
        ],
    ),
    "transit": PluginDefinition(
        plugin_id="transit",
        name="Transit Snapshot",
        kind="transit",
        accent_color="#43AA8B",
        description="Static route and commute summaries instead of live countdowns.",
        builtin=True,
        runtime_enabled=False,
        settings_keys=["transit_route", "transit_refresh_minutes"],
        config_schema=[
            {"key": "transit_route", "label": "Route", "type": "text"},
            {"key": "transit_refresh_minutes", "label": "Refresh Minutes", "type": "number"},
        ],
    ),
    "traffic": PluginDefinition(
        plugin_id="traffic",
        name="Traffic Snapshot",
        kind="traffic",
        accent_color="#F94144",
        description="Low-frequency traffic summaries for a typical route.",
        builtin=True,
        runtime_enabled=False,
        settings_keys=["traffic_route", "traffic_refresh_minutes"],
        config_schema=[
            {"key": "traffic_route", "label": "Route", "type": "text"},
            {"key": "traffic_refresh_minutes", "label": "Refresh Minutes", "type": "number"},
        ],
    ),
}

PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
PLUGIN_DIRS = plugin_search_dirs()


def _safe_manifest_value(value, default=""):
    return str(value).strip() if value is not None else default


def _safe_manifest_schema(schema):
    if not isinstance(schema, list):
        return None
    cleaned = []
    for field in schema[:20]:
        if not isinstance(field, dict):
            continue
        key = _safe_manifest_value(field.get("key"))[:48]
        label = _safe_manifest_value(field.get("label"), key)[:64]
        field_type = _safe_manifest_value(field.get("type"), "text").lower()
        if not key or field_type not in ("text", "number", "select", "textarea"):
            continue
        row = {"key": key, "label": label, "type": field_type}
        if "default" in field:
            row["default"] = field.get("default")
        if field_type == "select":
            options = field.get("options")
            if not isinstance(options, list):
                continue
            cleaned_options = []
            for option in options[:20]:
                if not isinstance(option, dict):
                    continue
                value = _safe_manifest_value(option.get("value"))[:64]
                option_label = _safe_manifest_value(option.get("label"), value)[:64]
                if not value:
                    continue
                cleaned_options.append({"value": value, "label": option_label})
            if not cleaned_options:
                continue
            row["options"] = cleaned_options
        cleaned.append(row)
    return cleaned or None


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
    config_schema = _safe_manifest_schema(manifest.get("config_schema"))
    if config_schema and not settings_keys:
        settings_keys = [str(field.get("key")) for field in config_schema if field.get("key")]

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
        config_schema=config_schema,
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


def get_runtime_plugins() -> Dict[str, PluginDefinition]:
    """Return plugins that are safe to run in the current daemon."""
    return {
        plugin_id: plugin
        for plugin_id, plugin in get_plugins().items()
        if plugin.runtime_enabled
    }


def get_plugin(plugin_id: str) -> Optional[PluginDefinition]:
    """Return a plugin definition by ID, or None if missing."""
    return get_plugins().get(plugin_id)


def get_plugin_payload() -> Dict[str, Dict[str, object]]:
    """Return plugin metadata in a JSON-friendly shape for the web UI."""
    return {plugin_id: plugin.to_dict() for plugin_id, plugin in get_plugins().items()}


def default_plugin_settings() -> Dict[str, Dict[str, object]]:
    """Return default per-plugin settings seeded from plugin config schema."""
    defaults: Dict[str, Dict[str, object]] = {}
    for plugin_id, plugin in get_plugins().items():
        schema = plugin.config_schema or []
        if not schema:
            continue
        plugin_defaults: Dict[str, object] = {}
        for field in schema:
            key = str(field.get("key") or "").strip()
            if not key:
                continue
            if "default" in field:
                plugin_defaults[key] = field.get("default")
            elif field.get("type") == "number":
                plugin_defaults[key] = 10
            else:
                plugin_defaults[key] = ""
        defaults[plugin_id] = plugin_defaults
    return defaults


def normalize_plugin_settings(plugin_settings) -> Dict[str, Dict[str, object]]:
    """Normalize stored per-plugin settings against the declared config schema."""
    normalized = default_plugin_settings()
    raw = plugin_settings if isinstance(plugin_settings, dict) else {}
    plugins = get_plugins()
    for plugin_id, plugin in plugins.items():
        schema = plugin.config_schema or []
        source = raw.get(plugin_id)
        if not isinstance(source, dict):
            continue
        bucket = normalized.setdefault(plugin_id, {})
        for field in schema:
            key = str(field.get("key") or "").strip()
            if not key or key not in source:
                continue
            value = source.get(key)
            field_type = str(field.get("type") or "text")
            if field_type == "number":
                try:
                    bucket[key] = max(1, min(1440, int(value)))
                except (TypeError, ValueError):
                    continue
            elif field_type == "textarea":
                bucket[key] = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()[:2000]
            else:
                bucket[key] = str(value).strip()[:240]
    return normalized


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


def get_runtime_plugin_ids() -> List[str]:
    """Return plugins that can be selected for the live display."""
    return list(get_runtime_plugins().keys())


def default_enabled_plugins(default_plugin: str = "pokemon") -> List[str]:
    """Return the default runnable plugin set for new configs."""
    runtime_plugins = get_runtime_plugins()
    preferred_ids = [plugin_id for plugin_id, plugin in runtime_plugins.items() if plugin.kind == "tcg"]
    runtime_ids = preferred_ids or list(runtime_plugins.keys())
    if default_plugin in runtime_ids:
        return [default_plugin] + [plugin_id for plugin_id in runtime_ids if plugin_id != default_plugin]
    return runtime_ids or ["pokemon"]


def normalize_enabled_plugins(enabled_plugins, default_plugin: str = "pokemon") -> List[str]:
    """Normalize enabled plugin IDs to runnable, unique plugins."""
    runtime_ids = get_runtime_plugin_ids()
    normalized: List[str] = []
    if isinstance(enabled_plugins, list):
        for raw_id in enabled_plugins[:32]:
            plugin_id = str(raw_id).strip()
            if plugin_id in runtime_ids and plugin_id not in normalized:
                normalized.append(plugin_id)
    if not normalized:
        return default_enabled_plugins(default_plugin)
    return normalized


def normalize_active_plugin(value: Optional[str], default: str = "pokemon", allowed_ids=None) -> str:
    """Normalize a requested active plugin ID to a runnable plugin."""
    allowed = allowed_ids if allowed_ids is not None else get_runtime_plugin_ids()
    if value in allowed:
        return str(value)
    if default in allowed:
        return default
    return allowed[0] if allowed else default


def default_display_schedule(default_plugin: str = "pokemon", enabled_plugins=None) -> List[Dict[str, object]]:
    """Return a simple all-day schedule seeded with one plugin."""
    allowed = normalize_enabled_plugins(enabled_plugins, default_plugin)
    plugin_id = normalize_active_plugin(default_plugin, default_plugin, allowed)
    return [{
        "plugin_ids": [plugin_id],
        "label": "All Day",
        "start_hour": 0,
        "end_hour": 24,
        "enabled": True,
        "rotation_minutes": 10,
    }]


def normalize_display_schedule(schedule, default_plugin: str = "pokemon", enabled_plugins=None) -> List[Dict[str, object]]:
    """Normalize saved schedule data into a safe, minimal structure."""
    enabled_ids = normalize_enabled_plugins(enabled_plugins, default_plugin)
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
                normalized_id = normalize_active_plugin(raw_id, default_plugin, enabled_ids)
                if normalized_id not in plugin_ids:
                    plugin_ids.append(normalized_id)
            if not plugin_ids:
                plugin_ids = [normalize_active_plugin(default_plugin, default_plugin, enabled_ids)]
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
        return default_display_schedule(default_plugin, enabled_ids)
    return normalized


def resolve_active_plugin(config: Dict[str, object], now_struct=None) -> str:
    """Resolve the currently active plugin from single/scheduled display config."""
    enabled_plugins = normalize_enabled_plugins(
        config.get("enabled_plugins"),
        config.get("single_plugin") or config.get("active_plugin") or config.get("active_tcg") or "pokemon",
    )
    single_plugin = normalize_active_plugin(
        config.get("single_plugin") or config.get("active_plugin") or config.get("active_tcg"),
        "pokemon",
        enabled_plugins,
    )
    mode = str(config.get("display_mode") or "single").strip().lower()
    if mode != "schedule":
        return single_plugin

    schedule = normalize_display_schedule(config.get("display_schedule"), single_plugin, enabled_plugins)
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
    enabled_plugins = normalize_enabled_plugins(
        normalized.get("enabled_plugins"),
        normalized.get("single_plugin") or normalized.get("active_plugin") or normalized.get("active_tcg") or "pokemon",
    )
    single_plugin = normalize_active_plugin(
        normalized.get("single_plugin") or normalized.get("active_plugin") or normalized.get("active_tcg"),
        "pokemon",
        enabled_plugins,
    )
    mode = str(normalized.get("display_mode") or "single").strip().lower()
    if mode not in ("single", "schedule"):
        mode = "single"
    schedule = normalize_display_schedule(normalized.get("display_schedule"), single_plugin, enabled_plugins)
    normalized["enabled_plugins"] = enabled_plugins
    normalized["single_plugin"] = single_plugin
    normalized["display_mode"] = mode
    normalized["display_schedule"] = schedule
    resolved_plugin = resolve_active_plugin(normalized)
    normalized["active_plugin"] = resolved_plugin
    normalized["active_tcg"] = resolved_plugin
    return normalized
