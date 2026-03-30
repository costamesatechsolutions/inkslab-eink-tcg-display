#!/bin/bash
# Sync Python dependencies needed by modular InkSlab apps.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v pip3 >/dev/null 2>&1; then
    apt-get update -qq >/dev/null 2>&1 || true
    apt-get install -y python3-pip >/dev/null 2>&1 || true
fi

if ! pip3 install --break-system-packages -r "$SCRIPT_DIR/requirements.txt" >/dev/null 2>&1; then
    pip3 install -r "$SCRIPT_DIR/requirements.txt" >/dev/null 2>&1
fi
