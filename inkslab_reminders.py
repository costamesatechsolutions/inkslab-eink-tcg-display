#!/usr/bin/python3
"""
Local reminders plugin helpers for InkSlab.

This keeps the first reminders app simple: no accounts, no API keys, and no
network dependency. Users can paste a few tasks and let InkSlab rotate them.
"""

import time

from PIL import Image, ImageDraw, ImageFont


DEMO_REMINDERS = [
    "Pack one ready-to-ship InkSlab unit",
    "Test weather, news, and market apps on the bench slab",
    "Reply to customer messages before 4 PM",
    "Rotate card photos for the product page",
]


def get_reminders_settings(config):
    plugin_settings = config.get("plugin_settings") if isinstance(config, dict) else {}
    bucket = plugin_settings.get("reminders") if isinstance(plugin_settings, dict) else {}
    bucket = bucket if isinstance(bucket, dict) else {}
    title = str(bucket.get("reminders_title") or "Today's Focus").strip()[:48]
    raw_items = str(bucket.get("reminders_items") or "").strip()
    demo_mode = str(bucket.get("reminders_demo_mode") or "on").strip().lower()
    try:
        refresh_minutes = max(10, min(360, int(bucket.get("reminders_refresh_minutes", 30))))
    except (TypeError, ValueError):
        refresh_minutes = 30
    items = [line.strip(" -\t") for line in raw_items.splitlines() if line.strip()]
    return {
        "reminders_title": title or "Today's Focus",
        "reminders_items": items[:8],
        "reminders_demo_mode": "off" if demo_mode == "off" else "on",
        "reminders_refresh_minutes": refresh_minutes,
    }


def reminders_wait_seconds(config):
    return get_reminders_settings(config)["reminders_refresh_minutes"] * 60


def _load_font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(path, size)
    except IOError:
        return ImageFont.load_default()


def fetch_reminders_snapshot(config):
    settings = get_reminders_settings(config)
    items = list(settings["reminders_items"])
    if not items and settings["reminders_demo_mode"] == "on":
        items = list(DEMO_REMINDERS)
        reason = "Demo reminders are showing until you add your own list."
        label = "Demo Reminders"
    elif not items:
        return {
            "ok": False,
            "title": settings["reminders_title"],
            "items": [],
            "reason": "Add a few reminder lines in Setup > Apps > Reminders, or turn demo mode back on.",
            "updated_at": int(time.time()),
        }
    else:
        reason = "Local reminders update instantly and do not need any account setup."
        label = settings["reminders_title"]

    return {
        "ok": True,
        "title": label,
        "items": items[:6],
        "reason": reason,
        "updated_at": int(time.time()),
    }


def _wrap_text(draw, text, font, width, max_lines):
    words = str(text or "").split()
    if not words:
        return [""]
    lines = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        if draw.textbbox((0, 0), trial, font=font)[2] <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
            if len(lines) >= max_lines - 1:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and words:
        remaining = " ".join(words)
        if remaining and lines[-1] != remaining and not lines[-1].endswith("..."):
            if len(lines[-1]) > 3:
                lines[-1] = lines[-1][:-3].rstrip() + "..."
    return lines


def render_reminders_canvas(config):
    snapshot = fetch_reminders_snapshot(config)
    canvas = Image.new("RGB", (400, 600), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    font_title = _load_font(18, bold=True)
    font_header = _load_font(18, bold=True)
    font_list = _load_font(16, bold=True)
    font_body = _load_font(13)
    font_meta = _load_font(12)

    draw.rectangle((0, 0, 400, 84), fill=(0, 0, 255))
    draw.text((20, 14), "Reminders", fill=(255, 255, 255), font=font_title)
    draw.text((20, 40), str(snapshot.get("title") or "Today's Focus")[:28], fill=(255, 255, 255), font=font_header)

    items = snapshot.get("items") or []
    top = 108
    if items:
        row_height = 72 if len(items) <= 4 else 62
        for idx, item in enumerate(items[:6], start=1):
            draw.rounded_rectangle((20, top, 380, top + row_height - 10), radius=14, outline=(0, 0, 255), width=2, fill=(252, 253, 240))
            draw.text((34, top + 14), str(idx), fill=(255, 140, 0), font=font_list)
            lines = _wrap_text(draw, item, font_list, 300, 2)
            for line_idx, line in enumerate(lines):
                draw.text((70, top + 10 + (line_idx * 20)), line, fill=(0, 0, 0), font=font_list)
            top += row_height
    else:
        draw.rounded_rectangle((20, 124, 380, 300), radius=18, outline=(0, 0, 255), width=2, fill=(252, 253, 240))
        empty_lines = _wrap_text(draw, snapshot.get("reason", ""), font_list, 300, 5)
        for idx, line in enumerate(empty_lines):
            draw.text((38, 154 + (idx * 24)), line, fill=(0, 0, 0), font=font_list)

    reason_lines = _wrap_text(draw, snapshot.get("reason", ""), font_body, 360, 3)
    for idx, line in enumerate(reason_lines):
        draw.text((20, 520 + (idx * 16)), line, fill=(0, 0, 0), font=font_body)

    updated_struct = time.localtime(snapshot.get("updated_at", int(time.time())))
    draw.line((20, 582, 380, 582), fill=(220, 226, 230), width=1)
    draw.text((20, 586), "Updated " + time.strftime("%-I:%M %p", updated_struct), fill=(0, 0, 0), font=font_meta)
    draw.text((380, 586), "Local", fill=(0, 0, 255), font=font_meta, anchor="ra")
    return canvas, snapshot
