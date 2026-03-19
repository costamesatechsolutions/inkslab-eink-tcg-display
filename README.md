# InkSlab — e-Ink TCG Card Display

A Raspberry Pi-powered e-ink display that shows your Pokemon, Magic: The Gathering, and Disney Lorcana cards in a graded-slab style layout. Upload your own custom images too. Control everything from your phone — switch between TCGs, download cards, curate your collection by rarity, and more.

**No command line needed.** Pre-flashed units have built-in WiFi setup — just power on, connect to the InkSlab network, and pick your WiFi. Everything else runs through a clean web dashboard — including software updates.

**By [Costa Mesa Tech Solutions](https://github.com/costamesatechsolutions)** (a brand of Pine Heights Ventures LLC)

---

## What It Does

- Cycles through TCG cards on a 7-color e-ink display (black, white, red, yellow, blue, green, orange)
- Shows card art in a graded-slab frame with set name, year, card number, and rarity
- **Slab Header Modes:** Normal (white bg), Inverted (black bg), or Off (full-screen card art)
- **Web Dashboard:** Control everything from your phone or browser at `http://<your-pi-ip>`
- **Live Player Controls:** Pause, play, skip, or go back, complete with an "Up Next" queue and countdown timer
- **Collection Mode & Search:** Only display cards you own. Search for a card (e.g., "Pikachu") and instantly add *all* variations across every set to your collection.
- **Rarity Filtering:** Select or deselect all cards of a specific rarity (e.g., "Mythic Rare" or "Illustration Rare") across every set with one tap
- **Smart Shuffle:** Remembers recently shown cards and pushes them to the back of the deck upon reshuffling so you always see fresh art
- **Custom Images:** Upload your own images and organize them into sets with optional metadata
- **WiFi Setup Mode:** Pre-flashed units automatically create an "InkSlab-Setup" WiFi network on first boot. Connect with your phone, pick your home WiFi, and you're done — no SSH needed
- **OTA Updates:** Update InkSlab software directly from the web dashboard — no SSH needed
- **Startup Splash:** On boot, the display shows your Pi's IP address so you know exactly where to connect — no SSH or router lookup needed
- **WiFi Auto-Recovery:** If your WiFi goes down for 30+ minutes (router swap, password change, etc.), InkSlab automatically creates the setup hotspot so you can reconfigure. If your old WiFi comes back, it reconnects on its own — no action needed
- **Self-Healing:** A boot-time script verifies all critical files and auto-repairs from git if anything is corrupted. Designed for years of unattended operation
- Runs 24/7 as a desk display, rotating cards every 10 minutes (configurable for day/night)

### Supported TCGs
- **Pokemon** — via [PokemonTCG data](https://github.com/PokemonTCG/pokemon-tcg-data)
- **Magic: The Gathering** — via [Scryfall API](https://scryfall.com/)
- **Disney Lorcana** — via [Lorcast API](https://lorcast.com/)
- **Custom** — upload your own PNG/JPG images

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
| **Micro SD card** | 32 GB for one TCG, 64 GB+ for all three (Pokemon ~13 GB, MTG ~13 GB, Lorcana ~2 GB) |
| **90-degree micro USB cable** | Optional but recommended — keeps the power cable hidden behind the frame |
| **3D printed frame** | Print files on MakerWorld: **[InkSlab on MakerWorld](https://makerworld.com/en/models/2452200-inkslab-open-source-e-ink-tcg-display)** |

**Assembly:** Attach the e-Paper HAT to the Pi's GPIO header, mount in the frame, route the USB cable out the back, and follow the software setup below.

---

## Setup

### Pre-Flashed Units (Easiest)

If you received a pre-flashed InkSlab, setup takes about 30 seconds:

1. **Power on** the InkSlab — wait about 90 seconds for the e-ink display to show setup instructions
2. On your phone, go to **Settings > WiFi** and connect to `InkSlab-Setup` (no password needed)
3. A setup page should appear automatically. If not, open `http://10.42.0.1` in your web browser (Safari, Chrome, etc.) — or scan the QR code on the display
4. **Pick your home WiFi** from the list, enter the password, and tap Connect
5. The display will show your new dashboard address (e.g., `http://192.168.1.42`) with a scannable QR code
6. **Reconnect your phone** to your home WiFi and open that address in your web browser — you're done!

To change WiFi later, go to **Settings** > **Change WiFi Network** in the dashboard.

---

### DIY Setup (Flash Your Own SD Card)

### Step 1 — Flash the SD Card

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Choose **Raspberry Pi Zero** > **Raspberry Pi OS (Legacy, 32-bit) Lite** — this is the Bookworm-based version with no desktop environment
3. Click **Next** > **Edit Settings**:
   - Set hostname to `inkslab`, username to `pi`, pick a password
   - Enter your Wi-Fi name and password
   - Under **Services**, enable SSH
4. Flash, insert the SD card, power on the Pi, and wait ~2 minutes

### Step 2 — SSH In and Install

SSH into your Pi from any terminal (find the IP from your router's admin page):

```bash
ssh pi@<your-pi-ip>
```

Then enable SPI (required for the e-ink display) and reboot:

```bash
sudo raspi-config nonint do_spi 0
sudo reboot
```

After reboot, SSH back in and run:

```bash
# Install all required packages
sudo apt-get update
sudo apt-get install -y python3-pil python3-numpy python3-spidev python3-gpiozero python3-lgpio python3-requests python3-flask python3-qrcode git

# Clone InkSlab
git clone https://github.com/costamesatechsolutions/inkslab-eink-tcg-display.git ~/inkslab
cd ~/inkslab
```

### Step 3 — Run Setup

```bash
sudo bash ~/inkslab/scripts/setup.sh
```

This installs dependencies, configures the services, and reboots. Your SSH session will disconnect — this is normal.

After reboot, InkSlab will either:
- **Show the WiFi setup screen** if no WiFi is saved — connect your phone to the `InkSlab-Setup` network and follow the on-screen instructions
- **Show a splash screen with your dashboard URL** (e.g., `http://192.168.1.42`) if WiFi is already configured

The Pi Zero W takes **1–2 minutes** to fully boot and display the first screen. Be patient — the e-ink display won't update until the services are ready.

> **Tip:** If buttons or controls on the dashboard ever stop responding, first **close any extra tabs** — having multiple dashboard tabs open is the most common cause. Then try opening a fresh tab or using Ctrl+Shift+R (Win) / Cmd+Shift+R (Mac) to force-clear the cache. On mobile, try private/incognito mode.

---

## Web Dashboard

Once running, everything is managed from the web dashboard — no SSH needed. The IP address is shown on the e-ink display at boot and in the dashboard footer.

### Display Tab
- **Live Preview:** See exactly what card is currently on the screen with real-time loading states
- **Player Controls:** iPod-style controls to Pause/Play, skip to the Next card, or go back to Previous cards
- **Queue:** View thumbnail previews of the "Up Next" and "Previously" shown cards
- **Quick Switch:** Instantly toggle between Pokemon, MTG, Lorcana, or Custom with one tap

### Settings Tab
- **Active TCG:** Switch between Pokemon, MTG, Lorcana, or Custom
- **Slab Header Mode:** Choose between Normal (white background), Inverted (black background), or Off (full-screen card art with no header)
- Change how often cards rotate (separate day and night intervals to save power)
- Adjust display rotation and color saturation (boost colors for the e-paper display)
- Enable **Collection Only** mode to restrict the display to cards you've marked as owned
- **Software Update:** Check for and install OTA updates directly from the web dashboard
- **WiFi Network:** View current connection status and change WiFi networks without SSH

### Collection Tab
- Browse every downloaded set and toggle ownership. Tap any card name to view a high-res preview modal
- **Search Cards:** Search for any character or card and instantly add all versions of it to your collection
- **Filter by Rarity:** Pick a rarity from the dropdown (e.g., "Rare Holo", "Mythic Rare", "Enchanted") and select/deselect all matching cards across every set at once
- **Set Management:** Select/Deselect an entire set, or use the per-set rarity chips to bulk-manage specific rarities within a single set

### Downloads Tab
- **Smart Storage:** View high-speed, native disk space calculations to see exactly how much SD card space you have left
- **Download Cards:** Pull down Pokemon, MTG, or Lorcana cards directly from the dashboard with a live progress log
- **MTG Year Filter:** Magic is massive. Save SD card space by entering a year (e.g., `2020`) to only download MTG sets released from that year onward
- **Custom Images:** Create folders, upload your own PNG/JPG images, edit card metadata (name, number, rarity), rename or delete sets
- Delete card data with a safety confirmation

---

## Updating

### From the Web Dashboard (Recommended)
1. Go to **Settings** tab
2. Click **Check for Updates**
3. If updates are available, click **Update Now**
4. The page will automatically reconnect after the services restart

### Via SSH
```bash
ssh pi@<your-pi-ip>
cd ~/inkslab
sudo git pull
sudo cp ~/inkslab/inkslab.service ~/inkslab/inkslab_web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart inkslab inkslab_web
```

---

## Custom Images

Upload your own images to display on the InkSlab.

### How It Works
- Go to the **Downloads** tab and find the **Custom Images** section
- **Create a folder** — each folder is a "set" (e.g., "Favorites", "My Art", "Proxies")
- **Upload images** — PNG or JPG, any aspect ratio (will be auto-scaled)
- **Edit metadata** — optionally set a name, number, and rarity for each card so the slab header looks right
- **Switch to Custom** — use Quick Switch or Settings to display your custom images
- Multiple folders supported — organize by theme, artist, or whatever you like

### Folder Structure on Disk
```
/home/pi/custom_cards/
  master_index.json          # Set names (auto-generated)
  my_favorites/
    _data.json               # Card metadata (auto-generated from filenames)
    cool_dragon.jpg
    awesome_wizard.png
  proxies/
    _data.json
    black_lotus.png
```

---

## Configuration

All settings are managed from the web dashboard. They're stored in `/home/pi/inkslab_config.json` if you want to edit them directly.

| Setting | Default | Description |
|---------|---------|-------------|
| `active_tcg` | `"pokemon"` | Which TCG to display (`pokemon`, `mtg`, `lorcana`, `custom`) |
| `slab_header_mode` | `"normal"` | Slab header style: `"normal"`, `"inverted"`, or `"off"` |
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
| Can't find the dashboard | The IP is shown on the e-ink display at boot. If you missed it, run `sudo systemctl restart inkslab` to show the splash screen again, or run `hostname -I` via SSH. You can also check your router's admin page. |
| SSH disconnected after starting services | Normal — the Pi reboots or enters WiFi setup mode. Wait 1–2 minutes, then reconnect. |
| Display not updating | The Pi Zero takes 1–2 minutes to boot. If still stuck after 3 minutes, check SPI is enabled (`ls /dev/spi*`) and service status (`sudo systemctl status inkslab`). |
| Washed-out colors | Increase **Color Saturation** in the Settings tab (default 2.5, try 3.0–4.0) |
| Web dashboard not loading | Run `journalctl -u inkslab_web -f` to check for errors |
| Web dashboard not loading (restart) | Run `sudo systemctl restart inkslab_web` to restart the dashboard service. If that doesn't help, `sudo systemctl restart inkslab inkslab_web` restarts both. |
| Can't find the IP address | Check your router's admin page, or SSH in and run `hostname -I`. You can also restart the `inkslab` service to redisplay the splash screen: `sudo systemctl restart inkslab` |
| Collection mode shows nothing | Mark some cards as owned in the Collection tab first |
| Services show "masked" | Run: `sudo bash ~/inkslab/scripts/setup.sh` (this removes masked symlinks, installs fresh service files, and reboots) |
| Download fails or stalls | The Pi Zero has limited RAM. If a massive download (MTG or Pokemon) stalls out, click "Stop Download" and then start it again. It will safely skip over existing files and resume exactly where it left off. |
| OTA update stuck | If the update progress bar stalls, wait 60 seconds then refresh the page. The services auto-restart via systemd. |
| WiFi setup not appearing | Make sure you're connected to the `InkSlab-Setup` network. If the setup page doesn't auto-open, go to `http://10.42.0.1` manually. |
| Wrong WiFi password | The setup page will show an error and let you retry. The InkSlab-Setup network will reappear automatically. |
| Want to change WiFi | Go to **Settings** > **Change WiFi Network** in the dashboard. The InkSlab will re-enter setup mode. |
| Got a new router / changed WiFi password | InkSlab will automatically detect the lost connection and re-enter setup mode within ~30 minutes (or ~5 minutes after a reboot). The display will show the setup QR code again. Connect to `InkSlab-Setup` and enter your new WiFi details. |
| WiFi went out temporarily | No action needed — InkSlab continues showing cards offline and auto-reconnects when your WiFi comes back. |
| Buttons not responding | **First: close any extra tabs** — having multiple dashboard tabs open is the most common cause. Then try opening a fresh tab and navigating to the same address. Or try Ctrl+Shift+R (Win) / Cmd+Shift+R (Mac) to force-clear the cache. On mobile, open a new tab or use private/incognito. |

---

## Project Structure

```
inkslab-eink-tcg-display/
  inkslab.py                     # Display daemon
  inkslab_web.py                 # Web dashboard (Flask)
  wifi_manager.py                # WiFi setup mode (nmcli wrapper)
  requirements.txt               # Python dependencies
  inkslab.service                # systemd service for display
  inkslab_web.service            # systemd service for web dashboard
  lib/waveshare_epd/             # e-Paper display driver (bundled)
  scripts/
    download_cards_pokemon.py    # Pokemon card downloader
    download_cards_mtg.py        # MTG card downloader (Scryfall API)
    download_cards_lorcana.py    # Lorcana card downloader (Lorcast API)
    setup.sh                     # First-time setup (installs services + reboots)
    ota_update.sh                # OTA update script (git pull + service restart)
    selfheal.sh                  # Self-healer: runs before each service start, fixes broken states
```

## Credits

- Pokemon card data: [PokemonTCG/pokemon-tcg-data](https://github.com/PokemonTCG/pokemon-tcg-data) (open data)
- MTG card data: [Scryfall](https://scryfall.com/) (free API)
- Lorcana card data: [Lorcast](https://lorcast.com/) (free API)
- Display driver: [Waveshare e-Paper](https://github.com/waveshare/e-Paper) (MIT License)

## License

AGPL-3.0 — see [LICENSE](LICENSE)


## Star History

<a href="https://www.star-history.com/?repos=costamesatechsolutions%2Finkslab-eink-tcg-display&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=costamesatechsolutions/inkslab-eink-tcg-display&type=date&theme=dark&legend=bottom-right" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=costamesatechsolutions/inkslab-eink-tcg-display&type=date&legend=bottom-right" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=costamesatechsolutions/inkslab-eink-tcg-display&type=date&legend=bottom-right" />
 </picture>
</a>
