#!/usr/bin/python3
"""
Runtime rendering helpers for built-in non-card plugins.
"""

from inkslab_calendar import calendar_wait_seconds, render_calendar_canvas
from inkslab_news import news_wait_seconds, render_news_canvas
from inkslab_weather import render_weather_canvas, weather_wait_seconds


def _format_weather_status(snapshot):
    if not isinstance(snapshot, dict) or not snapshot.get("ok"):
        return ""
    units_suffix = "F" if snapshot.get("weather_units") == "imperial" else "C"
    temp = snapshot.get("temperature")
    try:
        return f"{int(round(float(temp)))} {units_suffix}"
    except (TypeError, ValueError):
        return ""


def _format_news_status(snapshot):
    if not isinstance(snapshot, dict) or not snapshot.get("ok"):
        return ""
    headlines = snapshot.get("headlines") or []
    count = len(headlines)
    return f"{count} headlines" if count else ""


def _format_calendar_status(snapshot):
    if not isinstance(snapshot, dict) or not snapshot.get("ok"):
        return ""
    events = snapshot.get("events") or []
    count = len(events)
    return f"{count} events" if count else ""


def render_runtime_plugin(plugin_id, config):
    """
    Render a built-in non-card plugin and return its canvas plus UI metadata.

    Returns (canvas, payload) or (None, None) if the plugin is not handled here.
    """
    if plugin_id == "weather":
        plugin_canvas, plugin_snapshot = render_weather_canvas(config)
        return plugin_canvas, {
            "name": "Weather",
            "wait_seconds": weather_wait_seconds(config),
            "set_info": plugin_snapshot.get("location_label", ""),
            "card_num": _format_weather_status(plugin_snapshot),
            "rarity": plugin_snapshot.get("condition_label", plugin_snapshot.get("reason", "")),
            "error": plugin_snapshot.get("reason", "Weather is temporarily unavailable."),
        }
    if plugin_id == "news":
        plugin_canvas, plugin_snapshot = render_news_canvas(config)
        return plugin_canvas, {
            "name": "News",
            "wait_seconds": news_wait_seconds(config),
            "set_info": plugin_snapshot.get("feed_label") or plugin_snapshot.get("feed_title", ""),
            "card_num": _format_news_status(plugin_snapshot),
            "rarity": plugin_snapshot.get("reason") or "Top headlines",
            "error": plugin_snapshot.get("reason", "News is temporarily unavailable."),
        }
    if plugin_id == "calendar":
        plugin_canvas, plugin_snapshot = render_calendar_canvas(config)
        return plugin_canvas, {
            "name": "Calendar",
            "wait_seconds": calendar_wait_seconds(config),
            "set_info": plugin_snapshot.get("calendar_label", "Calendar Agenda"),
            "card_num": _format_calendar_status(plugin_snapshot),
            "rarity": plugin_snapshot.get("reason") or "Upcoming events",
            "error": plugin_snapshot.get("reason", "Calendar is temporarily unavailable."),
        }
    return None, None
