#!/bin/bash
# InkSlab Self-Healing Boot Check
# Runs before services start. Verifies critical files are intact.
# If anything is missing or corrupt, auto-repairs from git.
# This makes the device recover from any user-caused corruption on reboot.

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CRITICAL_FILES="inkslab.py inkslab_web.py wifi_manager.py"
NEEDS_REPAIR=false

cd "$SCRIPT_DIR" || { echo "selfheal: Cannot find $SCRIPT_DIR"; exit 1; }

# Fix "dubious ownership" error — service runs as root but repo is owned by pi
git config --global safe.directory "$SCRIPT_DIR" 2>/dev/null

# Ensure ~/inkslab symlink exists (for migration from old deep-nested paths)
INKSLAB_HOME="/home/pi/inkslab"
if [ "$SCRIPT_DIR" != "$INKSLAB_HOME" ] && [ ! -e "$INKSLAB_HOME" ] && [ -d "$SCRIPT_DIR" ]; then
    ln -sfn "$SCRIPT_DIR" "$INKSLAB_HOME"
    chown -h pi:pi "$INKSLAB_HOME" 2>/dev/null
    echo "selfheal: Created symlink $INKSLAB_HOME -> $SCRIPT_DIR"
fi

# Check each critical file exists and is non-empty
for f in $CRITICAL_FILES; do
    if [ ! -s "$SCRIPT_DIR/$f" ]; then
        echo "selfheal: $f is missing or empty"
        NEEDS_REPAIR=true
        break
    fi
done

# Quick syntax check (only if files exist)
if [ "$NEEDS_REPAIR" = false ]; then
    for f in $CRITICAL_FILES; do
        if ! python3 -m py_compile "$SCRIPT_DIR/$f" 2>/dev/null; then
            echo "selfheal: $f has syntax errors"
            NEEDS_REPAIR=true
            break
        fi
    done
fi

# Note: no import check — inkslab.py imports hardware modules (e-ink, GPIO)
# that can't be imported outside the display daemon context. py_compile is sufficient.

if [ "$NEEDS_REPAIR" = true ]; then
    echo "selfheal: Repairing from git..."

    # Save current commit so we can roll back if remote is also broken
    PREV_COMMIT=$(git rev-parse HEAD 2>/dev/null)

    # Try to fetch latest and hard reset
    if timeout 30 git fetch origin 2>/dev/null; then
        # Auto-detect branch
        BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
        if [ -z "$BRANCH" ]; then
            if git rev-parse --verify origin/main >/dev/null 2>&1; then
                BRANCH="main"
            else
                BRANCH="master"
            fi
        fi

        git reset --hard "origin/$BRANCH" 2>/dev/null
        chown -R pi:pi "$SCRIPT_DIR" 2>/dev/null

        # Verify repair worked — files exist, syntax OK, and imports succeed
        REPAIR_OK=true
        for f in $CRITICAL_FILES; do
            if [ ! -s "$SCRIPT_DIR/$f" ]; then
                REPAIR_OK=false
                break
            fi
        done
        if [ "$REPAIR_OK" = true ]; then
            for f in $CRITICAL_FILES; do
                if ! python3 -m py_compile "$SCRIPT_DIR/$f" 2>/dev/null; then
                    REPAIR_OK=false
                    break
                fi
            done
        fi
        if [ "$REPAIR_OK" = true ]; then
            echo "selfheal: Repair successful"
        else
            # Remote is broken too — roll back to previous local commit
            echo "selfheal: Remote code is broken, rolling back to previous commit"
            if [ -n "$PREV_COMMIT" ]; then
                git reset --hard "$PREV_COMMIT" 2>/dev/null
                chown -R pi:pi "$SCRIPT_DIR" 2>/dev/null
                echo "selfheal: Rolled back to $PREV_COMMIT"
            else
                echo "selfheal: Repair failed — no previous commit to roll back to"
            fi
        fi
    else
        # No internet — try git checkout from local repo
        echo "selfheal: No internet, trying local git restore..."
        for f in $CRITICAL_FILES; do
            if [ ! -s "$SCRIPT_DIR/$f" ]; then
                git checkout HEAD -- "$f" 2>/dev/null
            fi
        done
        chown -R pi:pi "$SCRIPT_DIR" 2>/dev/null
    fi

    # Update service files — rm -f first to handle masked symlinks (cp follows them)
    if [ -f "$SCRIPT_DIR/inkslab.service" ]; then
        rm -f /etc/systemd/system/inkslab.service
        cp "$SCRIPT_DIR/inkslab.service" /etc/systemd/system/inkslab.service 2>/dev/null
    fi
    if [ -f "$SCRIPT_DIR/inkslab_web.service" ]; then
        rm -f /etc/systemd/system/inkslab_web.service
        cp "$SCRIPT_DIR/inkslab_web.service" /etc/systemd/system/inkslab_web.service 2>/dev/null
    fi
    if [ -f "$SCRIPT_DIR/inkslab-selfheal.service" ] && [ -f "$SCRIPT_DIR/inkslab-selfheal.timer" ]; then
        rm -f /etc/systemd/system/inkslab-selfheal.service /etc/systemd/system/inkslab-selfheal.timer
        cp "$SCRIPT_DIR/inkslab-selfheal.service" /etc/systemd/system/inkslab-selfheal.service 2>/dev/null
        cp "$SCRIPT_DIR/inkslab-selfheal.timer" /etc/systemd/system/inkslab-selfheal.timer 2>/dev/null
        systemctl enable inkslab-selfheal.timer 2>/dev/null
    fi
    systemctl daemon-reload 2>/dev/null
else
    echo "selfheal: All files OK"
fi

# Clean up any stale lock/temp files from previous crashes
rm -f /tmp/inkslab_update.lock
rm -f /tmp/inkslab_next
rm -f /tmp/inkslab_prev
rm -f /tmp/inkslab_pause
rm -f /tmp/inkslab_collection_changed
rm -f /tmp/inkslab_wifi_connected
rm -f /tmp/inkslab_wifi_failed
rm -f /tmp/inkslab_wifi_setup
rm -f /tmp/inkslab_unbox
rm -f /tmp/inkslab_watchdog_setup
rm -f /tmp/inkslab_update_status.json
rm -f /tmp/inkslab_status.json
rm -f /tmp/inkslab_download.log

# Ensure hardware watchdog is enabled
if [ -f /boot/firmware/config.txt ]; then
    if ! grep -q "dtparam=watchdog=on" /boot/firmware/config.txt; then
        echo "dtparam=watchdog=on" >> /boot/firmware/config.txt
        echo "selfheal: Enabled hardware watchdog (takes effect next reboot)"
    fi
fi
if [ ! -f /etc/systemd/system.conf.d/watchdog.conf ]; then
    mkdir -p /etc/systemd/system.conf.d
    cat > /etc/systemd/system.conf.d/watchdog.conf << 'WEOF'
[Manager]
RuntimeWatchdog=15s
RebootWatchdogSec=10min
WEOF
    systemctl daemon-reexec 2>/dev/null
    echo "selfheal: Configured systemd watchdog"
fi

# Ensure journal size is capped
if [ ! -f /etc/systemd/journald.conf.d/inkslab.conf ]; then
    mkdir -p /etc/systemd/journald.conf.d
    cat > /etc/systemd/journald.conf.d/inkslab.conf << 'JEOF'
[Journal]
SystemMaxUse=50M
JEOF
    systemctl restart systemd-journald 2>/dev/null
    echo "selfheal: Configured journal size limit"
fi

# Periodic git garbage collection (keeps .git small over years of updates)
# Timeout prevents slow GC from delaying boot on Pi Zero
timeout 15 git gc --auto 2>/dev/null || true

# Clean up orphaned .tmp files from interrupted atomic writes
find /home/pi/inkslab -name '*.tmp' -mmin +5 -delete 2>/dev/null
find /home/pi -maxdepth 1 -name '*.tmp' -mmin +5 -delete 2>/dev/null
# Clean up atomic-write temp for status file (always safe to remove on boot)
rm -f /tmp/inkslab_status.json.tmp
