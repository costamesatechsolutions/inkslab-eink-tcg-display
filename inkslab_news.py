#!/usr/bin/python3
"""
News plugin helpers for InkSlab.

Uses standard-library RSS parsing and a local cache so the slab can show a few
calm headlines without adding external dependencies.
"""

import email.utils
import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

from inkslab_paths import NEWS_CACHE_FILE


DEFAULT_NEWS_FEED = "https://feeds.npr.org/1001/rss.xml"
NEWS_FEED_PRESETS = {
    "npr_top": {
        "label": "NPR Top News",
        "url": "https://feeds.npr.org/1001/rss.xml",
    },
    "google_top": {
        "label": "Google News Top Stories",
        "url": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    },
}


def get_news_settings(config):
    plugin_settings = config.get("plugin_settings") if isinstance(config, dict) else {}
    bucket = plugin_settings.get("news") if isinstance(plugin_settings, dict) else {}
    bucket = bucket if isinstance(bucket, dict) else {}
    preset = str(bucket.get("news_feed_preset") or "npr_top").strip().lower()
    custom_feed_url = str(bucket.get("news_feed_url") or "").strip()
    preset_info = NEWS_FEED_PRESETS.get(preset) or NEWS_FEED_PRESETS["npr_top"]
    if preset == "custom":
        feed_url = custom_feed_url or DEFAULT_NEWS_FEED
        feed_label = "Custom RSS Feed" if custom_feed_url else NEWS_FEED_PRESETS["npr_top"]["label"]
    else:
        feed_url = preset_info["url"]
        feed_label = preset_info["label"]
    try:
        refresh_minutes = max(10, min(360, int(bucket.get("news_refresh_minutes", 30))))
    except (TypeError, ValueError):
        refresh_minutes = 30
    try:
        headline_count = max(2, min(5, int(bucket.get("news_headline_count", 4))))
    except (TypeError, ValueError):
        headline_count = 4
    return {
        "news_feed_preset": preset if preset in NEWS_FEED_PRESETS or preset == "custom" else "npr_top",
        "news_feed_url": feed_url[:240],
        "news_feed_label": feed_label[:80],
        "news_refresh_minutes": refresh_minutes,
        "news_headline_count": headline_count,
    }


def news_wait_seconds(config):
    return get_news_settings(config)["news_refresh_minutes"] * 60


def _load_font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(path, size)
    except IOError:
        return ImageFont.load_default()


def _read_json_cache():
    if not os.path.exists(NEWS_CACHE_FILE):
        return None
    try:
        with open(NEWS_CACHE_FILE, "r") as handle:
            return json.load(handle)
    except Exception:
        return None


def _write_json_cache(snapshot):
    try:
        with open(NEWS_CACHE_FILE, "w") as handle:
            json.dump(snapshot, handle)
    except Exception:
        pass


def _fetch_feed_xml(url, timeout=15):
    request = urllib.request.Request(url, headers={"User-Agent": "InkSlab/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _strip_tag(tag):
    if not tag:
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _child_text(node, names):
    lowered = set(names)
    for child in list(node):
        if _strip_tag(child.tag).lower() in lowered and child.text:
            return str(child.text).strip()
    return ""


def _parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)
    channel = None
    if _strip_tag(root.tag).lower() == "rss":
        for child in list(root):
            if _strip_tag(child.tag).lower() == "channel":
                channel = child
                break
    if channel is None and _strip_tag(root.tag).lower() == "feed":
        channel = root
    if channel is None:
        raise ValueError("Unsupported feed format")

    feed_title = _child_text(channel, {"title"}) or "News"
    entries = []
    item_tags = {"item"} if _strip_tag(channel.tag).lower() == "channel" else {"entry"}
    for child in list(channel):
        if _strip_tag(child.tag).lower() not in item_tags:
            continue
        title = _child_text(child, {"title"}) or "Untitled"
        summary = _child_text(child, {"description", "summary"})
        published = _child_text(child, {"pubdate", "published", "updated"})
        entries.append({
            "title": title[:180],
            "summary": summary[:260],
            "published": published[:80],
        })
        if len(entries) >= 5:
            break
    return feed_title[:80], entries


def _format_published_label(published_raw):
    if not published_raw:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(published_raw)
        return parsed.strftime("%-I:%M %p")
    except Exception:
        return published_raw[:24]


def fetch_news_snapshot(config):
    settings = get_news_settings(config)
    feed_url = settings["news_feed_url"]
    cached = _read_json_cache()
    if (
        cached
        and cached.get("feed_url") == feed_url
        and int(time.time()) - int(cached.get("updated_at", 0)) < settings["news_refresh_minutes"] * 60
    ):
        return cached

    try:
        feed_title, entries = _parse_feed(_fetch_feed_xml(feed_url))
        if not entries:
            snapshot = {
                "ok": False,
                "feed_url": feed_url,
                "feed_label": settings["news_feed_label"],
                "feed_title": "News",
                "reason": "This feed did not return any headlines.",
                "headlines": [],
                "updated_at": int(time.time()),
            }
            _write_json_cache(snapshot)
            return snapshot
        snapshot = {
            "ok": True,
            "feed_url": feed_url,
            "feed_label": settings["news_feed_label"],
            "feed_title": feed_title,
            "reason": "",
            "headlines": entries,
            "updated_at": int(time.time()),
        }
        _write_json_cache(snapshot)
        return snapshot
    except Exception:
        if cached:
            cached["reason"] = "Showing cached headlines because the latest refresh failed."
            return cached
        snapshot = {
            "ok": False,
            "feed_url": feed_url,
            "feed_label": settings["news_feed_label"],
            "feed_title": "News",
            "reason": "Could not load the news feed right now.",
            "headlines": [],
            "updated_at": int(time.time()),
        }
        _write_json_cache(snapshot)
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


def render_news_canvas(config):
    snapshot = fetch_news_snapshot(config)
    settings = get_news_settings(config)
    canvas = Image.new("RGB", (400, 600), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    font_title = _load_font(18, bold=True)
    font_source = _load_font(18, bold=True)
    font_headline = _load_font(20, bold=True)
    font_summary = _load_font(13)
    font_meta = _load_font(12)

    draw.rectangle((0, 0, 400, 86), fill=(0, 0, 255))
    draw.text((20, 14), "News", fill=(255, 255, 255), font=font_title)
    source_name = snapshot.get("feed_label") or snapshot.get("feed_title", "News")
    source_lines = _wrap_text(draw, source_name, font_source, 250, 2)
    for idx, line in enumerate(source_lines):
        draw.text((20, 38 + (idx * 18)), line, fill=(255, 255, 255), font=font_source)
    if snapshot.get("feed_title") and snapshot.get("feed_title") != source_name:
        draw.text((380, 16), snapshot.get("feed_title")[:24], fill=(255, 255, 255), font=font_meta, anchor="ra")

    if not snapshot.get("ok"):
        draw.rounded_rectangle((20, 122, 380, 520), radius=16, outline=(0, 0, 255), width=3, fill=(252, 253, 240))
        draw.text((200, 188), "News Setup", fill=(0, 0, 0), font=font_source, anchor="mm")
        for idx, line in enumerate(_wrap_text(draw, snapshot.get("reason", "News is not ready yet."), font_headline, 300, 4)):
            draw.text((200, 250 + (idx * 26)), line, fill=(0, 0, 0), font=font_headline, anchor="mm")
        draw.text((200, 438), "Set up the feed in Setup > Apps > News Headlines.", fill=(0, 0, 255), font=font_meta, anchor="mm")
        draw.text((200, 462), "Use a preset or paste a public RSS feed URL.", fill=(0, 0, 0), font=font_meta, anchor="mm")
        return canvas, snapshot

    headlines = (snapshot.get("headlines") or [])[:settings["news_headline_count"]]
    top = 98
    footer_top = 582
    block_height = max(92, int((footer_top - top) / max(1, len(headlines))))
    for idx, item in enumerate(headlines, start=1):
        if idx > 1:
            draw.line((20, top - 10, 380, top - 10), fill=(220, 226, 230), width=1)
        draw.text((20, top), str(idx), fill=(255, 128, 0), font=_load_font(24, bold=True))
        published = _format_published_label(item.get("published"))
        if published:
            draw.text((380, top + 4), published, fill=(0, 0, 255), font=font_meta, anchor="ra")
        headline_lines = _wrap_text(draw, item.get("title", ""), font_headline, 292, 3 if len(headlines) <= 3 else 2)
        y = top - 2
        for line in headline_lines:
            draw.text((52, y), line, fill=(0, 0, 0), font=font_headline)
            y += 23
        summary = item.get("summary", "")
        if summary:
            summary_lines = 3 if len(headlines) <= 3 else 2
            for line in _wrap_text(draw, summary, font_summary, 320, summary_lines):
                draw.text((52, y + 4), line, fill=(0, 0, 0), font=font_summary)
                y += 16
        top += block_height

    updated_struct = time.localtime(snapshot.get("updated_at", int(time.time())))
    draw.line((20, 582, 380, 582), fill=(220, 226, 230), width=1)
    draw.text((20, 586), "Updated " + time.strftime("%-I:%M %p", updated_struct), fill=(0, 0, 0), font=font_meta)
    draw.text((380, 586), "RSS", fill=(0, 0, 255), font=font_meta, anchor="ra")
    return canvas, snapshot
