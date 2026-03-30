#!/bin/bash
# Sync Python dependencies needed by modular InkSlab apps.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if ! python3 -m pip --version >/dev/null 2>&1; then
    apt-get update -qq >/dev/null 2>&1 || true
    apt-get install -y python3-pip >/dev/null 2>&1 || true
fi

if ! python3 -m pip install --break-system-packages -r "$SCRIPT_DIR/requirements.txt" >/dev/null 2>&1; then
    python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" >/dev/null 2>&1
fi
