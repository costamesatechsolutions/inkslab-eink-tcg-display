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
import gc
import logging
from PIL import Image, ImageEnhance, ImageDraw, ImageFont, ImageOps

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
}

TCG_LIBRARIES = {
    "pokemon": "/home/pi/pokemon_cards",
    "mtg": "/home/pi/mtg_cards",
}

CONFIG_FILE = "/home/pi/inkslab_config.json"
COLLECTION_FILE = "/home/pi/inkslab_collection.json"
STATUS_FILE = "/tmp/inkslab_status.json"
NEXT_TRIGGER = "/tmp/inkslab_next"

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

from waveshare_epd import epd4in0e


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
    """Load the master set index from a library directory."""
    index_file = os.path.join(library_dir, "master_index.json")
    if os.path.exists(index_file):
        try:
            with open(index_file, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def write_status(info):
    """Write current display status for the web dashboard."""
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump(info, f)
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
            year = master_index[set_id]["year"]
            real_set = master_index[set_id]["name"].upper().replace(" AND ", " & ")
            info["set_info"] = f"{year} {real_set}"
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


def create_slab_layout(img_path, master_index, color_saturation):
    """Create a PSA-slab-style layout with card info header above the card image."""
    info = get_card_metadata(img_path, master_index)

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

        return canvas, info


def create_palette_image():
    """Create a reference palette image for Floyd-Steinberg dithering."""
    p_img = Image.new('P', (1, 1))
    full_palette = PALETTE_COLORS + [0, 0, 0] * (256 - len(PALETTE_COLORS) // 3)
    p_img.putpalette(full_palette)
    return p_img


def process_image(img_path, master_index, config):
    """Full image pipeline: layout -> enhance -> dither -> rotate for display."""
    try:
        img, info = create_slab_layout(img_path, master_index, config["color_saturation"])

        # Boost colors for the e-paper display
        img = ImageEnhance.Color(img).enhance(config["color_saturation"])
        img = ImageEnhance.Contrast(img).enhance(CONTRAST_BOOST)
        img = ImageEnhance.Sharpness(img).enhance(SHARPNESS_BOOST)

        # Quantize to 7-color palette with dithering
        palette_ref = create_palette_image()
        img_dithered = img.quantize(palette=palette_ref, dither=Image.Dither.FLOYDSTEINBERG)

        return img_dithered.convert("RGB").rotate(config["rotation_angle"], expand=True), info
    except Exception as e:
        logger.error(f"Image processing error: {e}")
        return None, {}


class ShuffleDeck:
    """Manages a shuffled deck of all card image paths. Re-shuffles when exhausted."""

    def __init__(self, root_dir, collection=None):
        self.root_dir = root_dir
        self.collection = collection
        self.deck = []
        self.total = 0
        self.reshuffle()

    def reshuffle(self):
        logger.info("Shuffling deck...")
        temp = []
        if os.path.isdir(self.root_dir):
            for root, dirs, files in os.walk(self.root_dir):
                for f in files:
                    if f.endswith(".png") and not f.startswith("_"):
                        # If collection mode, only include owned cards
                        if self.collection:
                            card_id = os.path.splitext(f)[0]
                            if card_id not in self.collection:
                                continue
                        temp.append(os.path.join(root, f))

        # Auto-fallback: if collection mode found 0 matching cards, show all
        if self.collection is not None and len(temp) == 0:
            logger.warning("Collection mode active but no matching cards on disk — showing all cards")
            self.collection = None
            if os.path.isdir(self.root_dir):
                for root, dirs, files in os.walk(self.root_dir):
                    for f in files:
                        if f.endswith(".png") and not f.startswith("_"):
                            temp.append(os.path.join(root, f))

        random.shuffle(temp)
        self.deck = temp
        self.total = len(temp)
        logger.info(f"Deck loaded: {self.total} cards")

    def draw(self):
        if not self.deck:
            self.reshuffle()
        if not self.deck:
            return None
        return self.deck.pop(0)


def wait_with_polling(seconds, config_check_interval=30):
    """Sleep for `seconds`, but check for next-card trigger every 1s and config every 30s."""
    config = load_config()
    last_config_check = time.time()

    for _ in range(seconds):
        # Check for skip trigger
        if os.path.exists(NEXT_TRIGGER):
            try:
                os.remove(NEXT_TRIGGER)
            except OSError:
                pass
            logger.info("Skip trigger detected — advancing to next card")
            return load_config(), True  # config, tcg_changed

        # Periodically re-read config
        if time.time() - last_config_check >= config_check_interval:
            new_config = load_config()
            tcg_changed = new_config["active_tcg"] != config["active_tcg"]
            if tcg_changed:
                logger.info(f"TCG changed to {new_config['active_tcg']}")
                return new_config, True
            config = new_config
            last_config_check = time.time()

        time.sleep(1)

    return config, False


def main():
    logger.info("InkSlab starting...")

    config = load_config()
    active_tcg = config["active_tcg"]
    library_dir = TCG_LIBRARIES.get(active_tcg, TCG_LIBRARIES["pokemon"])
    master_index = load_master_index(library_dir)

    # Load collection if collection mode is on
    collection = None
    if config["collection_only"]:
        collection = load_collection(active_tcg)
        if collection:
            logger.info(f"Collection mode: {len(collection)} owned cards")
        else:
            logger.info("Collection mode on but no cards marked — showing all")
            collection = None

    deck = ShuffleDeck(library_dir, collection)

    # If no cards available, wait and poll for config changes or new downloads
    while deck.total == 0:
        logger.warning(f"No cards found for {active_tcg} in {library_dir}. "
                       f"Waiting for cards to be downloaded or TCG to be changed...")
        write_status({
            "card_path": "",
            "set_name": "",
            "set_info": f"No {active_tcg.upper()} cards downloaded",
            "card_num": "",
            "rarity": "",
            "timestamp": int(time.time()),
            "tcg": active_tcg,
            "total_cards": 0,
            "error": f"No {active_tcg.upper()} cards found. Download cards from the web dashboard.",
        })
        config, _ = wait_with_polling(60)
        new_tcg = config["active_tcg"]
        if new_tcg != active_tcg:
            active_tcg = new_tcg
            library_dir = TCG_LIBRARIES.get(active_tcg, TCG_LIBRARIES["pokemon"])
            master_index = load_master_index(library_dir)
            collection = None
            if config["collection_only"]:
                collection = load_collection(active_tcg)
                if not collection:
                    collection = None
            deck = ShuffleDeck(library_dir, collection)
        else:
            # Same TCG — reshuffle in case cards were just downloaded
            deck.reshuffle()

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
        if not card_path:
            logger.warning(f"No cards available for {active_tcg}. Checking for changes...")
            write_status({
                "card_path": "",
                "set_name": "",
                "set_info": f"No {active_tcg.upper()} cards available",
                "card_num": "",
                "rarity": "",
                "timestamp": int(time.time()),
                "tcg": active_tcg,
                "total_cards": 0,
                "error": f"No {active_tcg.upper()} cards found. Download cards or switch TCG.",
            })
            config, _ = wait_with_polling(60)
            new_tcg = config["active_tcg"]
            if new_tcg != active_tcg:
                active_tcg = new_tcg
                library_dir = TCG_LIBRARIES.get(active_tcg, TCG_LIBRARIES["pokemon"])
                master_index = load_master_index(library_dir)
                collection = None
                if config["collection_only"]:
                    collection = load_collection(active_tcg)
                    if not collection:
                        collection = None
                deck = ShuffleDeck(library_dir, collection)
            else:
                # Same TCG — reshuffle in case cards were just downloaded
                deck.reshuffle()
            continue

        logger.info(f"Displaying: {os.path.basename(card_path)}")
        final_img, card_info = process_image(card_path, master_index, config)

        if final_img:
            try:
                epd.init()
                epd.display(epd.getbuffer(final_img))
                epd.sleep()
            except Exception as e:
                logger.error(f"Display error: {e}")

            # Write status for web dashboard
            write_status({
                "card_path": card_path,
                "set_name": card_info.get("set_name", ""),
                "set_info": card_info.get("set_info", ""),
                "card_num": card_info.get("card_num", ""),
                "rarity": card_info.get("rarity", ""),
                "timestamp": int(time.time()),
                "tcg": active_tcg,
                "total_cards": deck.total,
            })

            # Day mode: rotate every 10 min | Night mode: every hour
            hr = time.localtime().tm_hour
            wait = config["day_interval"] if config["day_start"] <= hr < config["day_end"] else config["night_interval"]
            logger.info(f"Next card in {wait // 60} minutes")

            # Poll during wait — picks up config changes and skip triggers
            config, needs_reshuffle = wait_with_polling(wait)

            # If TCG changed or collection settings changed, rebuild the deck
            new_tcg = config["active_tcg"]
            if new_tcg != active_tcg or needs_reshuffle:
                active_tcg = new_tcg
                library_dir = TCG_LIBRARIES.get(active_tcg, TCG_LIBRARIES["pokemon"])
                master_index = load_master_index(library_dir)
                collection = None
                if config["collection_only"]:
                    collection = load_collection(active_tcg)
                    if not collection:
                        collection = None
                deck = ShuffleDeck(library_dir, collection)
                if deck.total == 0:
                    logger.warning(f"Switched to {active_tcg} but no cards found. "
                                   f"Will wait for download or TCG change.")

            del final_img
            gc.collect()
        else:
            logger.warning(f"Skipping bad image: {card_path}")
            time.sleep(5)


if __name__ == '__main__':
    main()
