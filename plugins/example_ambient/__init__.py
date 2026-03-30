"""
Working example community plugin for InkSlab.
"""

import time

from PIL import ImageDraw, ImageFont


def _load_font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(path, size)
    except IOError:
        return ImageFont.load_default()


def render(settings, context):
    canvas = context["new_canvas"]((255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    font_title = _load_font(18, bold=True)
    font_body = _load_font(15)
    font_meta = _load_font(12)

    title = str(settings.get("example_title") or "My Ambient Plugin")[:32]
    mode = str(settings.get("example_mode") or "simple").strip().lower()
    notes = [line.strip() for line in str(settings.get("example_notes") or "").splitlines() if line.strip()]
    if not notes:
        notes = [
            "This plugin lives in the local plugins folder.",
            "Its settings come from manifest.json.",
            "Its render() function is running through the new plugin contract.",
        ]

    draw.rectangle((0, 0, 400, 84), fill=(0, 0, 255))
    draw.text((20, 16), "Example Plugin", fill=(255, 255, 255), font=font_title)
    draw.text((20, 44), title, fill=(255, 255, 255), font=font_title)

    top = 116
    for idx, line in enumerate(notes[:4], start=1):
        draw.rounded_rectangle((20, top, 380, top + 76), radius=16, outline=(0, 0, 255), width=2, fill=(252, 253, 240))
        draw.text((34, top + 14), str(idx), fill=(255, 140, 0), font=font_title)
        draw.text((72, top + 18), line[:34], fill=(0, 0, 0), font=font_body)
        top += 88

    draw.text((20, 560), "Rendered by a local community plugin", fill=(0, 0, 0), font=font_body)
    draw.text((380, 586), time.strftime("%-I:%M %p"), fill=(0, 0, 255), font=font_meta, anchor="ra")
    return canvas, {
        "name": "Example Ambient",
        "set_info": title,
        "card_num": "Mode: " + mode[:12],
        "rarity": "Local plugin render(settings, context)",
        "wait_seconds": 15 * 60,
    }
