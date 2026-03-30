#!/usr/bin/python3
"""
Weather plugin helpers for InkSlab.

Uses Open-Meteo's geocoding and forecast APIs so the first ambient plugin can run
without forcing users to manage API keys.
"""

import json
import math
import os
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

from inkslab_paths import WEATHER_CACHE_FILE


def get_weather_settings(config):
    plugin_settings = config.get("plugin_settings") if isinstance(config, dict) else {}
    bucket = plugin_settings.get("weather") if isinstance(plugin_settings, dict) else {}
    bucket = bucket if isinstance(bucket, dict) else {}
    location_name = str(bucket.get("location_name") or "").strip()
    units = "metric" if str(bucket.get("weather_units") or "").strip().lower() == "metric" else "imperial"
    try:
        refresh_minutes = max(10, min(360, int(bucket.get("weather_refresh_minutes", 30))))
    except (TypeError, ValueError):
        refresh_minutes = 30
    return {
        "location_name": location_name,
        "weather_units": units,
        "weather_refresh_minutes": refresh_minutes,
    }


def weather_wait_seconds(config):
    return get_weather_settings(config)["weather_refresh_minutes"] * 60


def _read_json_url(url, timeout=15):
    request = urllib.request.Request(url, headers={"User-Agent": "InkSlab/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _load_cached_snapshot():
    if not os.path.exists(WEATHER_CACHE_FILE):
        return None
    try:
        with open(WEATHER_CACHE_FILE, "r") as handle:
            return json.load(handle)
    except Exception:
        return None


def _save_cached_snapshot(snapshot):
    try:
        with open(WEATHER_CACHE_FILE, "w") as handle:
            json.dump(snapshot, handle)
    except Exception:
        pass


def _weather_code_label(code):
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "Unknown"
    if code == 0:
        return "Clear"
    if code in (1, 2):
        return "Partly Cloudy"
    if code == 3:
        return "Cloudy"
    if code in (45, 48):
        return "Fog"
    if code in (51, 53, 55, 56, 57):
        return "Drizzle"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "Rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "Snow"
    if code in (95, 96, 99):
        return "Storm"
    return "Mixed"


def _format_location(result):
    pieces = [result.get("name", "")]
    admin1 = result.get("admin1")
    country = result.get("country")
    if admin1 and admin1 not in pieces:
        pieces.append(admin1)
    if country and country not in pieces:
        pieces.append(country)
    return ", ".join([piece for piece in pieces if piece])


def _format_temp(value):
    try:
        return str(int(round(float(value))))
    except (TypeError, ValueError):
        return "--"


def _format_clock(value):
    if not value:
        return "--"
    try:
        parsed = time.strptime(value, "%Y-%m-%dT%H:%M")
        return time.strftime("%-I:%M %p", parsed)
    except Exception:
        return value[-5:]


def fetch_weather_snapshot(config):
    settings = get_weather_settings(config)
    location_name = settings["location_name"]
    if not location_name:
        return {
            "ok": False,
            "reason": "Set a location in Setup > Plugins > Weather.",
            "location_label": "Weather needs a location",
            "updated_at": int(time.time()),
        }

    cached = _load_cached_snapshot()
    if (
        cached
        and cached.get("location_query") == location_name
        and cached.get("weather_units") == settings["weather_units"]
        and int(time.time()) - int(cached.get("updated_at", 0)) < settings["weather_refresh_minutes"] * 60
    ):
        return cached

    try:
        query = urllib.parse.urlencode({"name": location_name, "count": 1, "language": "en", "format": "json"})
        geo = _read_json_url("https://geocoding-api.open-meteo.com/v1/search?" + query)
        results = geo.get("results") if isinstance(geo, dict) else None
        if not results:
            snapshot = {
                "ok": False,
                "reason": "Location not found. Try a city name like Costa Mesa or Boston.",
                "location_label": location_name,
                "updated_at": int(time.time()),
            }
            _save_cached_snapshot(snapshot)
            return snapshot

        result = results[0]
        units = settings["weather_units"]
        forecast_query = urllib.parse.urlencode({
            "latitude": result["latitude"],
            "longitude": result["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day",
            "hourly": "temperature_2m,weather_code,precipitation_probability",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max,precipitation_probability_max",
            "forecast_days": 1,
            "temperature_unit": "fahrenheit" if units == "imperial" else "celsius",
            "wind_speed_unit": "mph" if units == "imperial" else "kmh",
            "timezone": "auto",
        })
        forecast = _read_json_url("https://api.open-meteo.com/v1/forecast?" + forecast_query)
        current = forecast.get("current", {}) if isinstance(forecast, dict) else {}
        daily = forecast.get("daily", {}) if isinstance(forecast, dict) else {}
        hourly = forecast.get("hourly", {}) if isinstance(forecast, dict) else {}
        next_periods = []
        hourly_times = hourly.get("time") or []
        hourly_temps = hourly.get("temperature_2m") or []
        hourly_codes = hourly.get("weather_code") or []
        hourly_precip = hourly.get("precipitation_probability") or []
        current_time = current.get("time")
        start_index = 0
        if current_time and current_time in hourly_times:
            start_index = hourly_times.index(current_time) + 1
        for offset in (0, 2, 5):
            idx = start_index + offset
            if idx >= len(hourly_times):
                continue
            next_periods.append({
                "time": _format_clock(hourly_times[idx]),
                "temperature": hourly_temps[idx] if idx < len(hourly_temps) else None,
                "weather_code": hourly_codes[idx] if idx < len(hourly_codes) else None,
                "condition": _weather_code_label(hourly_codes[idx] if idx < len(hourly_codes) else None),
                "precipitation_probability": hourly_precip[idx] if idx < len(hourly_precip) else None,
            })

        snapshot = {
            "ok": True,
            "location_query": location_name,
            "location_label": _format_location(result),
            "timezone": result.get("timezone") or forecast.get("timezone"),
            "weather_units": units,
            "temperature": current.get("temperature_2m"),
            "apparent_temperature": current.get("apparent_temperature"),
            "weather_code": current.get("weather_code"),
            "condition_label": _weather_code_label(current.get("weather_code")),
            "is_day": bool(current.get("is_day", 1)),
            "wind_speed": current.get("wind_speed_10m"),
            "high": (daily.get("temperature_2m_max") or [None])[0],
            "low": (daily.get("temperature_2m_min") or [None])[0],
            "today_code": (daily.get("weather_code") or [current.get("weather_code")])[0],
            "sunrise": (daily.get("sunrise") or [None])[0],
            "sunset": (daily.get("sunset") or [None])[0],
            "uv_index_max": (daily.get("uv_index_max") or [None])[0],
            "precipitation_probability_max": (daily.get("precipitation_probability_max") or [None])[0],
            "next_periods": next_periods,
            "updated_at": int(time.time()),
        }
    except Exception:
        if cached:
            cached["reason"] = "Showing cached weather because the latest refresh failed."
            return cached
        snapshot = {
            "ok": False,
            "reason": "Weather refresh failed. Check internet or try again later.",
            "location_label": location_name,
            "updated_at": int(time.time()),
        }
        _save_cached_snapshot(snapshot)
        return snapshot
    _save_cached_snapshot(snapshot)
    return snapshot


def _load_font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(path, size)
    except IOError:
        return ImageFont.load_default()


def _draw_sun(draw, center_x, center_y):
    draw.ellipse((center_x - 45, center_y - 45, center_x + 45, center_y + 45), fill=(255, 255, 0), outline=(255, 128, 0), width=4)
    for angle in range(0, 360, 45):
        radians = math.radians(angle)
        x1 = center_x + math.cos(radians) * 60
        y1 = center_y + math.sin(radians) * 60
        x2 = center_x + math.cos(radians) * 88
        y2 = center_y + math.sin(radians) * 88
        draw.line((x1, y1, x2, y2), fill=(255, 128, 0), width=5)


def _draw_cloud(draw, center_x, center_y, fill=(255, 255, 255), outline=(0, 0, 255)):
    draw.ellipse((center_x - 78, center_y - 28, center_x - 5, center_y + 34), fill=fill, outline=outline, width=4)
    draw.ellipse((center_x - 28, center_y - 50, center_x + 48, center_y + 28), fill=fill, outline=outline, width=4)
    draw.ellipse((center_x + 16, center_y - 26, center_x + 92, center_y + 34), fill=fill, outline=outline, width=4)
    draw.rounded_rectangle((center_x - 86, center_y, center_x + 92, center_y + 38), radius=18, fill=fill, outline=outline, width=4)


def _draw_moon(draw, center_x, center_y):
    draw.ellipse((center_x - 46, center_y - 46, center_x + 46, center_y + 46), fill=(255, 255, 255), outline=(0, 0, 255), width=4)
    draw.ellipse((center_x - 20, center_y - 52, center_x + 58, center_y + 28), fill=(255, 255, 255), outline=(255, 255, 255), width=0)
    draw.ellipse((center_x - 8, center_y - 40, center_x + 46, center_y + 16), fill=(0, 0, 255), outline=(0, 0, 255), width=0)


def _draw_weather_icon(draw, code, is_day):
    if code == 0 and is_day:
        _draw_sun(draw, 298, 144)
        return
    if code == 0 and not is_day:
        _draw_moon(draw, 298, 144)
        return
    if code in (1, 2):
        if is_day:
            _draw_sun(draw, 278, 126)
        else:
            _draw_moon(draw, 278, 126)
        _draw_cloud(draw, 302, 152)
        return
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        _draw_cloud(draw, 298, 144)
        for x in (252, 288, 324):
            draw.line((x, 198, x - 8, 232), fill=(0, 0, 255), width=5)
        return
    if code in (71, 73, 75, 77, 85, 86):
        _draw_cloud(draw, 298, 144)
        for x in (250, 286, 322):
            draw.text((x, 204), "*", fill=(0, 255, 0), font=_load_font(32, bold=True), anchor="mm")
        return
    _draw_cloud(draw, 298, 144)


def _wrap_text(draw, text, font, max_width):
    words = str(text or "").split()
    lines = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:3]


def _draw_metric_box(draw, left, top, width, height, title, value, accent=(0, 0, 255)):
    draw.rounded_rectangle((left, top, left + width, top + height), radius=16, outline=accent, width=3, fill=(252, 253, 240))
    draw.text((left + 14, top + 12), title, fill=(0, 0, 0), font=_load_font(15))
    draw.text((left + 14, top + 40), value, fill=(0, 0, 0), font=_load_font(22, bold=True))


def _draw_forecast_box(draw, left, top, width, height, period, units_suffix):
    draw.rounded_rectangle((left, top, left + width, top + height), radius=14, outline=(0, 0, 255), width=2, fill=(255, 255, 255))
    draw.text((left + 10, top + 10), period.get("time", "--"), fill=(0, 0, 0), font=_load_font(14, bold=True))
    draw.text((left + 10, top + 34), period.get("condition", "--"), fill=(0, 0, 255), font=_load_font(13))
    temp_label = _format_temp(period.get("temperature")) + " " + units_suffix
    draw.text((left + 10, top + 58), temp_label, fill=(0, 0, 0), font=_load_font(18, bold=True))


def render_weather_canvas(config):
    snapshot = fetch_weather_snapshot(config)
    canvas = Image.new("RGB", (400, 600), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    font_title = _load_font(20, bold=True)
    font_location = _load_font(24, bold=True)
    font_large = _load_font(90, bold=True)
    font_body = _load_font(22)
    font_body_bold = _load_font(22, bold=True)
    font_small = _load_font(15)

    draw.rectangle((0, 0, 400, 118), fill=(0, 0, 255))
    draw.text((22, 16), "Weather", fill=(255, 255, 255), font=font_title)
    location_lines = _wrap_text(draw, snapshot.get("location_label", "Weather"), font_location, 340)
    for idx, line in enumerate(location_lines):
        draw.text((22, 42 + (idx * 26)), line, fill=(255, 255, 255), font=font_location)

    if not snapshot.get("ok"):
        draw.rounded_rectangle((22, 120, 378, 520), radius=18, outline=(0, 0, 255), width=3, fill=(255, 255, 255))
        draw.text((200, 190), "Weather Setup", fill=(0, 0, 0), font=font_title, anchor="mm")
        reason_lines = _wrap_text(draw, snapshot.get("reason", "Weather is not ready yet."), font_body, 300)
        for idx, line in enumerate(reason_lines):
            draw.text((200, 258 + (idx * 28)), line, fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((200, 384), "Enable the Weather plugin and add a city name.", fill=(255, 128, 0), font=font_body_bold, anchor="mm")
        draw.text((200, 474), "Example: Costa Mesa, CA", fill=(0, 0, 255), font=font_small, anchor="mm")
        return canvas, snapshot

    _draw_weather_icon(draw, snapshot.get("weather_code"), snapshot.get("is_day"))

    units_suffix = "F" if snapshot.get("weather_units") == "imperial" else "C"
    wind_suffix = "mph" if snapshot.get("weather_units") == "imperial" else "km/h"
    temperature = _format_temp(snapshot.get("temperature"))
    apparent = _format_temp(snapshot.get("apparent_temperature"))
    high = _format_temp(snapshot.get("high"))
    low = _format_temp(snapshot.get("low"))
    wind_speed = _format_temp(snapshot.get("wind_speed"))

    draw.rectangle((0, 118, 400, 288), fill=(252, 253, 240))
    draw.text((22, 136), temperature, fill=(0, 0, 0), font=font_large)
    draw.text((162, 160), units_suffix, fill=(255, 128, 0), font=font_body_bold)
    draw.text((24, 234), snapshot.get("condition_label", "Weather"), fill=(0, 0, 255), font=font_body_bold)
    draw.text((24, 262), "Today " + high + " / " + low + " " + units_suffix, fill=(0, 0, 0), font=font_small)

    _draw_metric_box(draw, 20, 300, 172, 88, "Feels Like", apparent + " " + units_suffix)
    _draw_metric_box(draw, 208, 300, 172, 88, "Wind", wind_speed + " " + wind_suffix, accent=(255, 128, 0))
    _draw_metric_box(draw, 20, 398, 172, 88, "Sunrise", _format_clock(snapshot.get("sunrise")))
    _draw_metric_box(draw, 208, 398, 172, 88, "Sunset", _format_clock(snapshot.get("sunset")), accent=(0, 255, 0))

    precip_text = (_format_temp(snapshot.get("precipitation_probability_max")) + "%")
    uv = _format_temp(snapshot.get("uv_index_max"))
    draw.text((24, 512), "Rain Chance " + precip_text, fill=(0, 0, 0), font=font_small)
    draw.text((210, 512), "UV Max " + uv, fill=(0, 0, 0), font=font_small)

    next_periods = snapshot.get("next_periods") or []
    if next_periods:
        draw.text((24, 536), "Next Up", fill=(0, 0, 255), font=font_small)
        box_left = 20
        for period in next_periods[:3]:
            _draw_forecast_box(draw, box_left, 550, 114, 42, period, units_suffix)
            box_left += 122

    updated_struct = time.localtime(snapshot.get("updated_at", int(time.time())))
    draw.text((24, 580), "Updated " + time.strftime("%-I:%M %p", updated_struct), fill=(0, 0, 0), font=_load_font(13))
    draw.text((376, 580), "Open-Meteo", fill=(0, 0, 255), font=_load_font(13), anchor="ra")
    return canvas, snapshot
