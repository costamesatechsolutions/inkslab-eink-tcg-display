#!/usr/bin/python3
"""
Calendar plugin helpers for InkSlab.

Reads a private iCal / ICS feed URL and renders a compact agenda suitable for
slow-refresh e-ink displays.
"""

import json
import os
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from inkslab_paths import CALENDAR_CACHE_FILE


DEMO_EVENTS = [
    {
        "summary": "InkSlab feature review",
        "location": "Costa Mesa Studio",
        "all_day": False,
        "offset_days": 0,
        "start_time": "10:00 AM",
        "end_time": "10:45 AM",
    },
    {
        "summary": "Pack and test demo unit",
        "location": "Workbench",
        "all_day": False,
        "offset_days": 0,
        "start_time": "2:30 PM",
        "end_time": "3:15 PM",
    },
    {
        "summary": "Sketch modular app ideas",
        "location": "All Day",
        "all_day": True,
        "offset_days": 1,
        "start_time": "",
        "end_time": "",
    },
]


def get_calendar_settings(config):
    plugin_settings = config.get("plugin_settings") if isinstance(config, dict) else {}
    bucket = plugin_settings.get("calendar") if isinstance(plugin_settings, dict) else {}
    bucket = bucket if isinstance(bucket, dict) else {}
    ics_url = str(bucket.get("calendar_ics_url") or "").strip()
    demo_mode = str(bucket.get("calendar_demo_mode") or "on").strip().lower()
    try:
        refresh_minutes = max(10, min(360, int(bucket.get("calendar_refresh_minutes", 30))))
    except (TypeError, ValueError):
        refresh_minutes = 30
    try:
        days_ahead = max(1, min(7, int(bucket.get("calendar_days_ahead", 2))))
    except (TypeError, ValueError):
        days_ahead = 2
    return {
        "calendar_ics_url": ics_url[:240],
        "calendar_demo_mode": "off" if demo_mode == "off" else "on",
        "calendar_refresh_minutes": refresh_minutes,
        "calendar_days_ahead": days_ahead,
    }


def calendar_wait_seconds(config):
    return get_calendar_settings(config)["calendar_refresh_minutes"] * 60


def _load_font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(path, size)
    except IOError:
        return ImageFont.load_default()


def _read_cache():
    if not os.path.exists(CALENDAR_CACHE_FILE):
        return None
    try:
        with open(CALENDAR_CACHE_FILE, "r") as handle:
            return json.load(handle)
    except Exception:
        return None


def _write_cache(snapshot):
    try:
        with open(CALENDAR_CACHE_FILE, "w") as handle:
            json.dump(snapshot, handle)
    except Exception:
        pass


def _fetch_calendar(url, timeout=15):
    request = urllib.request.Request(url, headers={"User-Agent": "InkSlab/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _unfold_ics_lines(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded = []
    for line in lines:
        if not line:
            unfolded.append("")
            continue
        if line.startswith(" ") or line.startswith("\t"):
            if unfolded:
                unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _parse_ics_datetime(value, params, local_tz):
    if not value:
        return None, False
    if params.get("VALUE") == "DATE":
        try:
            parsed = datetime.strptime(value, "%Y%m%d").date()
            return parsed, True
        except Exception:
            return None, True
    try:
        if value.endswith("Z"):
            parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return parsed.astimezone(local_tz), False
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
        tzid = params.get("TZID")
        if tzid:
            try:
                parsed = parsed.replace(tzinfo=ZoneInfo(tzid))
            except Exception:
                parsed = parsed.replace(tzinfo=local_tz)
        else:
            parsed = parsed.replace(tzinfo=local_tz)
        return parsed.astimezone(local_tz), False
    except Exception:
        return None, False


def _parse_property(line):
    if ":" not in line:
        return None, {}, ""
    name_part, value = line.split(":", 1)
    pieces = name_part.split(";")
    name = pieces[0].upper()
    params = {}
    for piece in pieces[1:]:
        if "=" in piece:
            key, param_value = piece.split("=", 1)
            params[key.upper()] = param_value
    return name, params, value.strip()


def _calendar_timezone(config):
    tz_name = config.get("timezone_name") if isinstance(config, dict) else None
    if tz_name:
        try:
            return ZoneInfo(str(tz_name))
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def _parse_ics_events(text, config):
    local_tz = _calendar_timezone(config)
    today = datetime.now(local_tz).date()
    settings = get_calendar_settings(config)
    end_day = today + timedelta(days=settings["calendar_days_ahead"] - 1)

    events = []
    current = None
    for line in _unfold_ics_lines(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current:
                start_raw = current.get("DTSTART")
                start_params = current.get("DTSTART_PARAMS", {})
                end_raw = current.get("DTEND")
                end_params = current.get("DTEND_PARAMS", {})
                start_value, all_day = _parse_ics_datetime(start_raw, start_params, local_tz)
                end_value, _ = _parse_ics_datetime(end_raw, end_params, local_tz)
                if start_value is None:
                    current = None
                    continue
                event_day = start_value if all_day else start_value.date()
                if event_day < today or event_day > end_day:
                    current = None
                    continue
                events.append({
                    "summary": current.get("SUMMARY", "Untitled Event")[:120],
                    "location": current.get("LOCATION", "")[:120],
                    "all_day": all_day,
                    "start_day": event_day.isoformat(),
                    "start_time": "" if all_day else start_value.strftime("%-I:%M %p"),
                    "end_time": "" if all_day or end_value is None or not hasattr(end_value, "strftime") else end_value.strftime("%-I:%M %p"),
                })
            current = None
            continue
        if current is None:
            continue
        name, params, value = _parse_property(line)
        if not name:
            continue
        if name in ("DTSTART", "DTEND"):
            current[name] = value
            current[name + "_PARAMS"] = params
        elif name in ("SUMMARY", "LOCATION"):
            current[name] = value

    events.sort(key=lambda item: (item.get("start_day", ""), item.get("start_time", ""), item.get("summary", "")))
    return events


def fetch_calendar_snapshot(config):
    settings = get_calendar_settings(config)
    ics_url = settings["calendar_ics_url"]
    if not ics_url and settings["calendar_demo_mode"] == "on":
        today = datetime.now(_calendar_timezone(config)).date()
        events = []
        for item in DEMO_EVENTS:
            event_day = today + timedelta(days=item["offset_days"])
            events.append({
                "summary": item["summary"],
                "location": item["location"],
                "all_day": item["all_day"],
                "start_day": event_day.isoformat(),
                "start_time": item["start_time"],
                "end_time": item["end_time"],
            })
        return {
            "ok": True,
            "calendar_ics_url": "",
            "calendar_label": "Demo Agenda",
            "reason": "Demo events are showing until you add a private ICS feed.",
            "events": events,
            "updated_at": int(time.time()),
        }
    if not ics_url:
        return {
            "ok": False,
            "calendar_label": "Calendar Agenda",
            "reason": "Add a private ICS / iCal feed URL in Setup > Apps > Calendar Agenda, or turn demo mode back on.",
            "events": [],
            "updated_at": int(time.time()),
        }

    cached = _read_cache()
    if (
        cached
        and cached.get("calendar_ics_url") == ics_url
        and int(time.time()) - int(cached.get("updated_at", 0)) < settings["calendar_refresh_minutes"] * 60
    ):
        return cached

    try:
        events = _parse_ics_events(_fetch_calendar(ics_url), config)
        snapshot = {
            "ok": True,
            "calendar_ics_url": ics_url,
            "calendar_label": "Calendar Agenda",
            "reason": "",
            "events": events[:5],
            "updated_at": int(time.time()),
        }
        _write_cache(snapshot)
        return snapshot
    except Exception:
        if cached:
            cached["reason"] = "Showing cached calendar items because the latest refresh failed."
            return cached
        snapshot = {
            "ok": False,
            "calendar_ics_url": ics_url,
            "calendar_label": "Calendar Agenda",
            "reason": "Could not load the calendar feed right now.",
            "events": [],
            "updated_at": int(time.time()),
        }
        _write_cache(snapshot)
        return snapshot


def _wrap_text(draw, text, font, max_width, max_lines):
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
            if len(lines) >= max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if words and len(lines) == max_lines and " ".join(lines).strip() != " ".join(words).strip():
        last = lines[-1]
        if len(last) > 3:
            lines[-1] = last[:-1] + "…"
    return lines


def _day_label(iso_day):
    try:
        parsed = date.fromisoformat(iso_day)
    except Exception:
        return ""
    today = datetime.now().astimezone().date()
    if parsed == today:
        return "Today"
    if parsed == today + timedelta(days=1):
        return "Tomorrow"
    return parsed.strftime("%a")


def render_calendar_canvas(config):
    snapshot = fetch_calendar_snapshot(config)
    canvas = Image.new("RGB", (400, 600), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    font_title = _load_font(18, bold=True)
    font_header = _load_font(20, bold=True)
    font_event = _load_font(18, bold=True)
    font_time = _load_font(14, bold=True)
    font_body = _load_font(13)
    font_meta = _load_font(12)

    draw.rectangle((0, 0, 400, 90), fill=(0, 0, 255))
    draw.text((20, 14), "Calendar", fill=(255, 255, 255), font=font_title)
    draw.text((20, 40), "Agenda", fill=(255, 255, 255), font=font_header)

    if not snapshot.get("ok"):
        draw.rounded_rectangle((20, 122, 380, 520), radius=16, outline=(0, 0, 255), width=3, fill=(252, 253, 240))
        draw.text((200, 186), "Calendar Setup", fill=(0, 0, 0), font=font_header, anchor="mm")
        for idx, line in enumerate(_wrap_text(draw, snapshot.get("reason", "Calendar is not ready yet."), font_event, 300, 4)):
            draw.text((200, 250 + (idx * 24)), line, fill=(0, 0, 0), font=font_event, anchor="mm")
        draw.text((200, 432), "Use a private Google, Apple, or other iCal / ICS URL.", fill=(0, 0, 255), font=font_meta, anchor="mm")
        draw.text((200, 456), "Or turn demo mode on to preview the layout.", fill=(0, 0, 0), font=font_meta, anchor="mm")
        return canvas, snapshot

    events = snapshot.get("events") or []
    if not events:
        draw.rounded_rectangle((20, 122, 380, 520), radius=16, outline=(0, 0, 255), width=3, fill=(252, 253, 240))
        draw.text((200, 188), "No Upcoming Events", fill=(0, 0, 0), font=font_header, anchor="mm")
        draw.text((200, 226), "Nothing is scheduled in the selected window.", fill=(0, 0, 0), font=font_body, anchor="mm")
    else:
        top = 104
        block_height = max(86, int((582 - top) / max(1, len(events))))
        for item in events[:5]:
            draw.line((20, top - 8, 380, top - 8), fill=(220, 226, 230), width=1)
            draw.text((20, top), _day_label(item.get("start_day")), fill=(255, 128, 0), font=_load_font(16, bold=True))
            time_label = "All Day" if item.get("all_day") else item.get("start_time", "")
            if item.get("end_time"):
                time_label += " - " + item["end_time"]
            draw.text((380, top + 1), time_label, fill=(0, 0, 255), font=font_time, anchor="ra")
            y = top + 20
            for line in _wrap_text(draw, item.get("summary", ""), font_event, 332, 2):
                draw.text((20, y), line, fill=(0, 0, 0), font=font_event)
                y += 22
            location = item.get("location", "")
            if location:
                for line in _wrap_text(draw, location, font_body, 340, 2):
                    draw.text((20, y + 2), line, fill=(0, 0, 0), font=font_body)
                    y += 16
            top += block_height

    updated_struct = time.localtime(snapshot.get("updated_at", int(time.time())))
    draw.line((20, 582, 380, 582), fill=(220, 226, 230), width=1)
    draw.text((20, 586), "Updated " + time.strftime("%-I:%M %p", updated_struct), fill=(0, 0, 0), font=font_meta)
    draw.text((380, 586), "ICS", fill=(0, 0, 255), font=font_meta, anchor="ra")
    return canvas, snapshot
