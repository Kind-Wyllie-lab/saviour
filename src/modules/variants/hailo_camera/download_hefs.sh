#!/usr/bin/env bash
# download_hefs.sh — fetch the curated stock Hailo model-zoo HEFs used by the
# hailo_camera module. Run once per module (or after switching Hailo-8 <-> 8L).
#
#   sudo bash download_hefs.sh                # Hailo-8  (default)
#   sudo bash download_hefs.sh --arch hailo8l # Hailo-8L (AI Kit / AI HAT+ 13 TOPS)
#   sudo bash download_hefs.sh --force        # re-download even if present
#
# Detect which chip is fitted:  lspci | grep -i hailo   (says "Hailo-8" or "-8L")
#
# HEFs land in /usr/local/src/saviour/hailo_models/ (MODEL_DIR in
# src/modules/hailo_infer.py). Keep this list in sync with CURATED_MODELS there.

set -Eeuo pipefail

MODEL_DIR="/usr/local/src/saviour/hailo_models"
ZOO_VERSION="${HAILO_ZOO_VERSION:-v2.14.0}"   # bump if a HEF 404s
ARCH="hailo8"
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --arch)  ARCH="$2"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

case "$ARCH" in hailo8|hailo8l) ;; *) echo "arch must be hailo8 or hailo8l" >&2; exit 2 ;; esac

# Keep in sync with CURATED_MODELS in src/modules/hailo_infer.py
HEFS=(yolov8s yolov6n yolov8m yolov11n)

BASE="https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/${ZOO_VERSION}/${ARCH}"

mkdir -p "$MODEL_DIR"
echo "Downloading ${#HEFS[@]} HEF(s) for ${ARCH} (zoo ${ZOO_VERSION}) into ${MODEL_DIR}"

fail=0
for name in "${HEFS[@]}"; do
    dst="${MODEL_DIR}/${name}.hef"
    if [ -s "$dst" ] && [ "$FORCE" -eq 0 ]; then
        echo "  [skip]  ${name}.hef (already present)"
        continue
    fi
    echo "  [get]   ${name}.hef"
    if ! curl -fSL --retry 3 -o "${dst}.part" "${BASE}/${name}.hef"; then
        echo "  [FAIL]  ${name}.hef — ${BASE}/${name}.hef (try HAILO_ZOO_VERSION=<newer> or check the chip arch)" >&2
        rm -f "${dst}.part"
        fail=1
        continue
    fi
    mv "${dst}.part" "$dst"
done

[ "$fail" -eq 0 ] && echo "Done." || { echo "One or more downloads failed." >&2; exit 1; }
