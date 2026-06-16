#!/usr/bin/env bash
# SAVIOUR bootstrap installer
#
# Usage (fresh device, run as the pi user):
#   curl -fsSL https://raw.githubusercontent.com/Kind-Wyllie-lab/saviour/main/install.sh | bash

set -euo pipefail

TARGET="/usr/local/src/saviour"
REPO="https://github.com/Kind-Wyllie-lab/saviour.git"

echo "======================================="
echo " SAVIOUR bootstrap"
echo " Target: $TARGET"
echo "======================================="

if [ -d "$TARGET/.git" ]; then
    echo "Repo already exists at $TARGET, pulling latest..."
    git -C "$TARGET" pull --ff-only
else
    echo "Cloning SAVIOUR to $TARGET..."
    sudo git clone --depth 1 "$REPO" "$TARGET"
    sudo chown -R "$USER:$USER" "$TARGET"
    sudo git config --global --add safe.directory "$TARGET"
fi

echo "Handing off to setup.sh..."
bash "$TARGET/setup.sh"
