#!/bin/bash
# InkSlab first-time setup script
# Run with: sudo bash ~/inkslab/scripts/setup.sh
set -e

echo "=== InkSlab Setup ==="
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Step 1: Install Python dependencies
echo "[1/5] Installing dependencies..."
apt-get update -qq >/dev/null 2>&1
apt-get install -y python3-pip python3-pil python3-numpy python3-lgpio python3-spidev python3-gpiozero python3-flask python3-requests python3-qrcode git >/dev/null 2>&1

# Fallback: if python3-qrcode wasn't available via apt, try pip
if ! python3 -c "import qrcode" 2>/dev/null; then
    pip3 install --break-system-packages qrcode 2>/dev/null || pip3 install qrcode 2>/dev/null || true
fi

# Install Python packages that are not reliably available via apt
bash "$SCRIPT_DIR/scripts/sync_python_deps.sh" >/dev/null 2>&1 || true

# Verify critical imports
for mod in PIL numpy flask requests qrcode spidev gpiozero yfinance; do
    if ! python3 -c "import $mod" 2>/dev/null; then
        echo "  WARNING: Python module '$mod' failed to install."
    fi
done
echo "  Done."

# Step 2: Remove any existing service files (rm -f handles symlinks correctly)
# This is the critical fix: if services are masked, the file at
# /etc/systemd/system/inkslab.service is a symlink to /dev/null.
# Plain 'cp' follows the symlink and writes to /dev/null.
# 'rm -f' removes the symlink itself, then 'cp' creates a fresh file.
echo "[2/5] Installing service files..."
rm -f /etc/systemd/system/inkslab.service
rm -f /etc/systemd/system/inkslab_web.service
rm -f /etc/systemd/system/inkslab-selfheal.service
rm -f /etc/systemd/system/inkslab-selfheal.timer
cp "$SCRIPT_DIR/inkslab.service" /etc/systemd/system/inkslab.service
cp "$SCRIPT_DIR/inkslab_web.service" /etc/systemd/system/inkslab_web.service
cp "$SCRIPT_DIR/inkslab-selfheal.service" /etc/systemd/system/inkslab-selfheal.service
cp "$SCRIPT_DIR/inkslab-selfheal.timer" /etc/systemd/system/inkslab-selfheal.timer
echo "  Done."

# Step 3: Enable services
echo "[3/5] Enabling services..."
systemctl daemon-reload
systemctl enable inkslab inkslab_web inkslab-selfheal.timer
echo "  Done."

# Step 4: System hardening
echo "[4/5] Configuring system hardening..."

# Enable hardware watchdog — auto-reboots on kernel freeze
if ! grep -q "dtparam=watchdog=on" /boot/firmware/config.txt 2>/dev/null; then
    echo "dtparam=watchdog=on" >> /boot/firmware/config.txt
fi
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/watchdog.conf << 'WEOF'
[Manager]
RuntimeWatchdog=15s
RebootWatchdogSec=10min
WEOF

# Cap journal size to prevent SD card fill
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/inkslab.conf << 'JEOF'
[Journal]
SystemMaxUse=50M
JEOF

echo "  Done."

# Step 5: Verify
echo "[5/5] Verifying..."
STATUS_INKSLAB=$(systemctl is-enabled inkslab 2>/dev/null || true)
STATUS_WEB=$(systemctl is-enabled inkslab_web 2>/dev/null || true)

if [ "$STATUS_INKSLAB" = "enabled" ] && [ "$STATUS_WEB" = "enabled" ]; then
    echo "  Both services enabled successfully."
    echo ""
    echo "=== Setup complete! ==="
    echo "Rebooting in 5 seconds..."
    echo "(Services will start automatically after reboot)"
    echo "(Your SSH session will disconnect — reconnect after ~30 seconds)"
    sleep 5
    reboot
else
    echo "  WARNING: Service status unexpected."
    echo "  inkslab: $STATUS_INKSLAB"
    echo "  inkslab_web: $STATUS_WEB"
    echo ""
    echo "  Check with: ls -la /etc/systemd/system/inkslab*"
    exit 1
fi
