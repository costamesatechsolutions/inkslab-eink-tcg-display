#!/bin/bash
# InkSlab OTA Update Script
# Runs detached from the web process so it survives service restarts.
# Writes progress to /tmp/inkslab_update_status.json at each stage.
#
# Safety: Uses git reset --hard (atomic) instead of git pull (can corrupt files
# if interrupted). Verifies critical files after update before restarting services.

STATUS_FILE="/tmp/inkslab_update_status.json"
LOCK_FILE="/tmp/inkslab_update.lock"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Critical files that must exist and be non-empty after update
CRITICAL_FILES="inkslab.py inkslab_web.py wifi_manager.py"

write_status() {
    local stage="$1"
    local message="$2"
    local error="$3"
    # Pass values via env vars to avoid shell injection into Python strings
    _STAGE="$stage" _MSG="$message" _ERR="$error" _SF="$STATUS_FILE" \
    python3 -c "
import json, time, os
json.dump({
    'stage': os.environ['_STAGE'],
    'message': os.environ['_MSG'],
    'error': os.environ['_ERR'],
    'timestamp': int(time.time())
}, open(os.environ['_SF'], 'w'))
" 2>/dev/null || echo "{\"stage\":\"update\",\"message\":\"Working...\",\"error\":\"\",\"timestamp\":$(date +%s)}" > "$STATUS_FILE"
}

verify_files() {
    # Check that all critical files exist and are non-empty
    for f in $CRITICAL_FILES; do
        if [ ! -s "$SCRIPT_DIR/$f" ]; then
            echo "FAIL: $f is missing or empty"
            return 1
        fi
    done
    # Quick syntax check on main files
    for f in $CRITICAL_FILES; do
        if ! python3 -m py_compile "$SCRIPT_DIR/$f" 2>/dev/null; then
            echo "FAIL: $f has syntax errors"
            return 1
        fi
    done
    # Note: we intentionally do NOT run `import` checks here because
    # inkslab.py imports hardware-specific modules (e-ink driver, GPIO)
    # that fail during the update context. py_compile above is sufficient.
    return 0
}

cleanup() {
    rm -f "$LOCK_FILE"
}
trap cleanup EXIT

# Prevent concurrent updates
if [ -f "$LOCK_FILE" ]; then
    OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        write_status "error" "Another update is already running" "true"
        exit 1
    fi
    rm -f "$LOCK_FILE"
fi
echo $$ > "$LOCK_FILE"

cd "$SCRIPT_DIR" || {
    write_status "error" "Failed to cd to project directory" "true"
    exit 1
}

# Fix "dubious ownership" error — service runs as root but repo is owned by pi
git config --global safe.directory "$SCRIPT_DIR" 2>/dev/null

# Auto-detect the default branch (main or master)
BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
if [ -z "$BRANCH" ]; then
    if git rev-parse --verify origin/main >/dev/null 2>&1; then
        BRANCH="main"
    else
        BRANCH="master"
    fi
fi

# Save current commit hash for rollback
PREV_COMMIT=$(git rev-parse HEAD 2>/dev/null)

# Stage 1: Fetch latest from remote
write_status "fetching" "Checking for updates..." ""
if ! timeout 60 git fetch origin 2>&1; then
    write_status "error" "Could not reach update server. Check your internet connection." "true"
    exit 1
fi

# Stage 2: Apply update using reset --hard (atomic, can't leave partial files)
write_status "pulling" "Downloading update..." ""
# Stash any local changes (shouldn't be any on a device, but just in case)
git stash --quiet 2>/dev/null
# Hard reset to remote — this is atomic: files are fully written or not at all
if ! git reset --hard "origin/$BRANCH" 2>&1; then
    write_status "error" "Update failed. Try rebooting and updating again." "true"
    exit 1
fi

# Fix file ownership — OTA runs as root but pi user needs to own these files
chown -R pi:pi "$SCRIPT_DIR" 2>/dev/null

# Ensure ~/inkslab symlink exists (for migration from old deep-nested paths)
INKSLAB_HOME="/home/pi/inkslab"
if [ "$SCRIPT_DIR" != "$INKSLAB_HOME" ] && [ ! -e "$INKSLAB_HOME" ]; then
    ln -sfn "$SCRIPT_DIR" "$INKSLAB_HOME"
    chown -h pi:pi "$INKSLAB_HOME" 2>/dev/null
fi

# Stage 2.5: Verify critical files are intact
write_status "pulling" "Verifying update..." ""
VERIFY_RESULT=$(verify_files)
if [ $? -ne 0 ]; then
    write_status "error" "Update failed verification: $VERIFY_RESULT. Rolling back..." "true"
    # Roll back to previous working commit
    if [ -n "$PREV_COMMIT" ]; then
        git reset --hard "$PREV_COMMIT" 2>/dev/null
        chown -R pi:pi "$SCRIPT_DIR" 2>/dev/null
        echo "Rolled back to $PREV_COMMIT"
    fi
    exit 1
fi

# Clean up old git objects to prevent storage growth
git gc --auto 2>/dev/null

# Stage 2.75: Update service files
# rm -f first — if masked, the file is a symlink to /dev/null, and cp follows it
if [ -f "$SCRIPT_DIR/inkslab.service" ]; then
    rm -f /etc/systemd/system/inkslab.service
    cp "$SCRIPT_DIR/inkslab.service" /etc/systemd/system/inkslab.service 2>/dev/null
fi
if [ -f "$SCRIPT_DIR/inkslab_web.service" ]; then
    rm -f /etc/systemd/system/inkslab_web.service
    cp "$SCRIPT_DIR/inkslab_web.service" /etc/systemd/system/inkslab_web.service 2>/dev/null
fi
systemctl daemon-reload 2>/dev/null

# Stage 3: Restart display daemon
write_status "restarting_display" "Restarting display service..." ""
if ! sudo systemctl restart inkslab 2>&1; then
    write_status "error" "Display service restart failed. Try rebooting." "true"
    exit 1
fi

# Stage 4: Pre-restart web — write status before we kill ourselves
write_status "restarting_web" "Restarting web dashboard... Reconnecting shortly." ""
sleep 1

# Stage 5: Restart web dashboard (this kills the Flask process, but we're detached)
if ! sudo systemctl restart inkslab_web 2>&1; then
    write_status "error" "Web service restart failed. Try rebooting." "true"
    exit 1
fi

# Wait for web to come back up
sleep 3

# Stage 6: Complete
write_status "complete" "Update complete! Page will reload." ""
