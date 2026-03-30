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


def get_news_settings(config):
    plugin_settings = config.get("plugin_settings") if isinstance(config, dict) else {}
    bucket = plugin_settings.get("news") if isinstance(plugin_settings, dict) else {}
    bucket = bucket if isinstance(bucket, dict) else {}
    feed_url = str(bucket.get("news_feed_url") or DEFAULT_NEWS_FEED).strip() or DEFAULT_NEWS_FEED
    try:
        refresh_minutes = max(10, min(360, int(bucket.get("news_refresh_minutes", 30))))
    except (TypeError, ValueError):
        refresh_minutes = 30
    return {
        "news_feed_url": feed_url[:240],
        "news_refresh_minutes": refresh_minutes,
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
    canvas = Image.new("RGB", (400, 600), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    font_title = _load_font(18, bold=True)
    font_source = _load_font(22, bold=True)
    font_headline = _load_font(20, bold=True)
    font_summary = _load_font(13)
    font_meta = _load_font(12)

    draw.rectangle((0, 0, 400, 96), fill=(0, 0, 255))
    draw.text((20, 14), "News", fill=(255, 255, 255), font=font_title)
    for idx, line in enumerate(_wrap_text(draw, snapshot.get("feed_title", "News"), font_source, 360, 2)):
        draw.text((20, 40 + (idx * 22)), line, fill=(255, 255, 255), font=font_source)

    if not snapshot.get("ok"):
        draw.rounded_rectangle((20, 122, 380, 520), radius=16, outline=(0, 0, 255), width=3, fill=(252, 253, 240))
        draw.text((200, 188), "News Setup", fill=(0, 0, 0), font=font_source, anchor="mm")
        for idx, line in enumerate(_wrap_text(draw, snapshot.get("reason", "News is not ready yet."), font_headline, 300, 4)):
            draw.text((200, 250 + (idx * 26)), line, fill=(0, 0, 0), font=font_headline, anchor="mm")
        draw.text((200, 454), "Set up the feed in Setup > Apps > News Headlines.", fill=(0, 0, 255), font=font_meta, anchor="mm")
        return canvas, snapshot

    top = 114
    for idx, item in enumerate((snapshot.get("headlines") or [])[:3], start=1):
        if idx > 1:
            draw.line((20, top - 10, 380, top - 10), fill=(220, 226, 230), width=1)
        draw.text((20, top), str(idx), fill=(255, 128, 0), font=_load_font(22, bold=True))
        headline_lines = _wrap_text(draw, item.get("title", ""), font_headline, 322, 2)
        y = top - 2
        for line in headline_lines:
            draw.text((52, y), line, fill=(0, 0, 0), font=font_headline)
            y += 23
        published = _format_published_label(item.get("published"))
        if published:
            draw.text((380, top + 2), published, fill=(0, 0, 255), font=font_meta, anchor="ra")
        summary = item.get("summary", "")
        if summary:
            for line in _wrap_text(draw, summary, font_summary, 328, 2):
                draw.text((52, y + 4), line, fill=(0, 0, 0), font=font_summary)
                y += 16
        top = y + 22

    updated_struct = time.localtime(snapshot.get("updated_at", int(time.time())))
    draw.line((20, 582, 380, 582), fill=(220, 226, 230), width=1)
    draw.text((20, 586), "Updated " + time.strftime("%-I:%M %p", updated_struct), fill=(0, 0, 0), font=font_meta)
    draw.text((380, 586), "RSS", fill=(0, 0, 255), font=font_meta, anchor="ra")
    return canvas, snapshot
