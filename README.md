# InkSlab — e-Ink TCG Card Display

A Raspberry Pi-powered e-ink display that shows your Pokemon, Magic: The Gathering, and Disney Lorcana cards in a graded-slab style layout. Upload your own custom images too. Control everything from your phone — switch between TCGs, download cards, curate your card list by rarity, and more.

**No command line needed.** Pre-flashed units have built-in WiFi setup — just power on, connect to the InkSlab network, and pick your WiFi. Everything else runs through a clean web dashboard — including software updates.

**By [Costa Mesa Tech Solutions](https://github.com/costamesatechsolutions)** (a brand of Pine Heights Ventures LLC)

---

## What It Does

- Cycles through TCG cards on a 7-color e-ink display (black, white, red, yellow, blue, green, orange)
- Shows card art in a graded-slab frame with set name, year, card number, and rarity
- **Slab Header Modes:** Normal (white bg), Inverted (black bg), or Off (full-screen card art)
- **Web Dashboard:** Control everything from your phone or browser at `http://<your-pi-ip>`
- **Live Player Controls:** Pause, play, skip, or go back, complete with an "Up Next" queue and countdown timer
- **My Cards & Search:** Curate your own card list — favorites, a wish list, cards you collect, or any theme you like. Search for a card (e.g., "Pikachu") and instantly add *all* variations across every set.
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

1. **Power on** the InkSlab — wait 1–2 minutes for the e-ink display to show setup instructions
2. On your phone, go to **Settings > WiFi** and connect to `InkSlab-Setup` (no password needed)
3. A setup page should appear automatically. If not, open `http://10.42.0.1` in your web browser (Safari, Chrome, etc.) — or scan the QR code on the display
4. **Pick your home WiFi** from the list, enter the password, and tap Connect
5. The display will show your new dashboard address (e.g., `http://192.168.1.42`) with a scannable QR code
6. **Reconnect your phone** to your home WiFi and open that address in your web browser — you're done!

Head to the **Downloads** tab to grab your first card set — cards won't appear on the display until you download at least one.

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
# Install git if not already present (it's pre-installed on most Pi OS images)
sudo apt-get update -qq && sudo apt-get install -y git

# Clone InkSlab
git clone https://github.com/costamesatechsolutions/inkslab-eink-tcg-display.git ~/inkslab
```

### Step 3 — Run Setup

```bash
sudo bash ~/inkslab/scripts/setup.sh
```

This installs all dependencies, configures the services, and reboots. Your SSH session will disconnect — this is normal.

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
- **Timezone Auto-Detect:** Tap one button to set day/night timing to your local timezone (handles daylight saving)
- Adjust display rotation and color saturation (boost colors for the e-paper display)
- Enable **My Cards Only** mode to restrict the display to cards you've selected
- **Software Update:** Check for and install OTA updates directly from the web dashboard
- **WiFi Network:** View current connection status and change WiFi networks without SSH

### My Cards Tab
- Browse every downloaded set and select the cards you want to display. Tap any card name to view a high-res preview modal
- **Search Cards:** Search for any character or card and instantly add all versions to your list
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
4. Wait about 60 seconds — the display may go blank briefly while services restart. The page will automatically reconnect and cards will resume

### Via SSH
```bash
ssh pi@<your-pi-ip>
cd ~/inkslab
git fetch origin && git reset --hard origin/master
sudo rm -f /etc/systemd/system/inkslab.service /etc/systemd/system/inkslab_web.service /etc/systemd/system/inkslab-selfheal.service /etc/systemd/system/inkslab-selfheal.timer
sudo cp ~/inkslab/inkslab.service ~/inkslab/inkslab_web.service ~/inkslab/inkslab-selfheal.service ~/inkslab/inkslab-selfheal.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable inkslab-selfheal.timer
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
| `rotation_angle` | `270` | Display rotation: `270` (Default) or `90` (Upside Down) |
| `day_interval` | `600` (10 min) | Seconds between cards during the day. The dashboard shows this in minutes |
| `night_interval` | `3600` (1 hr) | Seconds between cards at night. The dashboard shows this in minutes |
| `day_start` / `day_end` | `7` / `23` | Day mode hours (24h format) |
| `color_saturation` | `2.5` | Color boost for e-paper (higher = more vivid, max 5.0) |
| `collection_only` | `false` | Only show cards you've selected in My Cards |
| `timezone_name` | `null` | IANA timezone name (e.g. `"America/New_York"`). Set automatically via the **Auto-Detect** button in Settings. Handles daylight saving time automatically — set it once and forget it |
| `timezone_offset` | `null` | Manual UTC offset fallback (e.g. `-5`). Only used if `timezone_name` is not set |

> **Timezone tip:** The Settings tab shows the Pi's current time and an Auto-Detect button. Tap it once to set your timezone from your phone — it detects your full timezone (e.g., "America/New_York") and handles daylight saving automatically. You never need to touch it again, even when clocks change.
>
> **Pre-flashed units:** The Pi's timezone is set when the SD card is flashed. If the recipient is in a different timezone, they just need to go to Settings and tap Auto-Detect — it takes 2 seconds and they'll never need to think about it again.

---

## Troubleshooting

> **The fix for almost everything: unplug the InkSlab, wait 10 seconds, plug it back in.** Wait 2 minutes for it to fully boot. This clears any stuck state and triggers automatic self-repair. If something seems wrong, always try this first before anything else.

### Common Issues (no SSH needed)

| Problem | Fix |
|---------|-----|
| Something seems wrong / nothing is working | **Unplug, wait 10 seconds, plug back in.** Wait 2 minutes. InkSlab self-heals on every boot. |
| Display is frozen / not changing cards | Unplug, wait 10 seconds, plug back in. Wait 2 minutes for first card to appear. |
| Can't find the dashboard | The IP address is shown on the e-ink display at boot. If you missed it, unplug and replug — it shows the IP again on startup. Or check your router's admin page for a device named `inkslab`. |
| Dashboard not loading in browser | Make sure your phone/computer is on the same WiFi as the InkSlab. Try typing the IP address directly (e.g. `http://192.168.1.42`). If still nothing, unplug and replug the InkSlab. |
| Buttons not responding | **First: close any extra tabs** — having multiple dashboard tabs open is the most common cause. Open a single fresh tab and navigate to the dashboard address. Or try Ctrl+Shift+R (Win) / Cmd+Shift+R (Mac). On mobile, open a new tab or use private/incognito. |
| Dashboard broken after a large update | Close all dashboard tabs. Open a single fresh tab and go to the dashboard. If still broken, unplug and replug the InkSlab, then open a fresh tab. |
| Washed-out or dull colors | Go to **Settings** and increase **Color Saturation** (default 2.5, try 3.0–4.0). Tap Save Settings. |
| Day/night timing is off | Go to **Settings** and tap **Auto-Detect** next to Timezone. Then tap **Save Settings**. |
| My Cards mode shows nothing | Go to the **My Cards** tab and select some cards first. |
| WiFi setup page not appearing | Make sure you're connected to the `InkSlab-Setup` WiFi network (no password). If the page doesn't open automatically, go to `http://10.42.0.1` in your browser. |
| Wrong WiFi password entered | The setup page will show an error. Just try again — the `InkSlab-Setup` network stays up so you can re-enter the correct password. |
| Want to change WiFi networks | Go to **Settings** > **Change WiFi Network**. |
| Got a new router / changed WiFi password | InkSlab detects this automatically within ~30 minutes and re-enters setup mode. The display will show the setup screen. Connect your phone to `InkSlab-Setup` and enter your new WiFi details. To speed this up: unplug and replug — it detects the issue faster on boot. |
| WiFi went out temporarily | No action needed — InkSlab keeps showing cards and reconnects automatically when WiFi comes back. |
| Download fails or stalls | Click **Stop Download**, then start it again. It safely skips files already downloaded and picks up where it left off. |
| OTA update stuck | Wait 60 seconds, then close the tab and open a fresh one. The update runs in the background and the services restart automatically. |

### Advanced (SSH required)

These are rare situations that require SSH access. If you don't have SSH set up, unplugging and replugging fixes most things.

| Problem | Fix |
|---------|-----|
| Services show "masked" or won't start | `sudo bash ~/inkslab/scripts/setup.sh` — removes broken service files, reinstalls, and reboots. |
| Display not updating after 5+ minutes | Check SPI is enabled: `ls /dev/spi*` — should show files. If missing, run `sudo raspi-config nonint do_spi 0 && sudo reboot`. |
| Check service logs | `journalctl -u inkslab -f` (display) or `journalctl -u inkslab_web -f` (dashboard) |
| Manually restart services | `sudo systemctl restart inkslab inkslab_web` |
| SSH disconnected after setup | Normal — the Pi reboots as part of setup. Wait 2 minutes and reconnect. |

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
  inkslab-selfheal.service       # systemd oneshot for periodic self-heal
  inkslab-selfheal.timer         # systemd timer — runs self-heal every 24h
  lib/waveshare_epd/             # e-Paper display driver (bundled)
  scripts/
    download_cards_pokemon.py    # Pokemon card downloader
    download_cards_mtg.py        # MTG card downloader (Scryfall API)
    download_cards_lorcana.py    # Lorcana card downloader (Lorcast API)
    setup.sh                     # First-time setup (installs services + reboots)
    ota_update.sh                # OTA update script (atomic git reset + verify + service restart)
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
