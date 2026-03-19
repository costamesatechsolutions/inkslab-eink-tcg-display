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
if [ "$SCRIPT_DIR" != "$INKSLAB_HOME" ] && [ ! -e "$INKSLAB_HOME" ]; then
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

if [ "$NEEDS_REPAIR" = true ]; then
    echo "selfheal: Repairing from git..."

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

        # Verify repair worked
        ALL_OK=true
        for f in $CRITICAL_FILES; do
            if [ ! -s "$SCRIPT_DIR/$f" ]; then
                ALL_OK=false
                break
            fi
        done

        if [ "$ALL_OK" = true ]; then
            echo "selfheal: Repair successful"
        else
            echo "selfheal: Repair failed — files still missing after git reset"
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

# Clean up orphaned .tmp files from interrupted atomic writes
find /home/pi/inkslab -name '*.tmp' -mmin +5 -delete 2>/dev/null
find /home/pi -maxdepth 1 -name '*.tmp' -mmin +5 -delete 2>/dev/null
# Clean up atomic-write temp for status file (always safe to remove on boot)
rm -f /tmp/inkslab_status.json.tmp
