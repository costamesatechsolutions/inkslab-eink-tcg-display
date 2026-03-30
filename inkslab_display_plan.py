#!/usr/bin/python3
"""
Helpers for turning advanced display scheduling into simpler product behavior.
"""

import time


def build_simple_display_plan(style, single_plugin, enabled_plugins, rotation_minutes):
    """Build a simple display plan for common, low-friction use cases."""
    plugin_ids = []
    for plugin_id in enabled_plugins or []:
        plugin_id = str(plugin_id).strip()
        if plugin_id and plugin_id not in plugin_ids:
            plugin_ids.append(plugin_id)

    single = str(single_plugin or "").strip() or (plugin_ids[0] if plugin_ids else "pokemon")
    if single not in plugin_ids:
        plugin_ids.insert(0, single)

    try:
        rotation = max(1, min(1440, int(rotation_minutes)))
    except (TypeError, ValueError):
        rotation = 10

    if style == "rotate_all_day" and plugin_ids:
        return {
            "display_mode": "schedule",
            "single_plugin": single,
            "display_schedule": [{
                "label": "All Day",
                "start_hour": 0,
                "end_hour": 24,
                "enabled": True,
                "rotation_minutes": rotation,
                "plugin_ids": plugin_ids,
            }],
        }

    return {
        "display_mode": "single",
        "single_plugin": single,
        "display_schedule": [{
            "label": "All Day",
            "start_hour": 0,
            "end_hour": 24,
            "enabled": True,
            "rotation_minutes": rotation,
            "plugin_ids": [single],
        }],
    }


def infer_display_style(display_mode, display_schedule, enabled_plugins):
    """Infer the friendliest simple-mode label for the current saved plan."""
    if str(display_mode or "single").strip().lower() != "schedule":
        return "single"

    schedule = display_schedule if isinstance(display_schedule, list) else []
    if len(schedule) != 1:
        return "custom"

    block = schedule[0] if isinstance(schedule[0], dict) else {}
    plugin_ids = [str(plugin_id).strip() for plugin_id in (block.get("plugin_ids") or []) if str(plugin_id).strip()]
    enabled = [str(plugin_id).strip() for plugin_id in (enabled_plugins or []) if str(plugin_id).strip()]

    if (
        block.get("enabled", True)
        and int(block.get("start_hour", 0)) == 0
        and int(block.get("end_hour", 24)) == 24
        and plugin_ids
        and plugin_ids == enabled
    ):
        return "rotate_all_day"
    return "custom"


def infer_rotation_minutes(display_schedule, fallback=10):
    """Infer a friendly rotation cadence from the first schedule block."""
    schedule = display_schedule if isinstance(display_schedule, list) else []
    if not schedule:
        return fallback
    block = schedule[0] if isinstance(schedule[0], dict) else {}
    try:
        return max(1, min(1440, int(block.get("rotation_minutes", fallback))))
    except (TypeError, ValueError):
        return fallback


def resolve_display_wait(display_mode, display_schedule, default_wait, now_struct=None):
    """Choose a wake-up time that respects schedule boundaries and rotation slots."""
    try:
        wait_seconds = max(60, int(default_wait))
    except (TypeError, ValueError):
        wait_seconds = 600

    if str(display_mode or "single").strip().lower() != "schedule":
        return wait_seconds

    schedule = display_schedule if isinstance(display_schedule, list) else []
    if not schedule:
        return wait_seconds

    now_struct = now_struct or time.localtime()
    now_minutes = now_struct.tm_hour * 60 + now_struct.tm_min
    now_seconds = int(now_struct.tm_sec)

    for block in schedule:
        if not isinstance(block, dict) or not block.get("enabled", True):
            continue
        try:
            start_hour = max(0, min(23, int(block.get("start_hour", 0))))
            end_hour = max(1, min(24, int(block.get("end_hour", 24))))
        except (TypeError, ValueError):
            continue
        if not (start_hour <= now_struct.tm_hour < end_hour):
            continue

        seconds_to_boundary = ((end_hour * 60) - now_minutes) * 60 - now_seconds
        if seconds_to_boundary > 0:
            wait_seconds = min(wait_seconds, seconds_to_boundary)

        plugin_ids = block.get("plugin_ids") or []
        if len(plugin_ids) > 1:
            try:
                rotation_seconds = max(60, min(86400, int(block.get("rotation_minutes", 10)) * 60))
            except (TypeError, ValueError):
                rotation_seconds = 600
            wait_seconds = min(wait_seconds, rotation_seconds)
        break

    return max(60, wait_seconds)
