#!/bin/bash
# InkSlab first-time setup script
# Run with: sudo bash ~/inkslab/scripts/setup.sh
set -e

echo "=== InkSlab Setup ==="
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Step 1: Install Python dependencies
echo "[1/4] Installing dependencies..."
apt-get install -y python3-pip python3-pil python3-numpy python3-lgpio python3-flask python3-requests >/dev/null 2>&1
pip3 install --break-system-packages qrcode 2>/dev/null || pip3 install qrcode 2>/dev/null || true
echo "  Done."

# Step 2: Remove any existing service files (rm -f handles symlinks correctly)
# This is the critical fix: if services are masked, the file at
# /etc/systemd/system/inkslab.service is a symlink to /dev/null.
# Plain 'cp' follows the symlink and writes to /dev/null.
# 'rm -f' removes the symlink itself, then 'cp' creates a fresh file.
echo "[2/4] Installing service files..."
rm -f /etc/systemd/system/inkslab.service
rm -f /etc/systemd/system/inkslab_web.service
cp "$SCRIPT_DIR/inkslab.service" /etc/systemd/system/inkslab.service
cp "$SCRIPT_DIR/inkslab_web.service" /etc/systemd/system/inkslab_web.service
echo "  Done."

# Step 3: Enable services
echo "[3/4] Enabling services..."
systemctl daemon-reload
systemctl enable inkslab inkslab_web
echo "  Done."

# Step 4: Verify
echo "[4/4] Verifying..."
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
