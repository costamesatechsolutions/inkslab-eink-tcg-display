#!/usr/bin/python3
"""
Market Snapshot helpers for InkSlab.

Demo mode works without setup. Live quotes use Yahoo Finance's public quote
endpoint so OTA installs do not depend on extra Python packages.
"""

import json
import os
import time
from urllib.parse import quote

from PIL import Image, ImageDraw, ImageFont
import requests

from inkslab_paths import MARKET_CACHE_FILE


DEMO_QUOTES = {
    "SPY": {"price": 521.34, "change_pct": 0.42, "label": "S&P 500 ETF"},
    "QQQ": {"price": 447.18, "change_pct": 0.76, "label": "Nasdaq 100 ETF"},
    "BTC-USD": {"price": 84210.00, "change_pct": -1.14, "label": "Bitcoin"},
    "ETH-USD": {"price": 4688.45, "change_pct": 1.02, "label": "Ethereum"},
}


def get_market_settings(config):
    plugin_settings = config.get("plugin_settings") if isinstance(config, dict) else {}
    bucket = plugin_settings.get("market") if isinstance(plugin_settings, dict) else {}
    bucket = bucket if isinstance(bucket, dict) else {}
    demo_mode = str(bucket.get("market_demo_mode") or "on").strip().lower()
    symbols_raw = str(bucket.get("market_symbols") or "SPY,QQQ,BTC-USD").strip()
    symbols = [symbol.strip().upper() for symbol in symbols_raw.split(",") if symbol.strip()][:4]
    if not symbols:
        symbols = ["SPY", "QQQ", "BTC-USD"]
    try:
        refresh_minutes = max(10, min(360, int(bucket.get("market_refresh_minutes", 30))))
    except (TypeError, ValueError):
        refresh_minutes = 30
    return {
        "market_demo_mode": "off" if demo_mode == "off" else "on",
        "market_symbols": symbols,
        "market_refresh_minutes": refresh_minutes,
    }


def market_wait_seconds(config):
    return get_market_settings(config)["market_refresh_minutes"] * 60


def _load_font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(path, size)
    except IOError:
        return ImageFont.load_default()


def _read_cache():
    if not os.path.exists(MARKET_CACHE_FILE):
        return None
    try:
        with open(MARKET_CACHE_FILE, "r") as handle:
            return json.load(handle)
    except Exception:
        return None


def _write_cache(snapshot):
    try:
        with open(MARKET_CACHE_FILE, "w") as handle:
            json.dump(snapshot, handle)
    except Exception:
        pass


def _format_price(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "--"
    if value >= 1000:
        return f"{value:,.0f}"
    if value >= 100:
        return f"{value:,.2f}"
    return f"{value:,.2f}"


def _format_change(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "--"
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:.2f}%"


def _change_color(value):
    try:
        return (0, 0, 255) if float(value) >= 0 else (255, 0, 0)
    except (TypeError, ValueError):
        return (0, 0, 0)


def _demo_snapshot(settings):
    quotes = []
    for symbol in settings["market_symbols"]:
        data = DEMO_QUOTES.get(symbol) or {"price": 100.0, "change_pct": 0.0, "label": symbol}
        quotes.append({
            "symbol": symbol,
            "label": data["label"],
            "price": data["price"],
            "change_pct": data["change_pct"],
        })
    return {
        "ok": True,
        "mode": "demo",
        "provider": "Sample Data",
        "quotes": quotes,
        "reason": "Demo values are showing. Turn demo mode off to use live Yahoo Finance quotes.",
        "updated_at": int(time.time()),
    }


def _live_snapshot(settings):
    symbols = settings["market_symbols"]
    if not symbols:
        return None, "No market symbols are configured."

    try:
        url = "https://query1.finance.yahoo.com/v7/finance/quote?symbols=" + quote(",".join(symbols))
        response = requests.get(url, timeout=12)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None, "Yahoo Finance could not be reached."

    result_bucket = payload.get("quoteResponse", {}) if isinstance(payload, dict) else {}
    results = result_bucket.get("result", []) if isinstance(result_bucket, dict) else []
    result_by_symbol = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        if symbol:
            result_by_symbol[symbol] = item

    quotes = []
    for symbol in symbols:
        item = result_by_symbol.get(symbol)
        if not item:
            continue
        price = item.get("regularMarketPrice")
        prev_close = item.get("regularMarketPreviousClose") or item.get("regularMarketOpen")
        change_pct = item.get("regularMarketChangePercent")
        if change_pct in (None, ""):
            try:
                if price is not None and prev_close not in (None, 0):
                    change_pct = ((float(price) - float(prev_close)) / float(prev_close)) * 100.0
            except Exception:
                change_pct = 0.0
        label = str(item.get("shortName") or item.get("longName") or item.get("displayName") or symbol)
        quotes.append({
            "symbol": symbol,
            "label": label[:40],
            "price": price,
            "change_pct": change_pct,
        })

    if not quotes:
        return None, "Yahoo Finance did not return any quotes."

    return {
        "ok": True,
        "mode": "live",
        "provider": "Yahoo Finance",
        "symbols": settings["market_symbols"],
        "quotes": quotes,
        "reason": "",
        "updated_at": int(time.time()),
    }, ""


def fetch_market_snapshot(config):
    settings = get_market_settings(config)
    if settings["market_demo_mode"] == "on":
        return _demo_snapshot(settings)

    cached = _read_cache()
    if (
        cached
        and cached.get("symbols") == settings["market_symbols"]
        and int(time.time()) - int(cached.get("updated_at", 0)) < settings["market_refresh_minutes"] * 60
    ):
        return cached

    snapshot, error = _live_snapshot(settings)
    if snapshot:
        _write_cache(snapshot)
        return snapshot
    if cached:
        cached["reason"] = "Showing cached market data because live Yahoo Finance refresh failed."
        return cached
    fallback = _demo_snapshot(settings)
    fallback["reason"] = error or "Falling back to demo values because live Yahoo Finance quotes failed."
    return fallback


def render_market_canvas(config):
    settings = get_market_settings(config)
    snapshot = fetch_market_snapshot(config)
    canvas = Image.new("RGB", (400, 600), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    font_title = _load_font(18, bold=True)
    font_header = _load_font(18, bold=True)
    font_symbol = _load_font(20, bold=True)
    font_price = _load_font(24, bold=True)
    font_meta = _load_font(12)
    font_body = _load_font(13)

    draw.rectangle((0, 0, 400, 84), fill=(0, 0, 255))
    draw.text((20, 14), "Market", fill=(255, 255, 255), font=font_title)
    draw.text((20, 40), "Snapshot", fill=(255, 255, 255), font=font_header)
    mode_label = "Demo" if snapshot.get("mode") == "demo" else "Live"
    draw.text((380, 16), mode_label, fill=(255, 255, 255), font=font_meta, anchor="ra")
    provider_label = str(snapshot.get("provider") or "Market")[:18]
    if provider_label != mode_label:
        draw.text((380, 40), provider_label, fill=(255, 255, 255), font=font_meta, anchor="ra")

    quotes = snapshot.get("quotes") or []
    top = 104
    footer_reserved = 52 if snapshot.get("reason") else 22
    block_height = max(96, int((600 - footer_reserved - top) / max(1, len(quotes))))
    for item in quotes[:4]:
        draw.rounded_rectangle((20, top, 380, top + block_height - 14), radius=16, outline=(0, 0, 255), width=2, fill=(252, 253, 240))
        draw.text((36, top + 16), item.get("symbol", "--"), fill=(0, 0, 0), font=font_symbol)
        draw.text((364, top + 18), _format_change(item.get("change_pct")), fill=_change_color(item.get("change_pct")), font=font_meta, anchor="ra")
        draw.text((36, top + 48), _format_price(item.get("price")), fill=(0, 0, 0), font=font_price)
        draw.text((36, top + 82), str(item.get("label", ""))[:36], fill=(0, 0, 0), font=font_body)
        top += block_height

    if snapshot.get("reason"):
        reason = str(snapshot["reason"])
        reason_lines = []
        current = ""
        words = reason.split()
        for word in words:
            trial = (current + " " + word).strip()
            if draw.textbbox((0, 0), trial, font=font_meta)[2] <= 360 or not current:
                current = trial
            else:
                reason_lines.append(current)
                current = word
                if len(reason_lines) >= 2:
                    break
        if current and len(reason_lines) < 2:
            reason_lines.append(current)
        for idx, line in enumerate(reason_lines):
            draw.text((20, 546 + (idx * 14)), line, fill=(0, 0, 0), font=font_meta)

    updated_struct = time.localtime(snapshot.get("updated_at", int(time.time())))
    draw.line((20, 582, 380, 582), fill=(220, 226, 230), width=1)
    draw.text((20, 586), "Updated " + time.strftime("%-I:%M %p", updated_struct), fill=(0, 0, 0), font=font_meta)
    draw.text((380, 586), str(snapshot.get("provider") or "Market")[:18], fill=(0, 0, 255), font=font_meta, anchor="ra")
    return canvas, snapshot
