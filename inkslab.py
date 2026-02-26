#!/usr/bin/python3
# -*- coding:utf-8 -*-
"""
InkSlab - e-Ink TCG Card Display
https://github.com/costamesatechsolutions/inkslab-eink-tcg-display

Displays random TCG cards on a Waveshare 4" e-Paper (E) / Spectra 6 color display
in a graded-slab-style layout with set name, year, card number, and market price.
Cards rotate every 10 minutes during the day (7am-11pm) and every hour at night.

By Costa Mesa Tech Solutions (a brand of Pine Heights Ventures LLC)
"""

import sys
import os
import time
import random
import json
import gc
import logging
from PIL import Image, ImageEnhance, ImageDraw, ImageFont, ImageOps

# --- CONFIGURATION ---
LIBRARY_DIR = "/home/pi/pokemon_cards"
INDEX_FILE = os.path.join(LIBRARY_DIR, "master_index.json")
ROTATION_ANGLE = 270  # Rotate for display orientation

# Display timing (seconds)
DAY_INTERVAL = 600    # 10 minutes during daytime
NIGHT_INTERVAL = 3600 # 1 hour at night
DAY_START = 7         # 7:00 AM
DAY_END = 23          # 11:00 PM

# Image processing
DISPLAY_WIDTH = 400
DISPLAY_HEIGHT = 600
COLOR_SATURATION = 2.5
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

from waveshare_epd import epd4in0e

# --- LOAD MASTER INDEX ---
MASTER_INDEX = {}
if os.path.exists(INDEX_FILE):
    with open(INDEX_FILE, 'r') as f:
        MASTER_INDEX = json.load(f)
    logger.info(f"Loaded master index with {len(MASTER_INDEX)} sets")
else:
    logger.warning(f"Master index not found at {INDEX_FILE}")


def get_card_metadata(img_path):
    """Extract set name, card number, price/rarity from card image path."""
    info = {"set_info": "", "stats": ""}
    try:
        folder_path = os.path.dirname(img_path)
        filename = os.path.basename(img_path)
        set_id = os.path.basename(folder_path)
        card_id = os.path.splitext(filename)[0]

        # Set info (top line)
        if set_id in MASTER_INDEX:
            year = MASTER_INDEX[set_id]["year"]
            real_set = MASTER_INDEX[set_id]["name"].upper().replace(" AND ", " & ")
            info["set_info"] = f"{year} {real_set}"
        else:
            info["set_info"] = set_id.upper()

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

                    # Price takes priority, fall back to rarity
                    if entry.get("price"):
                        extra = entry["price"]
                    elif entry.get("rarity"):
                        extra = entry["rarity"].upper()
                        extra = extra.replace("RARE HOLO", "HOLO").replace("DOUBLE RARE", "DBL RARE")

        # Try extracting number from card ID if not found in metadata
        if num == "00" and "-" in card_id:
            parts = card_id.split("-")
            if parts[-1].isdigit():
                num = parts[-1]

        # Format: "#201  *  $45.00" or just "#201"
        if extra:
            info["stats"] = f"#{num}  \u2022  {extra}"
        else:
            info["stats"] = f"#{num}"

    except Exception as e:
        logger.debug(f"Metadata error for {img_path}: {e}")
    return info


def create_slab_layout(img_path):
    """Create a PSA-slab-style layout with card info header above the card image."""
    info = get_card_metadata(img_path)

    with Image.open(img_path) as card:
        card = card.convert("RGB")

        # Scale card to fill display width
        aspect = card.height / card.width
        new_h = int(DISPLAY_WIDTH * aspect)
        card = card.resize((DISPLAY_WIDTH, new_h), Image.Resampling.LANCZOS)

        # White canvas
        canvas = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), (255, 255, 255))
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

            draw.text(((DISPLAY_WIDTH - w1) / 2, start_y), line1, font=font_set, fill=(0, 0, 0))
            draw.text(((DISPLAY_WIDTH - w2) / 2, start_y + h1 + gap), line2, font=font_stats, fill=(0, 0, 0))

        return canvas


def create_palette_image():
    """Create a reference palette image for Floyd-Steinberg dithering."""
    p_img = Image.new('P', (1, 1))
    full_palette = PALETTE_COLORS + [0, 0, 0] * (256 - len(PALETTE_COLORS) // 3)
    p_img.putpalette(full_palette)
    return p_img


def process_image(img_path):
    """Full image pipeline: layout -> enhance -> dither -> rotate for display."""
    try:
        img = create_slab_layout(img_path)

        # Boost colors for the e-paper display
        img = ImageEnhance.Color(img).enhance(COLOR_SATURATION)
        img = ImageEnhance.Contrast(img).enhance(CONTRAST_BOOST)
        img = ImageEnhance.Sharpness(img).enhance(SHARPNESS_BOOST)

        # Quantize to 7-color palette with dithering
        palette_ref = create_palette_image()
        img_dithered = img.quantize(palette=palette_ref, dither=Image.Dither.FLOYDSTEINBERG)

        return img_dithered.convert("RGB").rotate(ROTATION_ANGLE, expand=True)
    except Exception as e:
        logger.error(f"Image processing error: {e}")
        return None


class ShuffleDeck:
    """Manages a shuffled deck of all card image paths. Re-shuffles when exhausted."""

    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.deck = []
        self.reshuffle()

    def reshuffle(self):
        logger.info("Shuffling deck...")
        temp = []
        for root, dirs, files in os.walk(self.root_dir):
            for f in files:
                if f.endswith(".png") and not f.startswith("_"):
                    temp.append(os.path.join(root, f))
        random.shuffle(temp)
        self.deck = temp
        logger.info(f"Deck loaded: {len(self.deck)} cards")

    def draw(self):
        if not self.deck:
            self.reshuffle()
        return self.deck.pop(0)


def main():
    logger.info("InkSlab starting...")
    deck = ShuffleDeck(LIBRARY_DIR)

    try:
        epd = epd4in0e.EPD()
        epd.init()
        epd.Clear()
        logger.info("Display initialized and cleared")
    except Exception as e:
        logger.error(f"Display init failed: {e}")
        return

    while True:
        card_path = deck.draw()
        logger.info(f"Displaying: {os.path.basename(card_path)}")
        final_img = process_image(card_path)

        if final_img:
            try:
                epd.init()
                epd.display(epd.getbuffer(final_img))
                epd.sleep()
            except Exception as e:
                logger.error(f"Display error: {e}")

            # Day mode: rotate every 10 min | Night mode: every hour
            hr = time.localtime().tm_hour
            wait = DAY_INTERVAL if DAY_START <= hr < DAY_END else NIGHT_INTERVAL
            logger.info(f"Next card in {wait // 60} minutes")
            time.sleep(wait)

            del final_img
            gc.collect()
        else:
            logger.warning(f"Skipping bad image: {card_path}")
            time.sleep(5)


if __name__ == '__main__':
    main()
