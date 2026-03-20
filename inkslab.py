#!/usr/bin/python3
# -*- coding:utf-8 -*-
"""
InkSlab - e-Ink TCG Card Display
https://github.com/costamesatechsolutions/inkslab-eink-tcg-display

Displays random TCG cards on a Waveshare 4" e-Paper (E) / Spectra 6 color display
in a graded-slab-style layout with set name, year, card number, and rarity.
Cards rotate every 10 minutes during the day (7am-11pm) and every hour at night.

By Costa Mesa Tech Solutions (a brand of Pine Heights Ventures LLC)
"""

import sys
import os
import time
import random
import json
import logging
import signal
from PIL import Image, ImageEnhance, ImageDraw, ImageFont, ImageOps
import wifi_manager

# --- DEFAULT CONFIGURATION ---
# These defaults are used if no config file exists.
# The web dashboard writes to the config file to change settings on the fly.
DEFAULTS = {
    "active_tcg": "pokemon",
    "rotation_angle": 270,
    "day_interval": 600,
    "night_interval": 3600,
    "day_start": 7,
    "day_end": 23,
    "color_saturation": 2.5,
    "collection_only": False,
    "slab_header_mode": "normal",
    "timezone_offset": None,
    "timezone_name": None,
}

# --- DYNAMIC TCG REGISTRY ---
TCG_REGISTRY = {
    "pokemon": {"name": "Pokemon", "path": "/home/pi/pokemon_cards", "color": "#36A5CA", "download_script": "download_cards_pokemon.py"},
    "mtg":     {"name": "Magic: The Gathering", "path": "/home/pi/mtg_cards", "color": "#6BCCBD", "download_script": "download_cards_mtg.py"},
    "lorcana": {"name": "Disney Lorcana", "path": "/home/pi/lorcana_cards", "color": "#C084FC", "download_script": "download_cards_lorcana.py"},
    "custom":  {"name": "Custom", "path": "/home/pi/custom_cards", "color": "#F59E0B", "download_script": None},
}
TCG_LIBRARIES = {k: v["path"] for k, v in TCG_REGISTRY.items()}

# Supported image formats
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.avif')

CONFIG_FILE = "/home/pi/inkslab_config.json"
COLLECTION_FILE = "/home/pi/inkslab_collection.json"
STATUS_FILE = "/tmp/inkslab_status.json"
NEXT_TRIGGER = "/tmp/inkslab_next"
PREV_TRIGGER = "/tmp/inkslab_prev"
PAUSE_FILE = "/tmp/inkslab_pause"
COLLECTION_TRIGGER = "/tmp/inkslab_collection_changed"
WIFI_CONNECTED_TRIGGER = "/tmp/inkslab_wifi_connected"
WIFI_SETUP_TRIGGER = "/tmp/inkslab_wifi_setup"
WIFI_FAILED_TRIGGER = "/tmp/inkslab_wifi_failed"
UNBOX_TRIGGER = "/tmp/inkslab_unbox"
WATCHDOG_SETUP_FLAG = "/tmp/inkslab_watchdog_setup"

# WiFi watchdog: if WiFi is down for this many seconds, auto-enter hotspot setup mode.
# 30 minutes — long enough to ride out brief router reboots, short enough that the user
# doesn't stare at an unreachable device for hours after changing routers.
WIFI_WATCHDOG_TIMEOUT = 1800  # 30 minutes
WIFI_WATCHDOG_CHECK_INTERVAL = 120  # check every 2 minutes (don't spam nmcli)

# Graceful shutdown flag (module-level so wait_with_polling can check it)
_shutdown = False

# WiFi watchdog state (module-level so it persists across wait_with_polling calls)
_wifi_down_since = 0  # timestamp when WiFi was first seen down, 0 = connected

# Image processing (not configurable via web — these are display-specific)
DISPLAY_WIDTH = 400
DISPLAY_HEIGHT = 600
CONTRAST_BOOST = 1.1
SHARPNESS_BOOST = 1.4

# 7-color palette for Spectra 6: Black, White, Green, Blue, Red, Yellow, Orange
PALETTE_COLORS = [0, 0, 0, 255, 255, 255, 0, 255, 0, 0, 0, 255, 255, 0, 0, 255, 255, 0, 255, 128, 0]

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# --- HARDWARE SETUP ---
# Try the Waveshare SDK lib (three levels up: project dir -> examples -> python -> lib)
_script_dir = os.path.dirname(os.path.realpath(__file__))
_sdk_libdir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_script_dir))), 'lib')
_local_libdir = os.path.join(_script_dir, 'lib')

if os.path.exists(_sdk_libdir):
    sys.path.insert(0, _sdk_libdir)
if os.path.exists(_local_libdir):
    sys.path.insert(0, _local_libdir)

try:
    from waveshare_epd import epd4in0e
except ImportError:
    logger.error(f"Cannot import waveshare_epd. Checked: {_sdk_libdir}, {_local_libdir}")
    logger.error("Make sure the Waveshare e-Paper library is installed.")
    sys.exit(1)


def load_config():
    """Load config from file, falling back to defaults for missing keys."""
    config = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                saved = json.load(f)
            config.update(saved)
        except Exception as e:
            logger.warning(f"Error reading config: {e}, using defaults")
    return config


def load_collection(tcg):
    """Load the collection list for a given TCG. Returns a set of card IDs."""
    if os.path.exists(COLLECTION_FILE):
        try:
            with open(COLLECTION_FILE, 'r') as f:
                data = json.load(f)
            return set(data.get(tcg, []))
        except Exception:
            pass
    return set()


def load_master_index(library_dir):
    """Load the master set index from a library directory.
    Falls back to auto-discovering subdirectories for custom folders."""
    index_file = os.path.join(library_dir, "master_index.json")
    if os.path.exists(index_file):
        try:
            with open(index_file, 'r') as f:
                index = json.load(f)
            # Also discover any subdirs not in the index (newly added custom folders)
            if os.path.isdir(library_dir):
                for d in os.listdir(library_dir):
                    if os.path.isdir(os.path.join(library_dir, d)) and d not in index:
                        index[d] = {"name": d.replace('_', ' ').replace('-', ' ').title(), "year": ""}
            return index
        except Exception:
            pass
    # No index file — auto-discover from subdirectories
    index = {}
    if os.path.isdir(library_dir):
        for d in os.listdir(library_dir):
            if os.path.isdir(os.path.join(library_dir, d)):
                index[d] = {"name": d.replace('_', ' ').replace('-', ' ').title(), "year": ""}
    return index


def write_status(info):
    """Write current display status for the web dashboard.

    Uses atomic write (tmp + rename) so the web server never reads partial JSON.
    Both files live in /tmp (same tmpfs), so os.rename() is atomic.
    """
    try:
        tmp = STATUS_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(info, f)
        os.replace(tmp, STATUS_FILE)
    except Exception:
        pass


def _is_card_image(filename):
    """Check if a file is a supported card image."""
    return filename.lower().endswith(IMAGE_EXTENSIONS) and not filename.startswith('_')


def get_local_ip():
    """Get the Pi's local IP address (non-hotspot)."""
    try:
        import subprocess
        result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5)
        parts = result.stdout.strip().split()
        # Filter out hotspot IPs so splash screen shows the real LAN address
        for ip in parts:
            if not ip.startswith("10.42."):
                return ip
        return parts[0] if parts else None
    except Exception:
        return None


def make_qr(url, box_size=4, border=1):
    """Generate a QR code as a PIL RGB image."""
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=box_size, border=border,
                            error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white").convert("RGB")
    except Exception as e:
        logger.warning(f"QR code generation failed: {e}")
        return None


def show_splash_screen(epd, config):
    """Show a branded splash screen with the dashboard URL on the e-ink display.
    Handles full init/display/sleep cycle internally."""
    try:
        # Wait up to 60s for an IP address (DHCP can lag behind WiFi association,
        # especially on first boot with heavy background activity)
        ip = None
        for _ in range(12):
            ip = get_local_ip()
            if ip:
                break
            time.sleep(5)
        if not ip:
            logger.info("No IP address available after 60s, showing splash without URL")
            ip = "<no IP yet>"

        canvas = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        # Load fonts
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
            font_url = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
            font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        except Exception:
            font_title = ImageFont.load_default()
            font_url = font_title
            font_body = font_title
            font_small = font_title

        # Get WiFi info
        ssid = None
        signal_strength = None
        try:
            ssid = wifi_manager.get_active_ssid()
            signal_strength = wifi_manager.get_signal_strength()
        except Exception:
            pass

        # Draw content centered
        cx = DISPLAY_WIDTH // 2

        # Title
        draw.text((cx, 50), "InkSlab", fill=(0, 0, 0), font=font_title, anchor="mm")

        # WiFi info line
        if ssid:
            wifi_text = f"WiFi: {ssid}"
            if signal_strength is not None:
                wifi_text += f"  ({signal_strength}%)"
            draw.text((cx, 95), wifi_text, fill=(0, 0, 0), font=font_small, anchor="mm")

        # Dashboard URL with QR code
        url_text = f"http://{ip}"
        draw.text((cx, 130), "Scan or open in your", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 160), "web browser:", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 205), url_text, fill=(0, 0, 255), font=font_url, anchor="mm")

        # QR code
        qr_img = make_qr(url_text)
        if qr_img:
            qr_size = min(qr_img.size[0], 130)
            qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.NEAREST)
            canvas.paste(qr_img, (cx - qr_size // 2, 240))
            qr_img.close()

        # Transition note
        draw.text((cx, 430), "Your cards will appear shortly.", fill=(0, 0, 0), font=font_body, anchor="mm")

        # Bottom credit
        draw.text((cx, 555), "Costa Mesa Tech Solutions", fill=(0, 0, 0), font=font_small, anchor="mm")

        # Process for e-paper display
        img = ImageEnhance.Contrast(canvas).enhance(CONTRAST_BOOST)
        canvas.close()
        palette_ref = create_palette_image()
        img_dithered = img.quantize(palette=palette_ref, dither=Image.Dither.FLOYDSTEINBERG)
        img.close()
        palette_ref.close()
        img_rgb = img_dithered.convert("RGB")
        img_dithered.close()
        final = img_rgb.rotate(config["rotation_angle"], expand=True)
        img_rgb.close()

        epd.init()
        epd.display(epd.getbuffer(final))
        epd.sleep()
        final.close()
        logger.info(f"Splash screen shown: dashboard at {url_text}")
        return bool(ip and ip != "<no IP yet>")

    except Exception as e:
        logger.warning(f"Splash screen skipped: {e}")
        try:
            epd.sleep()
        except Exception:
            pass
        return False


def show_setup_screen(epd, config):
    """Show WiFi setup instructions on the e-ink display when no WiFi is configured.
    Handles full init/display/sleep cycle internally."""
    try:
        canvas = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        # Load fonts
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
            font_heading = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
            font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
            font_url = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        except Exception:
            font_title = ImageFont.load_default()
            font_heading = font_title
            font_body = font_title
            font_url = font_title
            font_small = font_title

        cx = DISPLAY_WIDTH // 2

        # Title
        draw.text((cx, 35), "InkSlab WiFi Setup", fill=(0, 0, 0), font=font_title, anchor="mm")

        # Step 1 — QR code to auto-join the hotspot
        draw.text((cx, 75), "Step 1: Scan to connect", fill=(0, 0, 255), font=font_heading, anchor="mm")

        # WiFi QR code (standard format, works on iOS + Android)
        wifi_qr = make_qr("WIFI:T:nopass;S:InkSlab-Setup;;", box_size=3, border=1)
        if wifi_qr:
            qr_size = min(wifi_qr.size[0], 120)
            wifi_qr = wifi_qr.resize((qr_size, qr_size), Image.Resampling.NEAREST)
            canvas.paste(wifi_qr, (cx - qr_size // 2, 100))
            wifi_qr.close()

        draw.text((cx, 235), "Scan with your phone camera", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 258), "to join the InkSlab-Setup WiFi.", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 288), "Or connect manually:", fill=(0, 0, 0), font=font_small, anchor="mm")
        draw.text((cx, 310), "InkSlab-Setup", fill=(255, 0, 0), font=font_url, anchor="mm")
        draw.text((cx, 336), "(no password)", fill=(0, 0, 0), font=font_small, anchor="mm")

        # Step 2
        draw.text((cx, 380), "Step 2: Set up your WiFi", fill=(0, 0, 255), font=font_heading, anchor="mm")
        draw.text((cx, 410), "A setup page will open automatically.", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 433), "If not, open your browser and go to:", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 465), "10.42.0.1", fill=(0, 0, 255), font=font_url, anchor="mm")

        # Step 3
        draw.text((cx, 515), "Pick your home WiFi network", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 538), "and enter the password.", fill=(0, 0, 0), font=font_body, anchor="mm")

        # Process for e-paper display
        img = ImageEnhance.Contrast(canvas).enhance(CONTRAST_BOOST)
        canvas.close()
        palette_ref = create_palette_image()
        img_dithered = img.quantize(palette=palette_ref, dither=Image.Dither.FLOYDSTEINBERG)
        img.close()
        palette_ref.close()
        img_rgb = img_dithered.convert("RGB")
        img_dithered.close()
        final = img_rgb.rotate(config["rotation_angle"], expand=True)
        img_rgb.close()

        epd.init()
        epd.display(epd.getbuffer(final))
        epd.sleep()
        final.close()
        logger.info("Setup screen shown: connect to InkSlab-Setup WiFi")

    except Exception as e:
        logger.warning(f"Setup screen skipped: {e}")
        try:
            epd.sleep()
        except Exception:
            pass


def show_wifi_failed_screen(epd, config, ssid=""):
    """Show a WiFi connection failure message on the e-ink display."""
    try:
        canvas = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
            font_heading = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
            font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
            font_url = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        except Exception:
            font_title = ImageFont.load_default()
            font_heading = font_title
            font_body = font_title
            font_url = font_title
            font_small = font_title

        cx = DISPLAY_WIDTH // 2

        draw.text((cx, 50), "Connection Failed", fill=(255, 0, 0), font=font_title, anchor="mm")

        if ssid:
            draw.text((cx, 100), f"Could not connect to:", fill=(0, 0, 0), font=font_body, anchor="mm")
            # Truncate long SSIDs
            display_ssid = ssid if len(ssid) <= 24 else ssid[:21] + "..."
            draw.text((cx, 130), display_ssid, fill=(0, 0, 0), font=font_heading, anchor="mm")
        else:
            draw.text((cx, 115), "Could not connect to WiFi.", fill=(0, 0, 0), font=font_body, anchor="mm")

        draw.text((cx, 180), "Check your password and try again.", fill=(0, 0, 0), font=font_body, anchor="mm")

        draw.text((cx, 240), "To retry:", fill=(0, 0, 255), font=font_heading, anchor="mm")
        draw.text((cx, 275), "1. Connect to InkSlab-Setup WiFi", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 305), "2. Open your browser and go to:", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 345), "10.42.0.1", fill=(0, 0, 255), font=font_url, anchor="mm")

        # WiFi QR to rejoin hotspot
        wifi_qr = make_qr("WIFI:T:nopass;S:InkSlab-Setup;;", box_size=3, border=1)
        if wifi_qr:
            qr_size = min(wifi_qr.size[0], 100)
            wifi_qr = wifi_qr.resize((qr_size, qr_size), Image.Resampling.NEAREST)
            canvas.paste(wifi_qr, (cx - qr_size // 2, 385))
            wifi_qr.close()

        draw.text((cx, 510), "Scan to rejoin InkSlab-Setup", fill=(0, 0, 0), font=font_small, anchor="mm")

        draw.text((cx, 555), "Costa Mesa Tech Solutions", fill=(0, 0, 0), font=font_small, anchor="mm")

        img = ImageEnhance.Contrast(canvas).enhance(CONTRAST_BOOST)
        canvas.close()
        palette_ref = create_palette_image()
        img_dithered = img.quantize(palette=palette_ref, dither=Image.Dither.FLOYDSTEINBERG)
        img.close()
        palette_ref.close()
        img_rgb = img_dithered.convert("RGB")
        img_dithered.close()
        final = img_rgb.rotate(config["rotation_angle"], expand=True)
        img_rgb.close()

        epd.init()
        epd.display(epd.getbuffer(final))
        epd.sleep()
        final.close()
        logger.info(f"WiFi failed screen shown for SSID: {ssid}")

    except Exception as e:
        logger.warning(f"WiFi failed screen skipped: {e}")
        try:
            epd.sleep()
        except Exception:
            pass


def show_no_cards_screen(epd, config, ip=None):
    """Show a welcome screen when no cards are downloaded yet."""
    try:
        canvas = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
            font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
            font_url = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        except Exception:
            font_title = ImageFont.load_default()
            font_body = font_title
            font_url = font_title
            font_small = font_title

        cx = DISPLAY_WIDTH // 2

        draw.text((cx, 45), "InkSlab", fill=(0, 0, 0), font=font_title, anchor="mm")

        # WiFi info
        try:
            ssid = wifi_manager.get_active_ssid()
            signal_strength = wifi_manager.get_signal_strength()
            if ssid:
                wifi_text = f"WiFi: {ssid}"
                if signal_strength is not None:
                    wifi_text += f"  ({signal_strength}%)"
                draw.text((cx, 85), wifi_text, fill=(0, 0, 0), font=font_small, anchor="mm")
        except Exception:
            pass

        collection_mode = config.get("collection_only", False)
        if collection_mode:
            draw.text((cx, 115), "No cards in your collection.", fill=(0, 0, 0), font=font_body, anchor="mm")
        else:
            draw.text((cx, 115), "No cards downloaded yet.", fill=(0, 0, 0), font=font_body, anchor="mm")

        draw.text((cx, 160), "Scan or open in your", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 190), "web browser:", fill=(0, 0, 0), font=font_body, anchor="mm")

        if ip:
            url_text = f"http://{ip}"
            draw.text((cx, 235), url_text, fill=(0, 0, 255), font=font_url, anchor="mm")
            # QR code
            qr_img = make_qr(url_text)
            if qr_img:
                qr_size = min(qr_img.size[0], 130)
                qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.NEAREST)
                canvas.paste(qr_img, (cx - qr_size // 2, 270))
                qr_img.close()

        if collection_mode:
            draw.text((cx, 430), "Open the Collection tab and", fill=(0, 0, 0), font=font_body, anchor="mm")
            draw.text((cx, 460), "mark the cards you own.", fill=(0, 0, 0), font=font_body, anchor="mm")
        else:
            draw.text((cx, 430), "Then tap Downloads and pick", fill=(0, 0, 0), font=font_body, anchor="mm")
            draw.text((cx, 460), "a card game to download.", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 520), "Cards will appear on this", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 550), "display once ready.", fill=(0, 0, 0), font=font_body, anchor="mm")

        img = ImageEnhance.Contrast(canvas).enhance(CONTRAST_BOOST)
        canvas.close()
        palette_ref = create_palette_image()
        img_dithered = img.quantize(palette=palette_ref, dither=Image.Dither.FLOYDSTEINBERG)
        img.close()
        palette_ref.close()
        img_rgb = img_dithered.convert("RGB")
        img_dithered.close()
        final = img_rgb.rotate(config["rotation_angle"], expand=True)
        img_rgb.close()

        epd.init()
        epd.display(epd.getbuffer(final))
        epd.sleep()
        final.close()
        logger.info("No-cards welcome screen shown")

    except Exception as e:
        logger.warning(f"No-cards screen skipped: {e}")
        try:
            epd.sleep()
        except Exception:
            pass


def show_unbox_screen(epd, config):
    """Show a customer-facing 'Plug me in!' screen for shipping. E-ink retains this when powered off."""
    try:
        canvas = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
            font_action = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
            font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        except Exception:
            font_title = ImageFont.load_default()
            font_action = font_title
            font_body = font_title
            font_small = font_title

        cx = DISPLAY_WIDTH // 2

        draw.text((cx, 80), "Welcome!", fill=(0, 0, 0), font=font_title, anchor="mm")
        draw.text((cx, 140), "to InkSlab", fill=(0, 0, 0), font=font_body, anchor="mm")

        draw.text((cx, 240), "Plug me in", fill=(0, 0, 255), font=font_action, anchor="mm")
        draw.text((cx, 280), "to get started!", fill=(0, 0, 255), font=font_action, anchor="mm")

        draw.text((cx, 370), "After plugging in, wait about", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 400), "90 seconds for this screen", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 430), "to update with setup instructions.", fill=(0, 0, 0), font=font_body, anchor="mm")

        draw.text((cx, 500), "Do not unplug during setup.", fill=(0, 0, 0), font=font_body, anchor="mm")
        draw.text((cx, 555), "Costa Mesa Tech Solutions", fill=(0, 0, 0), font=font_small, anchor="mm")

        img = ImageEnhance.Contrast(canvas).enhance(CONTRAST_BOOST)
        canvas.close()
        palette_ref = create_palette_image()
        img_dithered = img.quantize(palette=palette_ref, dither=Image.Dither.FLOYDSTEINBERG)
        img.close()
        palette_ref.close()
        img_rgb = img_dithered.convert("RGB")
        img_dithered.close()
        final = img_rgb.rotate(config["rotation_angle"], expand=True)
        img_rgb.close()

        epd.init()
        epd.display(epd.getbuffer(final))
        epd.sleep()
        final.close()
        logger.info("Unbox/shipping screen shown")

    except Exception as e:
        logger.warning(f"Unbox screen skipped: {e}")
        try:
            epd.sleep()
        except Exception:
            pass


def get_card_metadata(img_path, master_index):
    """Extract set name, card number, and rarity from card image path."""
    info = {"set_info": "", "stats": "", "set_name": "", "card_num": "", "rarity": ""}
    try:
        folder_path = os.path.dirname(img_path)
        filename = os.path.basename(img_path)
        set_id = os.path.basename(folder_path)
        card_id = os.path.splitext(filename)[0]

        # Set info (top line)
        if set_id in master_index:
            year = master_index[set_id].get("year", "")
            real_set = master_index[set_id]["name"].upper().replace(" AND ", " & ")
            if year:
                info["set_info"] = f"{year} {real_set}"
            else:
                info["set_info"] = real_set
            info["set_name"] = master_index[set_id]["name"]
        else:
            info["set_info"] = set_id.upper()
            info["set_name"] = set_id

        # Card stats (bottom line)
        num = "00"
        extra = ""

        json_path = os.path.join(folder_path, "_data.json")
        if os.path.exists(json_path):
            with open(json_path, "r") as f:
                data = json.load(f)
                if card_id in data:
                    entry = data[card_id]
                    num = entry.get("number", "00")

                    if entry.get("rarity"):
                        extra = entry["rarity"].upper()
                        extra = extra.replace("RARE HOLO", "HOLO").replace("DOUBLE RARE", "DBL RARE")
        else:
            # Auto-generate metadata from filename (for custom images)
            name = card_id.replace('_', ' ').replace('-', ' ').title()
            # No number or rarity to extract

        # Try extracting number from card ID if not found in metadata
        if num == "00" and "-" in card_id:
            parts = card_id.split("-")
            if parts[-1].isdigit():
                num = parts[-1]

        info["card_num"] = f"#{num}"
        info["rarity"] = extra

        # Format: "#201  •  HOLO" or just "#201"
        if extra:
            info["stats"] = f"#{num}  \u2022  {extra}"
        else:
            info["stats"] = f"#{num}"

    except Exception as e:
        logger.debug(f"Metadata error for {img_path}: {e}")
    return info


def create_slab_layout(img_path, master_index, header_mode="normal"):
    """Create a PSA-slab-style layout with card info header above the card image.
    header_mode: 'normal' (white bg, black text), 'inverted' (black bg, white text), 'off' (full card)"""
    info = get_card_metadata(img_path, master_index)

    with Image.open(img_path) as card_raw:
        card = card_raw.convert("RGB")

        if header_mode == "off":
            # Full-screen: center-crop card to fill entire display
            aspect = card.width / card.height
            display_aspect = DISPLAY_WIDTH / DISPLAY_HEIGHT
            if aspect > display_aspect:
                new_h = DISPLAY_HEIGHT
                new_w = int(new_h * aspect)
            else:
                new_w = DISPLAY_WIDTH
                new_h = int(new_w / aspect)
            card_resized = card.resize((new_w, new_h), Image.Resampling.LANCZOS)
            card.close()
            left = (new_w - DISPLAY_WIDTH) // 2
            top = (new_h - DISPLAY_HEIGHT) // 2
            card_cropped = card_resized.crop((left, top, left + DISPLAY_WIDTH, top + DISPLAY_HEIGHT))
            card_resized.close()
            canvas = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), (0, 0, 0))
            canvas.paste(card_cropped, (0, 0))
            card_cropped.close()
            return canvas, info

        # Normal or inverted: scale card to fill display width
        aspect = card.height / card.width
        new_h = int(DISPLAY_WIDTH * aspect)
        card_resized = card.resize((DISPLAY_WIDTH, new_h), Image.Resampling.LANCZOS)
        card.close()
        card = card_resized

        # Background color depends on mode
        bg_color = (0, 0, 0) if header_mode == "inverted" else (255, 255, 255)
        text_color = (255, 255, 255) if header_mode == "inverted" else (0, 0, 0)

        canvas = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), bg_color)
        draw = ImageDraw.Draw(canvas)

        # Position card flush to bottom
        y_pos = DISPLAY_HEIGHT - new_h
        canvas.paste(card, (0, y_pos))

        # Draw header text in the space above the card
        if y_pos > 30:
            try:
                font_set = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
                font_stats = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
            except IOError:
                font_set = ImageFont.load_default()
                font_stats = ImageFont.load_default()

            line1 = info["set_info"]
            line2 = info["stats"]

            # Measure text width
            w1 = draw.textbbox((0, 0), line1, font=font_set)[2]
            w2 = draw.textbbox((0, 0), line2, font=font_stats)[2]

            # Auto-shrink set name if it overflows
            if w1 > 380:
                try:
                    font_set = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 12)
                    w1 = draw.textbbox((0, 0), line1, font=font_set)[2]
                except IOError:
                    pass

            # Center text vertically in header area
            h1, h2, gap = 14, 18, 4
            total_h = h1 + h2 + gap
            start_y = (y_pos - total_h) // 2

            draw.text(((DISPLAY_WIDTH - w1) / 2, start_y), line1, font=font_set, fill=text_color)
            draw.text(((DISPLAY_WIDTH - w2) / 2, start_y + h1 + gap), line2, font=font_stats, fill=text_color)

        return canvas, info


def create_palette_image():
    """Create a reference palette image for Floyd-Steinberg dithering (cached)."""
    return _PALETTE_IMAGE.copy()

# Pre-create the palette image once at module load
_PALETTE_IMAGE = Image.new('P', (1, 1))
_PALETTE_IMAGE.putpalette(PALETTE_COLORS + [0, 0, 0] * (256 - len(PALETTE_COLORS) // 3))


def process_image(img_path, master_index, config):
    """Full image pipeline: layout -> enhance -> dither -> rotate for display."""
    img = palette_ref = img_dithered = img_rgb = None
    try:
        header_mode = config.get("slab_header_mode", "normal")
        img, info = create_slab_layout(img_path, master_index, header_mode)

        # Boost colors for the e-paper display (each enhance creates a new image)
        old = img
        img = ImageEnhance.Color(img).enhance(config["color_saturation"])
        old.close()
        old = img
        img = ImageEnhance.Contrast(img).enhance(CONTRAST_BOOST)
        old.close()
        old = img
        img = ImageEnhance.Sharpness(img).enhance(SHARPNESS_BOOST)
        old.close()

        # Quantize to 7-color palette with dithering
        palette_ref = create_palette_image()
        img_dithered = img.quantize(palette=palette_ref, dither=Image.Dither.FLOYDSTEINBERG)
        img.close(); img = None
        palette_ref.close(); palette_ref = None

        img_rgb = img_dithered.convert("RGB")
        img_dithered.close(); img_dithered = None
        final = img_rgb.rotate(config["rotation_angle"], expand=True)
        img_rgb.close(); img_rgb = None

        return final, info
    except Exception as e:
        logger.error(f"Image processing error: {e}")
        # Ensure any intermediate PIL images are freed even on exception
        for _im in (img, palette_ref, img_dithered, img_rgb):
            if _im is not None:
                try:
                    _im.close()
                except Exception:
                    pass
        return None, {}


class ShuffleDeck:
    """Manages a shuffled deck of all card image paths. Re-shuffles when exhausted."""

    def __init__(self, root_dir, collection=None, recent=None):
        self.root_dir = root_dir
        self.collection = collection
        self.deck = []
        self.total = 0
        self.history = list(recent) if recent else []
        self.reshuffle()

    def reshuffle(self):
        logger.info("Shuffling deck...")
        temp = []
        if os.path.isdir(self.root_dir):
            for root, dirs, files in os.walk(self.root_dir):
                for f in files:
                    if _is_card_image(f):
                        # If collection mode, only include owned cards
                        if self.collection is not None:
                            card_id = os.path.splitext(f)[0]
                            if card_id not in self.collection:
                                continue
                        temp.append(os.path.join(root, f))

        if self.collection is not None and len(temp) == 0:
            logger.info("Collection mode: no matching cards on disk yet")

        # Smart shuffle: push recently-shown cards to the back of the deck
        # so you see fresh cards first after a reshuffle or deck rebuild
        if self.history:
            recent_set = set(self.history)
            not_recent = [c for c in temp if c not in recent_set]
            was_recent = [c for c in temp if c in recent_set]
            random.shuffle(not_recent)
            random.shuffle(was_recent)
            temp = not_recent + was_recent
        else:
            random.shuffle(temp)

        # Never let the most recently shown card be the very first card
        # after a reshuffle — avoids an immediate repeat on small decks.
        if self.history and len(temp) > 1 and temp[0] == self.history[0]:
            temp.append(temp.pop(0))

        self.deck = temp
        self.total = len(temp)
        logger.info(f"Deck loaded: {self.total} cards")

    def draw(self):
        if not self.deck:
            self.reshuffle()
        if not self.deck:
            return None
        card = self.deck.pop(0)
        self.history.insert(0, card)
        if len(self.history) > 30:
            self.history = self.history[:30]
        return card

    def peek(self, n=3):
        """Return the next n cards without removing them."""
        return self.deck[:n]


def card_summary(card_path, master_index):
    """Extract minimal card info for the status file (used for prev/next queue)."""
    set_id = os.path.basename(os.path.dirname(card_path))
    card_id = os.path.splitext(os.path.basename(card_path))[0]
    info = get_card_metadata(card_path, master_index)
    return {
        "set_id": set_id,
        "card_id": card_id,
        "set_info": info.get("set_info", ""),
        "card_num": info.get("card_num", ""),
        "rarity": info.get("rarity", ""),
    }


def _wifi_setup_wait_loop(epd, config):
    """Wait in hotspot/setup mode for the user to configure WiFi.

    Handles three scenarios:
    - User-initiated "Change WiFi": old profile deleted, no profile exists → wait indefinitely
    - Watchdog-triggered (WiFi went down): profile still exists → alternate between
      hotspot mode (10 min, so user can reconfigure) and reconnect attempts (try saved
      WiFi for 30s). This lets the device auto-recover if the old WiFi comes back.
    - User manually connects via setup page → wifi_connected trigger fires → return

    Returns True if WiFi reconnected, False if loop exited via timeout.
    """
    global _wifi_down_since
    RECONNECT_ATTEMPT_WAIT = 30  # seconds to wait for WiFi association after stopping hotspot

    _wifi_wait = 0
    while not _shutdown and _wifi_wait < 600:
        # User connected via setup page
        if os.path.exists(WIFI_CONNECTED_TRIGGER):
            try:
                os.remove(WIFI_CONNECTED_TRIGGER)
            except OSError:
                pass
            _wifi_down_since = 0
            try:
                os.remove(WATCHDOG_SETUP_FLAG)
            except OSError:
                pass
            return True

        # User tried to connect but it failed
        if os.path.exists(WIFI_FAILED_TRIGGER):
            ssid = ""
            try:
                with open(WIFI_FAILED_TRIGGER, 'r') as f:
                    ssid = f.read().strip()
                os.remove(WIFI_FAILED_TRIGGER)
            except OSError:
                pass
            show_wifi_failed_screen(epd, config, ssid=ssid)
            _wifi_wait = 0  # Reset timeout on user activity

        time.sleep(3)
        _wifi_wait += 3

        # Timeout expired — behavior depends on whether a WiFi profile exists
        if _wifi_wait >= 600:
            if not wifi_manager.has_saved_wifi_profile():
                # No profile (user deleted WiFi but hasn't configured new one) — keep waiting
                _wifi_wait = 0
                show_setup_screen(epd, config)
            elif os.path.exists(WATCHDOG_SETUP_FLAG):
                # Watchdog-triggered setup with a saved profile — try to reconnect.
                # This handles: router rebooted, temporary outage, WiFi came back.
                logger.info("WiFi setup timeout — attempting reconnect to saved network...")
                wifi_manager.stop_hotspot()
                time.sleep(5)  # Let radio switch from AP to station mode

                # Wait for NetworkManager to auto-reconnect to saved profile
                reconnected = False
                for _ in range(RECONNECT_ATTEMPT_WAIT // 3):
                    time.sleep(3)
                    if _shutdown:
                        return False
                    try:
                        if wifi_manager.is_wifi_connected():
                            reconnected = True
                            break
                    except Exception:
                        pass

                if reconnected:
                    logger.info("WiFi reconnected to saved network — resuming normal operation")
                    _wifi_down_since = 0
                    try:
                        os.remove(WATCHDOG_SETUP_FLAG)
                    except OSError:
                        pass
                    return True
                else:
                    # Still can't connect — restart hotspot and try again later
                    logger.info("Reconnect failed — restarting hotspot for another cycle")
                    wifi_manager.start_hotspot()
                    show_setup_screen(epd, config)
                    _wifi_wait = 0  # Another 10 min in hotspot mode

    return False


def wait_with_polling(seconds, config_check_interval=5):
    """Sleep for `seconds`, checking triggers every 1s and config every 5s.

    While PAUSE_FILE exists, the countdown freezes (stays in loop indefinitely).
    Returns (config, action) where action is 'next', 'prev', 'tcg_changed', or None.
    """
    config = load_config()
    last_config_check = time.time()
    elapsed = 0

    while (elapsed < seconds or os.path.exists(PAUSE_FILE)) and not _shutdown:
        # Check for prev trigger
        if os.path.exists(PREV_TRIGGER):
            try:
                os.remove(PREV_TRIGGER)
            except OSError:
                pass
            logger.info("Previous card trigger detected")
            return load_config(), "prev"

        # Check for collection change trigger
        if os.path.exists(COLLECTION_TRIGGER):
            try:
                os.remove(COLLECTION_TRIGGER)
            except OSError:
                pass
            logger.info("Collection changed trigger detected")
            return load_config(), "collection_changed"

        # Check for skip/next trigger
        if os.path.exists(NEXT_TRIGGER):
            try:
                os.remove(NEXT_TRIGGER)
            except OSError:
                pass
            logger.info("Skip trigger detected — advancing to next card")
            return load_config(), "next"

        # Check for WiFi state change triggers
        if os.path.exists(WIFI_CONNECTED_TRIGGER):
            try:
                os.remove(WIFI_CONNECTED_TRIGGER)
            except OSError:
                pass
            logger.info("WiFi connected trigger detected")
            return load_config(), "wifi_connected"

        if os.path.exists(WIFI_FAILED_TRIGGER):
            ssid = ""
            try:
                with open(WIFI_FAILED_TRIGGER, 'r') as f:
                    ssid = f.read().strip()
                os.remove(WIFI_FAILED_TRIGGER)
            except OSError:
                pass
            logger.info(f"WiFi failed trigger detected for SSID: {ssid}")
            return load_config(), ("wifi_failed", ssid)

        if os.path.exists(WIFI_SETUP_TRIGGER):
            try:
                os.remove(WIFI_SETUP_TRIGGER)
            except OSError:
                pass
            logger.info("WiFi setup mode trigger detected")
            return load_config(), "wifi_setup"

        if os.path.exists(UNBOX_TRIGGER):
            try:
                os.remove(UNBOX_TRIGGER)
            except OSError:
                pass
            logger.info("Unbox screen trigger detected")
            return load_config(), "unbox"

        # Periodically re-read config
        if time.time() - last_config_check >= config_check_interval:
            new_config = load_config()
            if new_config["active_tcg"] != config["active_tcg"]:
                logger.info(f"TCG changed to {new_config['active_tcg']}")
                return new_config, "tcg_changed"
            config = new_config
            last_config_check = time.time()

        # WiFi watchdog: detect sustained WiFi loss and auto-enter hotspot setup mode.
        # This catches: new router, changed password, moved house — any scenario where
        # the saved WiFi profile can no longer connect and the user has no other way
        # to reach the device (no physical buttons, no SSH).
        global _wifi_down_since
        now = time.time()
        if now - getattr(wait_with_polling, '_last_wifi_check', 0) >= WIFI_WATCHDOG_CHECK_INTERVAL:
            wait_with_polling._last_wifi_check = now
            try:
                if wifi_manager.is_wifi_connected():
                    _wifi_down_since = 0  # WiFi is fine — reset watchdog
                    # Clear watchdog flag if it was set from a previous auto-recovery
                    if os.path.exists(WATCHDOG_SETUP_FLAG):
                        try:
                            os.remove(WATCHDOG_SETUP_FLAG)
                        except OSError:
                            pass
                elif _wifi_down_since == 0:
                    _wifi_down_since = now  # First time seeing WiFi down
                    logger.info("WiFi watchdog: connection lost, starting timer")
                elif now - _wifi_down_since >= WIFI_WATCHDOG_TIMEOUT:
                    logger.warning("WiFi watchdog: down for %d min — auto-entering hotspot setup",
                                   int((now - _wifi_down_since) / 60))
                    _wifi_down_since = 0  # Reset so we don't re-trigger immediately
                    wifi_manager.start_hotspot()
                    # Signal web server to enter setup mode
                    try:
                        with open(WATCHDOG_SETUP_FLAG, 'w') as f:
                            f.write('1')
                    except OSError:
                        pass
                    try:
                        with open(WIFI_SETUP_TRIGGER, 'w') as f:
                            f.write('1')
                    except OSError:
                        pass
                    return load_config(), "wifi_setup"
            except Exception as e:
                logger.debug("WiFi watchdog check failed: %s", e)

        time.sleep(1)
        # Only count elapsed time when not paused
        if not os.path.exists(PAUSE_FILE):
            elapsed += 1

    return config, None


def main():
    global _shutdown, _wifi_down_since
    logger.info("InkSlab starting...")

    # Write startup status immediately so web UI shows correct state
    write_status({
        "card_path": "", "set_name": "", "set_info": "",
        "card_num": "", "rarity": "",
        "timestamp": int(time.time()), "tcg": "",
        "total_cards": 0, "pending": "Starting up...",
    })

    config = load_config()
    active_tcg = config["active_tcg"]
    library_dir = TCG_LIBRARIES.get(active_tcg, TCG_LIBRARIES["pokemon"])
    master_index = load_master_index(library_dir)

    # Load collection if collection mode is on
    collection = None
    if config["collection_only"]:
        loaded = load_collection(active_tcg)
        collection = loaded if loaded else set()
        logger.info(f"Collection mode: {len(collection)} owned cards")

    deck = ShuffleDeck(library_dir, collection)
    _deck_collection_only = config["collection_only"]

    # Initialize the e-paper display (retry up to 5 times with increasing delays)
    # Note: we skip Clear() here — the first screen (setup/splash/no-cards) will
    # overwrite the display anyway, saving one unnecessary e-ink refresh flash.
    epd = None
    for attempt in range(1, 6):
        try:
            epd = epd4in0e.EPD()
            epd.init()
            epd.sleep()
            logger.info("Display initialized successfully")

            # Register signal handlers as soon as EPD is initialized, so SIGTERM
            # during WiFi wait loops sets _shutdown instead of killing the process
            def _handle_shutdown(signum, frame):
                global _shutdown
                _shutdown = True

            signal.signal(signal.SIGTERM, _handle_shutdown)
            signal.signal(signal.SIGINT, _handle_shutdown)
            break
        except Exception as e:
            logger.error(f"Display init attempt {attempt}/5 failed: {e}")
            if attempt < 5:
                delay = attempt * 10  # 10s, 20s, 30s, 40s
                logger.info(f"Retrying display init in {delay}s...")
                time.sleep(delay)
            else:
                logger.error("Display init failed after 5 attempts. Exiting.")
                return

    # Check WiFi status — show setup screen or splash screen.
    # IMPORTANT: Use has_saved_wifi_profile() as the primary signal, NOT is_wifi_connected().
    # On Pi Zero, boot WiFi association can take 30-60s. Checking only is_wifi_connected()
    # at startup would falsely show the setup screen even when WiFi is configured, because
    # the association hasn't completed yet. The setup screen tells users to join
    # "InkSlab-Setup" — but inkslab_web.py correctly does NOT start that hotspot when a
    # profile exists, so the phone scan fails. This was the root cause of WiFi setup issues.
    try:
        has_profile = wifi_manager.has_saved_wifi_profile()
        wifi_connected = wifi_manager.is_wifi_connected()
    except Exception as e:
        logger.warning(f"WiFi check failed, assuming connected: {e}")
        has_profile = True
        wifi_connected = True

    # If WiFi is configured but not yet connected (normal on slow Pi Zero boot),
    # wait up to 60 seconds for the association to complete before deciding.
    if has_profile and not wifi_connected:
        logger.info("WiFi profile exists but not yet connected — waiting up to 60s for association...")
        for _ in range(12):  # 12 x 5s = 60s
            time.sleep(5)
            try:
                wifi_connected = wifi_manager.is_wifi_connected()
            except Exception:
                pass
            if wifi_connected:
                logger.info("WiFi connected after waiting.")
                break
        if not wifi_connected:
            logger.warning("WiFi profile exists but did not connect within 60s — showing splash anyway.")
            wifi_connected = True  # Don't show setup screen when a profile is configured
            # Seed WiFi watchdog with 25 minutes already "elapsed" so we only wait
            # ~5 more minutes before auto-entering hotspot mode. The 60s boot wait
            # already proved the network is unreachable — don't make the user stare
            # at an unreachable device for another 30 minutes.
            _wifi_down_since = time.time() - (WIFI_WATCHDOG_TIMEOUT - 300)

    # After WiFi associates, DHCP lease assignment can lag a few seconds behind —
    # especially on a busy first boot (filesystem resize, SSH key gen, etc.).
    # Wait up to 30s for a real IP before proceeding so the splash screen has it.
    if wifi_connected:
        _ip_ready = False
        for _ in range(6):  # 6 x 5s = 30s
            if get_local_ip():
                _ip_ready = True
                break
            time.sleep(5)
        if not _ip_ready:
            logger.warning("WiFi connected but no IP after 30s — splash may show without address")

    # E-ink render time: Spectra 6 (7-color) takes ~30s to physically draw.
    # After that, the user needs time to actually read the screen content.
    EINK_RENDER_TIME = 30   # seconds for e-ink to finish drawing
    EINK_READ_TIME = 45     # extra seconds for user to read the result
    EINK_RENDER_WAIT = EINK_RENDER_TIME + EINK_READ_TIME  # total wait before next write

    if wifi_connected or has_profile:
        # WiFi is working — only show splash if we actually have cards to display.
        # If no cards, skip splash (no-cards screen shows the IP too, avoids extra flash).
        if deck.total > 0:
            had_ip = show_splash_screen(epd, config)
            logger.info(f"Splash screen sent — waiting {EINK_RENDER_WAIT}s for e-ink render...")
            time.sleep(EINK_RENDER_WAIT)
            # If the first splash rendered without an IP (slow DHCP on first boot),
            # try once more — the IP should be available by now after the render wait.
            if not had_ip and get_local_ip():
                logger.info("Re-showing splash — IP now available after render wait")
                show_splash_screen(epd, config)
                time.sleep(EINK_RENDER_WAIT)
        else:
            logger.info("WiFi connected but no cards — skipping splash, will show no-cards screen")
    else:
        # Genuinely no WiFi profile — show setup instructions and wait for trigger file.
        # Do NOT poll is_wifi_connected() here — the hotspot itself registers as "connected".
        show_setup_screen(epd, config)
        logger.info("No WiFi profile found — showing setup screen, waiting for trigger...")
        # No timeout on first boot — the user MUST connect WiFi before
        # anything else can happen.  The hotspot is broadcasting and the
        # display shows the QR code.  We'll wait here until they do.
        while not _shutdown:
            if os.path.exists(WIFI_CONNECTED_TRIGGER):
                try:
                    os.remove(WIFI_CONNECTED_TRIGGER)
                except OSError:
                    pass
                break
            if os.path.exists(WIFI_FAILED_TRIGGER):
                ssid = ""
                try:
                    with open(WIFI_FAILED_TRIGGER, 'r') as f:
                        ssid = f.read().strip()
                    os.remove(WIFI_FAILED_TRIGGER)
                except OSError:
                    pass
                show_wifi_failed_screen(epd, config, ssid=ssid)
            time.sleep(5)
        # WiFi connected — skip splash if no cards (no-cards screen shows IP)
        if deck.total > 0:
            logger.info("WiFi wait complete, showing splash screen...")
            show_splash_screen(epd, config)
            time.sleep(EINK_RENDER_WAIT)
        else:
            logger.info("WiFi connected but no cards — skipping splash")

    def rebuild_deck(preserve_history=False):
        """Helper to rebuild the deck when TCG, collection mode, or collection content changes."""
        nonlocal active_tcg, library_dir, master_index, collection, deck, _deck_collection_only
        old_history = deck.history if preserve_history else []
        active_tcg = config["active_tcg"]
        _deck_collection_only = config["collection_only"]
        library_dir = TCG_LIBRARIES.get(active_tcg, TCG_LIBRARIES["pokemon"])
        master_index = load_master_index(library_dir)
        if config["collection_only"]:
            loaded = load_collection(active_tcg)
            collection = loaded if loaded else set()
        else:
            collection = None
        deck = ShuffleDeck(library_dir, collection, recent=old_history)

    consecutive_failures = 0

    # Main loop: alternates between no-cards screen and card display
    # If cards are deleted mid-operation, falls back to no-cards screen
    try:
        while not _shutdown:

            # No cards available — show welcome screen and wait for downloads
            _no_cards_shown = False
            while deck.total == 0 and not _shutdown:
                if not _no_cards_shown:
                    show_no_cards_screen(epd, config, get_local_ip())
                    _no_cards_shown = True
                logger.warning(f"No cards found for {active_tcg}. Waiting for downloads...")
                err_msg = ("Collection mode is on but no cards are selected. Add cards from the Collection tab."
                           if config["collection_only"] else
                           "No cards downloaded yet. Use the Downloads tab to get started.")
                write_status({
                    "card_path": "", "set_name": "",
                    "set_info": "", "card_num": "", "rarity": "",
                    "timestamp": int(time.time()), "tcg": active_tcg,
                    "total_cards": 0, "error": err_msg,
                })
                config, action = wait_with_polling(60)

                # Handle WiFi state changes even while waiting for cards
                if isinstance(action, tuple) and action[0] == "wifi_failed":
                    show_wifi_failed_screen(epd, config, ssid=action[1])
                    _no_cards_shown = False
                    continue

                if action == "wifi_setup":
                    show_setup_screen(epd, config)
                    _wifi_reconnected = _wifi_setup_wait_loop(epd, config)
                    if _wifi_reconnected:
                        _no_cards_shown = False  # Force re-show with new IP
                    # No splash — the no-cards screen shows IP + QR anyway
                    _no_cards_shown = False
                    continue

                if action == "unbox":
                    show_unbox_screen(epd, config)
                    continue

                if action == "wifi_connected":
                    # Skip splash — no-cards screen will re-show with the new IP
                    _no_cards_shown = False
                    continue

                new_tcg = config["active_tcg"]
                if (new_tcg != active_tcg
                        or config["collection_only"] != _deck_collection_only
                        or action == "collection_changed"):
                    rebuild_deck()
                    _no_cards_shown = False  # Show updated screen if TCG changed
                else:
                    deck.reshuffle()

            if _shutdown:
                break

            # Card display loop — runs until cards run out or shutdown
            while not _shutdown:
                card_path = deck.draw()
                if not card_path:
                    # No card drawn — deck may be empty after deletion
                    rebuild_deck()
                    if deck.total == 0:
                        break  # Back to no-cards loop
                    continue

                # Check if the card file still exists (user may have deleted cards)
                if not os.path.exists(card_path):
                    consecutive_failures += 1
                    if consecutive_failures >= 5:
                        logger.info("Multiple missing card files — rebuilding deck")
                        rebuild_deck()
                        consecutive_failures = 0
                        if deck.total == 0:
                            break  # Back to no-cards loop
                    continue

                logger.info(f"Displaying: {os.path.basename(card_path)}")
                final_img, card_info = process_image(card_path, master_index, config)

                if not final_img:
                    consecutive_failures += 1
                    logger.warning(f"Skipping bad image ({consecutive_failures}): {card_path}")
                    if consecutive_failures >= 10:
                        logger.warning("Too many consecutive bad images. Rebuilding deck...")
                        rebuild_deck()
                        consecutive_failures = 0
                        if deck.total == 0:
                            break  # Back to no-cards loop
                        write_status({
                            "card_path": "", "set_name": "", "card_num": "", "rarity": "",
                            "set_info": "",
                            "timestamp": int(time.time()), "tcg": active_tcg,
                            "total_cards": deck.total,
                            "error": "Some card images may be corrupted. Try re-downloading from the Downloads tab.",
                        })
                        config, action = wait_with_polling(60)
                        if (config["active_tcg"] != active_tcg
                                or config["collection_only"] != _deck_collection_only
                                or action == "collection_changed"):
                            rebuild_deck()
                            if deck.total == 0:
                                break  # Back to no-cards loop
                    else:
                        time.sleep(2)
                    continue

                consecutive_failures = 0

                # Build prev/next card queue for the web dashboard
                prev_cards = []
                for p in deck.history[1:5]:
                    try:
                        prev_cards.append(card_summary(p, master_index))
                    except Exception:
                        pass
                next_cards = []
                for n in deck.peek(5):
                    try:
                        next_cards.append(card_summary(n, master_index))
                    except Exception:
                        pass

                # Calculate wait time — use timezone_name (DST-aware) > timezone_offset > system localtime
                tz_name = config.get("timezone_name")
                tz_offset = config.get("timezone_offset")
                if tz_name:
                    try:
                        from zoneinfo import ZoneInfo
                        from datetime import datetime
                        hr = datetime.now(ZoneInfo(tz_name)).hour
                    except Exception:
                        hr = time.localtime().tm_hour
                elif tz_offset is not None:
                    hr = (time.gmtime().tm_hour + int(tz_offset)) % 24
                else:
                    hr = time.localtime().tm_hour
                wait = config["day_interval"] if config["day_start"] <= hr < config["day_end"] else config["night_interval"]
                paused = os.path.exists(PAUSE_FILE)

                # If paused and skipping to a new card, reset the pause file to the
                # full interval for this card so resuming gives a fresh countdown.
                if paused:
                    try:
                        with open(PAUSE_FILE, 'w') as f:
                            f.write(str(wait))
                    except OSError:
                        pass

                # Write status BEFORE display refresh so web dashboard updates instantly.
                # next_change is 0 here (unknown until render finishes) — set after display.
                cur_set_id = os.path.basename(os.path.dirname(card_path))
                cur_card_id = os.path.splitext(os.path.basename(card_path))[0]
                status_info = {
                    "card_path": card_path,
                    "set_id": cur_set_id,
                    "card_id": cur_card_id,
                    "set_name": card_info.get("set_name", ""),
                    "set_info": card_info.get("set_info", ""),
                    "card_num": card_info.get("card_num", ""),
                    "rarity": card_info.get("rarity", ""),
                    "timestamp": int(time.time()),
                    "tcg": active_tcg,
                    "total_cards": deck.total,
                    "prev_cards": prev_cards,
                    "next_cards": next_cards,
                    "next_change": 0,
                    "paused": paused,
                    "interval": wait,
                    "display_updating": True,
                }
                write_status(status_info)

                try:
                    epd.init()
                    epd.display(epd.getbuffer(final_img))
                except Exception as e:
                    logger.error(f"Display error: {e}")
                finally:
                    try:
                        epd.sleep()
                    except Exception:
                        pass

                # Display fully rendered — now start the countdown from the correct time
                status_info["display_updating"] = False
                next_change = 0 if paused else int(time.time()) + wait
                status_info["next_change"] = next_change
                write_status(status_info)

                logger.info(f"Next card in {wait // 60} minutes")
                final_img.close()

                # Poll during wait — picks up config changes, skip/prev triggers, and pause
                config, action = wait_with_polling(wait)

                if action == "prev":
                    if len(deck.history) > 1:
                        current = deck.history.pop(0)
                        previous = deck.history.pop(0)
                        deck.deck.insert(0, current)
                        deck.deck.insert(0, previous)
                    continue

                # WiFi failed — show error screen, then resume waiting
                if isinstance(action, tuple) and action[0] == "wifi_failed":
                    show_wifi_failed_screen(epd, config, ssid=action[1])
                    time.sleep(EINK_RENDER_WAIT)
                    continue

                # WiFi connected — show splash screen with new IP, then resume cards
                if action == "wifi_connected":
                    logger.info("WiFi connected — showing splash with new IP")
                    show_splash_screen(epd, config)
                    time.sleep(EINK_RENDER_WAIT)
                    continue

                # WiFi setup mode — show setup instructions on display
                if action == "wifi_setup":
                    logger.info("WiFi setup mode — showing setup screen")
                    show_setup_screen(epd, config)
                    _wifi_setup_wait_loop(epd, config)
                    show_splash_screen(epd, config)
                    time.sleep(EINK_RENDER_WAIT)
                    continue

                if action == "unbox":
                    show_unbox_screen(epd, config)
                    continue

                # Collection content changed — rebuild deck but keep showing current card
                if action == "collection_changed" and config["collection_only"]:
                    rebuild_deck(preserve_history=True)
                    if deck.total == 0:
                        break  # Back to no-cards loop
                    new_next = []
                    for nc in deck.peek(5):
                        try:
                            new_next.append(card_summary(nc, master_index))
                        except Exception:
                            pass
                    new_prev = []
                    for pc in deck.history[:4]:
                        try:
                            new_prev.append(card_summary(pc, master_index))
                        except Exception:
                            pass
                    paused = os.path.exists(PAUSE_FILE)
                    remaining = max(0, next_change - int(time.time())) if next_change else wait
                    write_status({
                        "card_path": card_path,
                        "set_name": card_info.get("set_name", ""),
                        "set_info": card_info.get("set_info", ""),
                        "card_num": card_info.get("card_num", ""),
                        "rarity": card_info.get("rarity", ""),
                        "timestamp": int(time.time()),
                        "tcg": active_tcg,
                        "total_cards": deck.total,
                        "prev_cards": new_prev,
                        "next_cards": new_next,
                        "next_change": next_change,
                        "paused": paused,
                        "interval": wait,
                    })
                    logger.info(f"Collection updated — deck rebuilt ({deck.total} cards), queue refreshed")
                    config, action = wait_with_polling(remaining)
                    if action == "prev":
                        if len(deck.history) > 1:
                            current = deck.history.pop(0)
                            previous = deck.history.pop(0)
                            deck.deck.insert(0, current)
                            deck.deck.insert(0, previous)
                        continue
                    # Re-create trigger files for actions that the main loop
                    # handlers (above) would process — prevents silent drops.
                    if action == "wifi_setup":
                        try:
                            with open(WIFI_SETUP_TRIGGER, 'w') as f:
                                f.write('1')
                        except OSError:
                            pass
                    elif action == "wifi_connected":
                        try:
                            with open(WIFI_CONNECTED_TRIGGER, 'w') as f:
                                f.write('1')
                        except OSError:
                            pass
                    elif isinstance(action, tuple) and action[0] == "wifi_failed":
                        try:
                            with open(WIFI_FAILED_TRIGGER, 'w') as f:
                                f.write(action[1])
                        except OSError:
                            pass
                    elif action == "unbox":
                        try:
                            with open(UNBOX_TRIGGER, 'w') as f:
                                f.write('1')
                        except OSError:
                            pass

                # If TCG or collection mode changed, rebuild and advance to new card
                new_tcg = config["active_tcg"]
                needs_rebuild = (new_tcg != active_tcg
                                 or config["collection_only"] != _deck_collection_only
                                 or action == "collection_changed")
                if needs_rebuild:
                    rebuild_deck(preserve_history=(new_tcg == active_tcg))
                    if deck.total == 0:
                        break  # Back to no-cards loop

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        # Always clean up the display on exit
        logger.info("Cleaning up display...")
        try:
            epd.sleep()
        except Exception:
            pass
        logger.info("InkSlab stopped.")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logger.error(f"InkSlab crashed: {e}", exc_info=True)
        sys.exit(1)
