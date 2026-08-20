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
    # --depth 1 alone implies --single-branch (git default since 1.9), which
    # narrows remote.origin.fetch to just the cloned branch -- no other
    # branch's refs/objects are fetched at all, so `git checkout <branch>`
    # fails outright on a device provisioned this way. --no-single-branch
    # keeps every branch shallow (depth 1 each) but checkout-able, at
    # near-zero extra disk cost versus a true single-branch shallow clone.
    sudo git clone --depth 1 --no-single-branch "$REPO" "$TARGET"
    sudo chown -R "$USER:$USER" "$TARGET"
    sudo git config --global --add safe.directory "$TARGET"
fi

echo "Handing off to setup.sh..."
bash "$TARGET/setup.sh"
