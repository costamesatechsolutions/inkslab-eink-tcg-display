#!/usr/bin/python3
"""
Market Snapshot helpers for InkSlab.

Demo mode works without setup. Optional live quotes can use yfinance.
"""

import json
import os
import time

from PIL import Image, ImageDraw, ImageFont

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
        "provider": "Demo",
        "quotes": quotes,
        "reason": "Demo values are showing. Turn demo mode off to use free Yahoo Finance quotes.",
        "updated_at": int(time.time()),
    }


def _live_snapshot(settings):
    try:
        import yfinance as yf
    except Exception:
        return None, "yfinance is not installed yet."

    quotes = []
    for symbol in settings["market_symbols"]:
        ticker = yf.Ticker(symbol)
        fast = getattr(ticker, "fast_info", None) or {}
        history = ticker.history(period="5d", interval="1d", auto_adjust=False)
        price = fast.get("lastPrice") or fast.get("regularMarketPrice")
        prev_close = fast.get("previousClose")
        if (price is None or prev_close in (None, 0)) and history is not None and not history.empty:
            try:
                price = price if price is not None else float(history["Close"].iloc[-1])
                prev_close = prev_close if prev_close not in (None, 0) else float(history["Close"].iloc[-2])
            except Exception:
                pass
        change_pct = 0.0
        try:
            if price is not None and prev_close not in (None, 0):
                change_pct = ((float(price) - float(prev_close)) / float(prev_close)) * 100.0
        except Exception:
            change_pct = 0.0
        label = symbol
        try:
            info = getattr(ticker, "info", {}) or {}
            label = str(info.get("shortName") or info.get("longName") or symbol)
        except Exception:
            pass
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
    font_symbol = _load_font(22, bold=True)
    font_price = _load_font(24, bold=True)
    font_meta = _load_font(12)
    font_body = _load_font(13)

    draw.rectangle((0, 0, 400, 84), fill=(0, 0, 255))
    draw.text((20, 14), "Market", fill=(255, 255, 255), font=font_title)
    draw.text((20, 40), "Snapshot", fill=(255, 255, 255), font=font_header)
    mode_label = "Demo" if snapshot.get("mode") == "demo" else "Live"
    draw.text((380, 16), mode_label, fill=(255, 255, 255), font=font_meta, anchor="ra")
    draw.text((380, 40), str(snapshot.get("provider") or "Market")[:18], fill=(255, 255, 255), font=font_meta, anchor="ra")

    quotes = snapshot.get("quotes") or []
    top = 104
    block_height = max(106, int((582 - top) / max(1, len(quotes))))
    for item in quotes[:4]:
        draw.rounded_rectangle((20, top, 380, top + block_height - 14), radius=16, outline=(0, 0, 255), width=2, fill=(252, 253, 240))
        draw.text((36, top + 18), item.get("symbol", "--"), fill=(0, 0, 0), font=font_symbol)
        draw.text((364, top + 22), _format_change(item.get("change_pct")), fill=_change_color(item.get("change_pct")), font=font_meta, anchor="ra")
        draw.text((36, top + 52), _format_price(item.get("price")), fill=(0, 0, 0), font=font_price)
        draw.text((36, top + 84), str(item.get("label", ""))[:36], fill=(0, 0, 0), font=font_body)
        top += block_height

    if snapshot.get("reason"):
        draw.text((20, 556), snapshot["reason"][:52], fill=(0, 0, 0), font=font_meta)

    updated_struct = time.localtime(snapshot.get("updated_at", int(time.time())))
    draw.line((20, 582, 380, 582), fill=(220, 226, 230), width=1)
    draw.text((20, 586), "Updated " + time.strftime("%-I:%M %p", updated_struct), fill=(0, 0, 0), font=font_meta)
    draw.text((380, 586), str(snapshot.get("provider") or "Market")[:18], fill=(0, 0, 255), font=font_meta, anchor="ra")
    return canvas, snapshot
