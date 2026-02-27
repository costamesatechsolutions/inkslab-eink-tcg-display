# InkSlab — e-Ink TCG Card Display

A Raspberry Pi + e-ink display that cycles through TCG cards in a graded-slab-style layout — showing the set name, year, card number, and rarity on a 7-color Waveshare Spectra 6 screen.

**Supports Pokemon and Magic: The Gathering.**

Cards rotate every **10 minutes** during the day and every **hour** at night to preserve the display.

Includes a **web dashboard** at `http://inkslab.local` for switching TCGs, adjusting settings, managing your card collection, and downloading new sets — all from your phone.

**By [Costa Mesa Tech Solutions](https://github.com/costamesatechsolutions)** (a brand of Pine Heights Ventures LLC)

## What You Need

### Electronics
- **Raspberry Pi Zero W H** (the H means headers are pre-soldered — required for the HAT)
- **[Waveshare 4" e-Paper HAT+ (E)](https://www.waveshare.com/wiki/4inch_e-Paper_HAT%2B_(E)_Manual)** — Spectra 6-color model (black, white, red, yellow, blue, green, orange)
- **Micro SD card** (32GB for one TCG, 64GB if running both — Pokemon takes ~12GB, MTG takes ~18GB)
- **90-degree micro USB cable** (recommended) — keeps the power cable hidden behind the frame instead of sticking out the side

### 3D Printed Frame
Print files available on MakerWorld: **[InkSlab on MakerWorld](https://makerworld.com/en/models/2452200-inkslab-open-source-e-ink-tcg-display)**

The frame holds the Pi and e-paper screen in a clean, desk-friendly package. Just print, assemble, and plug in.

### Assembly
1. Attach the e-Paper HAT+ to the Pi Zero W H's 40-pin GPIO header
2. Mount everything in the 3D printed frame
3. Route the 90-degree USB power cable out the back
4. Follow the software setup below

## How It Works

1. A download script fetches card images and metadata from an open data source (PokemonTCG GitHub repo or Scryfall for MTG)
2. `inkslab.py` shuffles all cards into a "deck", processes each image for the 7-color e-paper palette (Floyd-Steinberg dithering), and displays them in a loop
3. A systemd service keeps it running as a daemon on boot
4. The web dashboard lets you switch TCGs, change settings, manage your collection, and download new sets from your phone

## Display Layout

Each card is shown in a graded-slab style:
```
┌──────────────────────┐
│  2023 OBSIDIAN FLAMES │
│    #201  •  HOLO      │
│ ┌──────────────────┐  │
│ │                  │  │
│ │    Card Image    │  │
│ │                  │  │
│ │                  │  │
│ └──────────────────┘  │
└──────────────────────┘
```

## Software Setup

### 0. Flash Raspberry Pi OS

1. Download and install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer
2. Insert your micro SD card
3. Open Raspberry Pi Imager and choose:
   - **Device:** Raspberry Pi Zero
   - **OS:** Raspberry Pi OS (Legacy, 32-bit) — **Bookworm**
   - **Storage:** Your SD card
4. Click **Next**, then click **Edit Settings** when prompted:
   - **General tab:** Set a hostname (e.g. `inkslab`), username (`pi`), and password
   - **General tab:** Configure your Wi-Fi network name and password
   - **Services tab:** Enable SSH (use password authentication)
5. Click **Save**, then **Yes** to flash the card
6. Insert the SD card into your Pi Zero W H and power it on
7. Wait a couple minutes for first boot, then SSH in:

```bash
ssh pi@inkslab.local
```

> **Tip:** If `inkslab.local` doesn't resolve, check your router's admin page for the Pi's IP address and use `ssh pi@<IP_ADDRESS>` instead.

### 1. Enable SPI

```bash
sudo raspi-config
```

Navigate to: **Interface Options > SPI > Enable**, then reboot:

```bash
sudo reboot
```

### 2. Install System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y python3-pip python3-pil python3-numpy python3-spidev python3-gpiozero python3-requests python3-flask git unzip screen
```

### 3. Install Hardware Libraries

```bash
# bcm2835 library
cd ~
wget http://www.airspayce.com/mikem/bcm2835/bcm2835-1.71.tar.gz
tar zxvf bcm2835-1.71.tar.gz
cd bcm2835-1.71
sudo ./configure && sudo make && sudo make check && sudo make install
```

```bash
# lgpio library
cd ~
wget https://github.com/joan2937/lg/archive/master.zip
unzip master.zip
cd lg-master
make
sudo make install
```

```bash
# gpiod
sudo apt install gpiod libgpiod-dev
```

### 4. Install the Waveshare Driver

```bash
cd ~
wget "https://files.waveshare.com/wiki/4inch-e-Paper-HAT%2B-(E)/4inch_e-Paper_E.zip"
unzip 4inch_e-Paper_E.zip -d 4inch_e-Paper_E
```

### 5. Clone InkSlab

```bash
cd ~/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/examples
git clone https://github.com/costamesatechsolutions/inkslab-eink-tcg-display.git
cd inkslab-eink-tcg-display
```

### 6. Download Card Images

Use a `screen` session so the download survives if your SSH connection drops:

```bash
sudo apt install screen
screen -S downloader
```

**For Pokemon** (~15,000+ cards, ~12GB):

```bash
cd ~/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/examples/inkslab-eink-tcg-display
python3 scripts/download_cards_pokemon.py
```

**For MTG** (~90,000+ cards, ~18GB):

```bash
cd ~/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/examples/inkslab-eink-tcg-display
python3 scripts/download_cards_mtg.py
```

To save space, you can limit MTG to recent sets:

```bash
python3 scripts/download_cards_mtg.py --since 2018
```

To **detach** from screen (download keeps running in the background): press `Ctrl+A`, then press `D`.

To **re-attach** later and check progress:

```bash
screen -r downloader
```

> **Note:** You can also trigger downloads from the web dashboard later (see step 9).

### 7. Choose Your TCG

Edit the top of `inkslab.py` and set `ACTIVE_TCG` to whichever game you want to display:

```python
ACTIVE_TCG = "pokemon"  # "pokemon" or "mtg"
```

Or skip this — you can switch TCGs from the web dashboard.

### 8. Test It

```bash
cd ~/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/examples/inkslab-eink-tcg-display
python3 inkslab.py
```

You should see a random card appear on the display within ~30 seconds.

### 9. Run on Boot (Daemon)

**Display service:**

```bash
sudo cp inkslab.service /etc/systemd/system/inkslab.service
sudo systemctl enable inkslab.service
sudo systemctl start inkslab.service
```

**Web dashboard:**

```bash
sudo cp inkslab_web.service /etc/systemd/system/inkslab_web.service
sudo systemctl enable inkslab_web.service
sudo systemctl start inkslab_web.service
```

The dashboard is now live at **http://inkslab.local** — open it on your phone or computer.

> **Tip:** If `inkslab.local` doesn't work in your browser, use the Pi's IP address instead (run `hostname -I` on the Pi to find it). The dashboard footer also shows the IP address. Some routers and Android devices don't support `.local` mDNS addresses.

Check that both services are running:

```bash
journalctl -u inkslab.service -f
journalctl -u inkslab_web.service -f
```

## Web Dashboard

Access the dashboard at `http://inkslab.local` from any device on your network.

| Tab | Features |
|-----|----------|
| **Display** | See the current card with preview image, skip to next card, quick-switch between Pokemon and MTG |
| **Settings** | Change rotation interval, day/night hours, display rotation, color saturation, enable collection mode |
| **Collection** | Browse all downloaded sets and cards, preview card images, mark which cards you own, select/deselect entire sets |
| **Downloads** | Storage stats, trigger/stop downloads for Pokemon or MTG, live download progress, delete card data |

When **collection mode** is enabled in Settings, the display only cycles through cards you've marked as owned.

## Project Structure

```
inkslab-eink-tcg-display/
├── inkslab.py                      # Main display script (runs as daemon)
├── inkslab_web.py                   # Web dashboard (Flask)
├── inkslab.service                  # systemd service for display
├── inkslab_web.service              # systemd service for web dashboard
├── requirements.txt                 # Python dependencies
├── lib/
│   └── waveshare_epd/              # e-Paper display driver (bundled)
│       ├── epd4in0e.py              # 4" Spectra 6 driver
│       └── epdconfig.py             # Hardware config (SPI/GPIO)
└── scripts/
    ├── download_cards_pokemon.py    # Download Pokemon card images + metadata
    └── download_cards_mtg.py        # Download MTG card images + metadata (Scryfall)
```

## Configuration

Settings can be changed from the web dashboard or by editing the config file directly.

**Config file:** `/home/pi/inkslab_config.json`

| Setting | Default | Description |
|---------|---------|-------------|
| `active_tcg` | `"pokemon"` | Which TCG to display (`"pokemon"` or `"mtg"`) |
| `rotation_angle` | `270` | Display rotation (0/90/180/270) |
| `day_interval` | `600` (10 min) | Seconds between cards during the day |
| `night_interval` | `3600` (1 hr) | Seconds between cards at night |
| `day_start` / `day_end` | `7` / `23` | Day mode hours (24h format) |
| `color_saturation` | `2.5` | Color boost for e-paper (higher = more vivid) |
| `collection_only` | `false` | Only show cards marked as owned |

If no config file exists, the display uses built-in defaults and works normally.

## Adding New Sets

From the **web dashboard**: go to the Downloads tab and tap the download button.

From **SSH**:

**Pokemon:**
```bash
cd ~/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/examples/inkslab-eink-tcg-display
python3 scripts/download_cards_pokemon.py
```

**MTG:**
```bash
cd ~/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/examples/inkslab-eink-tcg-display
python3 scripts/download_cards_mtg.py
```

Then restart the display service:

```bash
sudo systemctl restart inkslab.service
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `No module named waveshare_epd` | Make sure the repo is cloned inside the Waveshare `examples/` directory (see step 5). The `lib/` folder in the repo contains the driver as a fallback. |
| Display not updating | Check SPI is enabled: `ls /dev/spi*` should show devices |
| Service won't start | Check logs: `journalctl -u inkslab.service -f` |
| Washed-out colors | Increase `color_saturation` in the web dashboard or config file (default 2.5) |
| SSH can't connect | Make sure SSH was enabled in Raspberry Pi Imager settings. Check the Pi is on your Wi-Fi network. |
| Web dashboard not loading | Check `journalctl -u inkslab_web.service -f`. Make sure Flask is installed: `sudo apt install python3-flask` |
| Collection mode shows nothing | Make sure you've marked some cards as owned in the Collection tab first |

## Credits

- Pokemon card data: [PokemonTCG/pokemon-tcg-data](https://github.com/PokemonTCG/pokemon-tcg-data) (open data)
- MTG card data: [Scryfall](https://scryfall.com/) (free API)
- Display driver: [Waveshare e-Paper](https://github.com/waveshare/e-Paper) (MIT License)

## License

AGPL-3.0 — see [LICENSE](LICENSE)
