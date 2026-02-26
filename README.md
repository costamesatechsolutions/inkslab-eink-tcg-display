# InkSlab — e-Ink TCG Card Display

A Raspberry Pi + e-ink display that cycles through every TCG card ever printed in a graded-slab-style layout — showing the set name, year, card number, and market price on a 7-color Waveshare Spectra 6 screen.

Cards rotate every **10 minutes** during the day and every **hour** at night to preserve the display.

**By [Costa Mesa Tech Solutions](https://github.com/costamesatechsolutions)** (a brand of Pine Heights Ventures LLC)

## What You Need

### Electronics
- **Raspberry Pi Zero W H** (the H means headers are pre-soldered — required for the HAT)
- **[Waveshare 4" e-Paper HAT+ (E)](https://www.waveshare.com/wiki/4inch_e-Paper_HAT%2B_(E)_Manual)** — Spectra 6-color model (black, white, red, yellow, blue, green, orange)
- **Micro SD card** (16GB minimum, 32GB+ recommended — card images take ~12GB)
- **90-degree micro USB cable** (recommended) — keeps the power cable hidden behind the frame instead of sticking out the side

### 3D Printed Frame
Print files available on MakerWorld: **[link coming soon]**

The frame holds the Pi and e-paper screen in a clean, desk-friendly package. Just print, assemble, and plug in.

### Assembly
1. Attach the e-Paper HAT+ to the Pi Zero W H's 40-pin GPIO header
2. Mount everything in the 3D printed frame
3. Route the 90-degree USB power cable out the back
4. Follow the software setup below

## How It Works

1. `scripts/download_cards.py` downloads card images from the [PokemonTCG open data repo](https://github.com/PokemonTCG/pokemon-tcg-data)
2. `scripts/update_metadata.py` fetches set names, card numbers, rarities, and TCGPlayer market prices
3. `inkslab.py` shuffles all cards into a "deck", processes each image for the 7-color e-paper palette (Floyd-Steinberg dithering), and displays them in a loop
4. A systemd service keeps it running as a daemon on boot

## Display Layout

Each card is shown in a graded-slab style:
```
┌──────────────────────┐
│  2023 OBSIDIAN FLAMES │
│    #201  •  $45.00    │
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
sudo apt-get install -y python3-pip python3-pil python3-numpy python3-spidev python3-gpiozero python3-requests git unzip screen
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

Downloads every card image (~15,000+ cards, ~12GB). This takes a while on a Pi Zero. Supports resume — you can stop and restart safely.

Use a `screen` session so the download survives if your SSH connection drops:

```bash
sudo apt install screen
```

Start a new screen session:

```bash
screen -S downloader
```

You're now inside the screen session. Run the download:

```bash
cd ~/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/examples/inkslab-eink-tcg-display
python3 scripts/download_cards.py
```

To **detach** from screen (download keeps running in the background): press `Ctrl+A`, then press `D`.

To **re-attach** later and check progress:

```bash
screen -r downloader
```

### 7. Build Metadata

```bash
cd ~/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/examples/inkslab-eink-tcg-display
python3 scripts/update_metadata.py
```

This creates `master_index.json` (set names/years) and `_data.json` in each set folder (card names, numbers, rarities, and market prices).

### 8. Test It

```bash
cd ~/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/examples/inkslab-eink-tcg-display
python3 inkslab.py
```

You should see a random card appear on the display within ~30 seconds.

### 9. Run on Boot (Daemon)

```bash
sudo cp inkslab.service /etc/systemd/system/inkslab.service
```

```bash
sudo systemctl enable inkslab.service
sudo systemctl start inkslab.service
```

Check that it's running:

```bash
journalctl -u inkslab.service -f
```

To restart after editing the script:

```bash
sudo systemctl restart inkslab.service
```

## Project Structure

```
inkslab-eink-tcg-display/
├── inkslab.py               # Main display script (runs as daemon)
├── inkslab.service           # systemd service file
├── requirements.txt          # Python dependencies
├── lib/
│   └── waveshare_epd/       # e-Paper display driver (bundled)
│       ├── epd4in0e.py       # 4" Spectra 6 driver
│       └── epdconfig.py      # Hardware config (SPI/GPIO)
└── scripts/
    ├── download_cards.py     # Download all card images
    └── update_metadata.py    # Fetch set names, prices, rarities
```

Card images are stored at `/home/pi/pokemon_cards/` (not in the repo — too large):
```
pokemon_cards/
├── master_index.json         # Set ID -> name + year lookup
├── base1/                    # Base Set (1999)
│   ├── _data.json            # Card metadata
│   ├── base1-1.png
│   └── ...
├── sv9/                      # Journey Together (2025)
└── ...                       # 150+ sets
```

## Configuration

Edit the top of `inkslab.py` to customize:

| Setting | Default | Description |
|---------|---------|-------------|
| `LIBRARY_DIR` | `/home/pi/pokemon_cards` | Path to card images |
| `ROTATION_ANGLE` | `270` | Display rotation (0/90/180/270) |
| `DAY_INTERVAL` | `600` (10 min) | Seconds between cards during the day |
| `NIGHT_INTERVAL` | `3600` (1 hr) | Seconds between cards at night |
| `DAY_START` / `DAY_END` | `7` / `23` | Day mode hours (24h format) |
| `COLOR_SATURATION` | `2.5` | Color boost for e-paper (higher = more vivid) |

## Updating Prices

Re-run the metadata updater to refresh TCGPlayer market prices:

```bash
cd ~/4inch_e-Paper_E/RaspberryPi_JetsonNano/python/examples/inkslab-eink-tcg-display
python3 scripts/update_metadata.py
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `No module named waveshare_epd` | Make sure the repo is cloned inside the Waveshare `examples/` directory (see step 5). The `lib/` folder in the repo contains the driver as a fallback. |
| Display not updating | Check SPI is enabled: `ls /dev/spi*` should show devices |
| Service won't start | Check logs: `journalctl -u inkslab.service -f` |
| Washed-out colors | Increase `COLOR_SATURATION` in `inkslab.py` (default 2.5) |
| SSH can't connect | Make sure SSH was enabled in Raspberry Pi Imager settings. Check the Pi is on your Wi-Fi network. |

## Credits

- Card data: [PokemonTCG/pokemon-tcg-data](https://github.com/PokemonTCG/pokemon-tcg-data) (open data)
- Display driver: [Waveshare e-Paper](https://github.com/waveshare/e-Paper) (MIT License)

## License

AGPL-3.0 — see [LICENSE](LICENSE)
