#!/usr/bin/python3
"""
Runtime helpers for third-party InkSlab plugins.

These plugins are loaded from local plugin directories and use a stable
`render(settings, context)` contract.
"""

import importlib.util
import os
import sys
import time

from PIL import Image, ImageDraw, ImageFont

from inkslab_paths import APP_DIR, HOME_DIR, PLUGIN_RUNTIME_CACHE_DIR, RUNTIME_DIR
from inkslab_plugins import get_plugin


_MODULE_CACHE = {}


def _load_font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(path, size)
    except IOError:
        return ImageFont.load_default()


def _plugin_file_path(plugin):
    manifest_path = getattr(plugin, "manifest_path", None) or ""
    plugin_dir = os.path.dirname(manifest_path)
    entrypoint = getattr(plugin, "entrypoint", "__init__.py") or "__init__.py"
    entry_name = os.path.basename(entrypoint)
    path = os.path.join(plugin_dir, entry_name)
    if not os.path.isfile(path):
        return ""
    return path


def _load_plugin_module(plugin):
    path = _plugin_file_path(plugin)
    if not path:
        raise FileNotFoundError("Plugin entry point is missing.")
    mtime = os.path.getmtime(path)
    cache_key = (plugin.plugin_id, path, mtime)
    cached = _MODULE_CACHE.get(cache_key)
    if cached:
        return cached

    module_name = "inkslab_plugin_" + plugin.plugin_id.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise ImportError("Could not load plugin module.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _MODULE_CACHE.clear()
    _MODULE_CACHE[cache_key] = module
    return module


def _default_wait_seconds(settings):
    for key, value in (settings or {}).items():
        if str(key).endswith("_refresh_minutes"):
            try:
                return max(60, min(86400, int(value) * 60))
            except (TypeError, ValueError):
                continue
    return 30 * 60


def _normalize_canvas(image):
    if image.mode != "RGB":
        image = image.convert("RGB")
    if image.size == (400, 600):
        return image
    final = Image.new("RGB", (400, 600), (255, 255, 255))
    fitted = image.copy()
    fitted.thumbnail((400, 600))
    x = max(0, (400 - fitted.width) // 2)
    y = max(0, (600 - fitted.height) // 2)
    final.paste(fitted, (x, y))
    if fitted is not image:
        fitted.close()
    image.close()
    return final


def _error_canvas(title, message):
    canvas = Image.new("RGB", (400, 600), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    font_title = _load_font(18, bold=True)
    font_body = _load_font(14)
    font_meta = _load_font(12)
    draw.rectangle((0, 0, 400, 84), fill=(0, 0, 255))
    draw.text((20, 18), title[:28], fill=(255, 255, 255), font=font_title)
    draw.text((20, 46), "Plugin Error", fill=(255, 255, 255), font=font_title)
    words = str(message or "Unknown plugin error.").split()
    lines = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        if draw.textbbox((0, 0), trial, font=font_body)[2] <= 340 or not current:
            current = trial
        else:
            lines.append(current)
            current = word
            if len(lines) >= 10:
                break
    if current and len(lines) < 10:
        lines.append(current)
    for idx, line in enumerate(lines):
        draw.text((28, 130 + (idx * 22)), line, fill=(0, 0, 0), font=font_body)
    draw.text((20, 584), "Fix the plugin code or settings and try again.", fill=(0, 0, 0), font=font_meta)
    return canvas


def _plugin_context(plugin, config):
    manifest_path = getattr(plugin, "manifest_path", None) or ""
    plugin_dir = os.path.dirname(manifest_path)
    return {
        "plugin_id": plugin.plugin_id,
        "plugin_name": plugin.name,
        "plugin_dir": plugin_dir,
        "project_root": APP_DIR,
        "home_dir": HOME_DIR,
        "runtime_dir": RUNTIME_DIR,
        "cache_dir": os.path.join(PLUGIN_RUNTIME_CACHE_DIR, plugin.plugin_id),
        "canvas_size": {"width": 400, "height": 600},
        "now": int(time.time()),
        "config": config,
        "new_canvas": lambda color=(255, 255, 255): Image.new("RGB", (400, 600), color),
    }


def render_external_plugin(plugin_id, config):
    plugin = get_plugin(plugin_id)
    if not plugin or plugin.source != "local-manifest" or not plugin.runtime_enabled:
        return None, None

    settings = {}
    if isinstance(config, dict):
        plugin_settings = config.get("plugin_settings") if isinstance(config.get("plugin_settings"), dict) else {}
        settings = plugin_settings.get(plugin_id) if isinstance(plugin_settings.get(plugin_id), dict) else {}

    try:
        module = _load_plugin_module(plugin)
        render_fn = getattr(module, "render", None)
        if not callable(render_fn):
            raise AttributeError("Plugin is missing a callable render(settings, context) function.")
        os.makedirs(os.path.join(PLUGIN_RUNTIME_CACHE_DIR, plugin_id), exist_ok=True)
        result = render_fn(settings, _plugin_context(plugin, config))
        payload = {}
        image = None
        if isinstance(result, Image.Image):
            image = result
        elif isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], Image.Image) and isinstance(result[1], dict):
            image = result[0]
            payload = dict(result[1])
        elif isinstance(result, dict) and isinstance(result.get("image"), Image.Image):
            image = result.get("image")
            payload = dict(result)
        else:
            raise TypeError("Plugin render() must return an Image, (Image, payload), or a payload dict with image.")
        image = _normalize_canvas(image)
        return image, {
            "name": str(payload.get("name") or plugin.name)[:48],
            "wait_seconds": max(60, min(86400, int(payload.get("wait_seconds") or _default_wait_seconds(settings)))),
            "set_info": str(payload.get("set_info") or plugin.name)[:80],
            "card_num": str(payload.get("card_num") or "")[:40],
            "rarity": str(payload.get("rarity") or payload.get("reason") or plugin.description or "")[:160],
            "error": str(payload.get("error") or payload.get("reason") or "Plugin render failed.")[:160],
        }
    except Exception as exc:
        canvas = _error_canvas(plugin.name, str(exc))
        return canvas, {
            "name": plugin.name,
            "wait_seconds": _default_wait_seconds(settings),
            "set_info": plugin.name,
            "card_num": "",
            "rarity": "Plugin error",
            "error": str(exc)[:160],
        }
