#!/usr/bin/env bash
# download_hefs.sh — fetch the curated stock Hailo model-zoo HEFs used by the
# hailo_camera module. Run automatically by saviour-config when the module type
# is set (POST_INSTALL in variant.conf); safe to re-run any time.
#
#   sudo bash download_hefs.sh                 # auto-detect Hailo-8 vs 8L
#   sudo bash download_hefs.sh --arch hailo8l  # force an arch
#   sudo bash download_hefs.sh --force         # re-download even if present
#
# HEFs land in /usr/local/src/saviour/hailo_models/ (MODEL_DIR in
# src/modules/hailo_infer.py). Keep the HEFS list in sync with CURATED_MODELS.

set -Eeuo pipefail

MODEL_DIR="/usr/local/src/saviour/hailo_models"
ZOO_VERSION="${HAILO_ZOO_VERSION:-v2.14.0}"   # bump if a HEF 404s
ARCH=""
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --arch)  ARCH="$2"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

# ── Detect the fitted accelerator ────────────────────────────────────────────
if [ -z "$ARCH" ]; then
    if command -v hailortcli >/dev/null 2>&1; then
        # "Device Architecture: HAILO8" | "HAILO8L"
        det=$(hailortcli fw-control identify 2>/dev/null \
              | grep -i 'Device Architecture' | tr -d ' ' | cut -d: -f2 | tr 'A-Z' 'a-z' || true)
        case "$det" in
            hailo8l) ARCH="hailo8l" ;;
            hailo8)  ARCH="hailo8"  ;;
        esac
    fi
fi
if [ -z "$ARCH" ]; then
    echo "Could not detect the Hailo architecture (hailortcli / device not ready)." >&2
    echo "Re-run with:  --arch hailo8   or   --arch hailo8l" >&2
    exit 1
fi
case "$ARCH" in hailo8|hailo8l) ;; *) echo "arch must be hailo8 or hailo8l" >&2; exit 2 ;; esac

# Re-download automatically if the last fetch was for a different arch — the
# HEF filenames don't encode arch, so a stale set would silently mismatch.
mkdir -p "$MODEL_DIR"
ARCH_MARKER="$MODEL_DIR/.arch"
if [ "$FORCE" -eq 0 ] && [ "$(cat "$ARCH_MARKER" 2>/dev/null || echo none)" != "$ARCH" ]; then
    [ -f "$ARCH_MARKER" ] && echo "Stored HEFs are for a different arch — re-downloading for ${ARCH}"
    FORCE=1
fi

# Keep in sync with CURATED_MODELS in src/modules/hailo_infer.py
HEFS=(yolov8s yolov6n yolov8m yolov11n yolov8s_pose yolov8m_pose)
BASE="https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/${ZOO_VERSION}/${ARCH}"

echo "Downloading ${#HEFS[@]} HEF(s) for ${ARCH} (zoo ${ZOO_VERSION}) into ${MODEL_DIR}"
fail=0
for name in "${HEFS[@]}"; do
    dst="${MODEL_DIR}/${name}.hef"
    if [ -s "$dst" ] && [ "$FORCE" -eq 0 ]; then
        echo "  [skip]  ${name}.hef"
        continue
    fi
    echo "  [get]   ${name}.hef"
    if ! curl -fSL --retry 3 -o "${dst}.part" "${BASE}/${name}.hef"; then
        echo "  [FAIL]  ${name}.hef — ${BASE}/${name}.hef (try HAILO_ZOO_VERSION=<newer>)" >&2
        rm -f "${dst}.part"
        fail=1
        continue
    fi
    mv "${dst}.part" "$dst"
done

if [ "$fail" -eq 0 ]; then
    echo "$ARCH" > "$ARCH_MARKER"
    echo "Done."
else
    echo "One or more downloads failed." >&2
    exit 1
fi
