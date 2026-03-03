# InkSlab — e-Ink TCG Card Display

A Raspberry Pi-powered e-ink display that shows your Pokemon and Magic: The Gathering cards in a graded-slab style layout. Control everything from your phone — switch between TCGs, download cards, curate your collection by rarity, and more.

**No command line needed after initial setup.** Everything runs through a clean web dashboard.

**By [Costa Mesa Tech Solutions](https://github.com/costamesatechsolutions)** (a brand of Pine Heights Ventures LLC)

---

## What It Does

- Cycles through TCG cards on a 7-color e-ink display (black, white, red, yellow, blue, green, orange)
- Shows card art in a graded-slab frame with set name, year, card number, and rarity
- **Web Dashboard:** Control everything from your phone or browser at `http://inkslab.local`
- **Live Player Controls:** Pause, play, skip, or go back, complete with an "Up Next" queue and countdown timer
- **Collection Mode & Search:** Only display cards you own. Search for a card (e.g., "Pikachu") and instantly add *all* variations across every set to your collection.
- **Rarity Filtering:** Select or deselect all cards of a specific rarity (e.g., "Mythic Rare" or "Illustration Rare") across every set with one tap
- **Smart Shuffle:** Remembers recently shown cards and pushes them to the back of the deck upon reshuffling so you always see fresh art
- Runs 24/7 as a desk display, rotating cards every 10 minutes (configurable for day/night)

```
+-----------------------+
|  2023 OBSIDIAN FLAMES |
|    #201  *  HOLO      |
| +-------------------+ |
| |                   | |
| |    Card Image     | |
| |                   | |
| |                   | |
| +-------------------+ |
+-----------------------+
```

---

## What You Need

| Part | Notes |
|------|-------|
| **Raspberry Pi Zero W H** | The "H" means headers are pre-soldered (required for the display HAT) |
| **[Waveshare 4" e-Paper HAT+ (E)](https://www.waveshare.com/wiki/4inch_e-Paper_HAT%2B_(E)_Manual)** | Spectra 6 — the 7-color model |
| **Micro SD card** | 32 GB for one TCG, 64 GB for both (Pokemon ~13 GB, MTG ~13 GB) |
| **90-degree micro USB cable** | Optional but recommended — keeps the power cable hidden behind the frame |
| **3D printed frame** | Print files on MakerWorld: **[InkSlab on MakerWorld](https://makerworld.com/en/models/2452200-inkslab-open-source-e-ink-tcg-display)** |

**Assembly:** Attach the e-Paper HAT to the Pi's GPIO header, mount in the frame, route the USB cable out the back, and follow the software setup below.

---

## Setup

### Step 1 — Flash the SD Card

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Choose **Raspberry Pi Zero** > **Raspberry Pi OS (Legacy, 32-bit) Bookworm**
3. Click **Next** > **Edit Settings**:
   - Set hostname to `inkslab`, username to `pi`, pick a password
   - Enter your Wi-Fi name and password
   - Under **Services**, enable SSH
4. Flash, insert the SD card, power on the Pi, and wait ~2 minutes

### Step 2 — SSH In and Install

SSH into your Pi from any terminal:

```bash
ssh pi@inkslab.local
```

> If `inkslab.local` doesn't resolve, check your router for the Pi's IP and use `ssh pi@<IP>` instead.

Then run these commands to install everything:

```bash
# Enable SPI (required for the display)
sudo raspi-config nonint do_spi 0
sudo reboot
```

After reboot, SSH back in and run:

```bash
# Install system packages
sudo apt-get update
sudo apt-get install -y python3-pip python3-pil python3-numpy python3-spidev python3-gpiozero python3-requests python3-flask git unzip

# Install hardware libraries
cd ~
wget http://www.airspayce.com/mikem/bcm2835/bcm2835-1.71.tar.gz
tar zxvf bcm2835-1.71.tar.gz && cd bcm2835-1.71
sudo ./configure && sudo make && sudo make install
cd ~
wget https://github.com/joan2937/lg/archive/master.zip
unzip master.zip && cd lg-master
make && sudo make install
sudo apt install -y gpiod libgpiod-dev

# Install Waveshare driver
cd ~
wget "https://files.waveshare.com/wiki/4inch-e-Paper-HAT%2B-(E)/4inch_e-Paper_E.zip"
unzip 4inch_e-Paper_E.zip -d 4inch_e-Paper_E

# Clone InkSlab
cd ~/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/examples
git clone https://github.com/costamesatechsolutions/inkslab-eink-tcg-display.git
cd inkslab-eink-tcg-display
```

### Step 3 — Start the Services

```bash
sudo cp inkslab.service /etc/systemd/system/
sudo cp inkslab_web.service /etc/systemd/system/
sudo systemctl enable inkslab inkslab_web
sudo systemctl start inkslab inkslab_web
```

That's it. Open **http://inkslab.local** on your phone or computer.

---

## Web Dashboard

Once running, everything is managed from the dashboard at **http://inkslab.local** — no SSH needed.

### Display Tab
- **Live Preview:** See exactly what card is currently on the screen with real-time loading states
- **Player Controls:** iPod-style controls to Pause/Play, skip to the Next card, or go back to Previous cards
- **Queue:** View thumbnail previews of the "Up Next" and "Previously" shown cards
- **Quick Switch:** Instantly toggle between Pokémon and MTG with one tap

### Settings Tab
- Change how often cards rotate (separate day and night intervals to save power)
- Adjust display rotation and color saturation (boost colors for the e-paper display)
- Enable **Collection Only** mode to restrict the display to cards you've marked as owned

### Collection Tab
- Browse every downloaded set and toggle ownership. Tap any card name to view a high-res preview modal
- **Search Cards:** Search for any character or card and instantly add all versions of it to your collection
- **Filter by Rarity:** Pick a rarity from the dropdown (e.g., "Rare Holo", "Mythic Rare") and select/deselect all matching cards across every set at once
- **Set Management:** Select/Deselect an entire set, or use the per-set rarity chips to bulk-manage specific rarities within a single set

### Downloads Tab
- **Smart Storage:** View high-speed, native disk space calculations to see exactly how much SD card space you have left
- **Download Cards:** Pull down Pokémon or MTG cards directly from the dashboard with a live progress log
- **MTG Year Filter:** Magic is massive. Save SD card space by entering a year (e.g., `2020`) to only download MTG sets released from that year onward
- Delete card data with a safety confirmation

---

## Updating

SSH into your Pi and pull the latest code:

```bash
ssh pi@inkslab.local
cd ~/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/examples/inkslab-eink-tcg-display
git pull
sudo systemctl restart inkslab inkslab_web
```

---

## Configuration

All settings are managed from the web dashboard. They're stored in `/home/pi/inkslab_config.json` if you want to edit them directly.

| Setting | Default | Description |
|---------|---------|-------------|
| `active_tcg` | `"pokemon"` | Which TCG to display |
| `rotation_angle` | `270` | Display rotation (0/90/180/270) |
| `day_interval` | `600` (10 min) | Seconds between cards during the day |
| `night_interval` | `3600` (1 hr) | Seconds between cards at night |
| `day_start` / `day_end` | `7` / `23` | Day mode hours (24h format) |
| `color_saturation` | `2.5` | Color boost for e-paper (higher = more vivid) |
| `collection_only` | `false` | Only show cards marked as owned |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `inkslab.local` doesn't work | Some routers/Android devices don't support `.local`. Use the Pi's IP address instead — find it in your router admin page or run `hostname -I` on the Pi. |
| Display not updating | Check SPI is enabled: `ls /dev/spi*` should show devices. Check logs: `journalctl -u inkslab -f` |
| Washed-out colors | Increase **Color Saturation** in the Settings tab (default 2.5, try 3.0–4.0) |
| Web dashboard not loading | Run `journalctl -u inkslab_web -f` to check for errors |
| Collection mode shows nothing | Mark some cards as owned in the Collection tab first |
| Download fails or stalls | The Pi Zero has limited RAM. If a massive download (MTG or Pokémon) stalls out, click "Stop Download" and then start it again. It will safely skip over existing files and resume exactly where it left off. |

---

## Project Structure

```
inkslab-eink-tcg-display/
  inkslab.py                     # Display daemon
  inkslab_web.py                 # Web dashboard (Flask)
  inkslab.service                # systemd service for display
  inkslab_web.service            # systemd service for web dashboard
  lib/waveshare_epd/             # e-Paper display driver (bundled)
  scripts/
    download_cards_pokemon.py    # Pokemon card downloader
    download_cards_mtg.py        # MTG card downloader (Scryfall API)
```

## Credits

- Pokemon card data: [PokemonTCG/pokemon-tcg-data](https://github.com/PokemonTCG/pokemon-tcg-data) (open data)
- MTG card data: [Scryfall](https://scryfall.com/) (free API)
- Display driver: [Waveshare e-Paper](https://github.com/waveshare/e-Paper) (MIT License)

## License

AGPL-3.0 — see [LICENSE](LICENSE)
