#!/usr/bin/env bash
# POST_INSTALL hook (see variant.conf) — install pypylon into the module venv.
#
# pypylon ships manylinux + aarch64 wheels that bundle the Pylon runtime, so
# `pip install` is all that's needed on a Raspberry Pi 5 (Bookworm/arm64). If a
# wheel is ever unavailable for the target platform, fall back to installing the
# Basler "pylon" .deb from https://www.baslerweb.com/en/downloads/software/ and
# then `pip install pypylon --no-binary :all:`.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# repo root is variants/basler_camera -> ../../../.. from this file
VENV_PIP="$HERE/../../../../env/bin/pip"

if [[ ! -x "$VENV_PIP" ]]; then
    echo "install_pypylon.sh: module venv pip not found at $VENV_PIP" >&2
    exit 1
fi

"$VENV_PIP" install --upgrade "pypylon>=4.0"
echo "install_pypylon.sh: pypylon installed"
