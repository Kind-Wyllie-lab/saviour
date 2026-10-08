#!/usr/bin/env bash
# Flash the APA motor and shock Arduinos from this checkout's sketches.
#
# Run on the apa_arduino module Pi, as the normal login user (it needs the
# serial ports: dialout group), from anywhere:
#   scripts/flash_apa_arduinos.sh [--fqbn arduino:avr:uno] [--only motor|shock]
#
# Each board is found by asking it for its identity (<I:MOTOR> / <I:SHOCK>),
# so ttyACM numbering doesn't matter. Both sketches are compiled before the
# saviour service is stopped; the service is restarted on exit (also on
# failure) if it was running. arduino-cli is installed to ~/.local/bin if
# missing.
set -euo pipefail

FQBN="arduino:avr:uno"
ONLY=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --fqbn) FQBN="$2"; shift 2 ;;
        --only) ONLY="$2"; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKETCHES="$ROOT/src/modules/variants/apa_arduino/arduino"
PY="$ROOT/env/bin/python"; [[ -x "$PY" ]] || PY=python3
BUILD="$(mktemp -d)"
SERVICE_WAS_ACTIVE=0

cleanup() {
    rm -rf "$BUILD"
    if [[ $SERVICE_WAS_ACTIVE == 1 ]]; then
        echo "Restarting saviour.service"
        sudo systemctl start saviour.service
    fi
}
trap cleanup EXIT

# --- arduino-cli, core, libraries -------------------------------------------
if ! command -v arduino-cli >/dev/null; then
    export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v arduino-cli >/dev/null; then
    echo "Installing arduino-cli to ~/.local/bin"
    mkdir -p "$HOME/.local/bin"
    curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
        | BINDIR="$HOME/.local/bin" sh
fi
arduino-cli core update-index >/dev/null
arduino-cli core install "${FQBN%:*}"
arduino-cli lib install TimerOne DualG2HighPowerMotorShield

# --- compile first, so a broken sketch never stops the rig ------------------
declare -A SKETCH=([motor]=motor_controller [shock]=shock_controller)
TARGETS=(motor shock)
[[ -n "$ONLY" ]] && TARGETS=("$ONLY")
for t in "${TARGETS[@]}"; do
    [[ -n "${SKETCH[$t]:-}" ]] || { echo "--only must be motor or shock" >&2; exit 2; }
    echo "Compiling ${SKETCH[$t]} for $FQBN"
    arduino-cli compile --fqbn "$FQBN" --output-dir "$BUILD/$t" "$SKETCHES/${SKETCH[$t]}"
done

# --- free the ports ------------------------------------------------------------
if systemctl is-active --quiet saviour.service; then
    echo "Stopping saviour.service (motor stops and shock turns off on SIGTERM)"
    sudo systemctl stop saviour.service
    SERVICE_WAS_ACTIVE=1
fi

# Prints the board's identity (motor/shock), or nothing. Opening the port
# resets the board, and setup() announces itself, so just listen for it.
identify() {
    "$PY" - "$1" <<'EOF'
import re, sys, time
import serial
try:
    with serial.Serial(sys.argv[1], 115200, timeout=0.2) as s:
        buf, deadline = "", time.monotonic() + 6
        asked = False
        while time.monotonic() < deadline:
            buf += s.read(256).decode("utf-8", errors="ignore")
            m = re.search(r"<I:(MOTOR|SHOCK)>", buf)
            if m:
                print(m.group(1).lower())
                break
            if not asked and time.monotonic() > deadline - 3:
                s.write(b"<I:>")  # never announced: ask
                asked = True
except (OSError, serial.SerialException):
    pass
EOF
}

declare -A PORT=()
shopt -s nullglob
for dev in /dev/ttyACM* /dev/ttyUSB*; do
    id="$(identify "$dev")"
    echo "  $dev: ${id:-no APA identity}"
    [[ -n "$id" ]] && PORT[$id]="$dev"
done

# --- upload and confirm -----------------------------------------------------
for t in "${TARGETS[@]}"; do
    port="${PORT[$t]:-}"
    if [[ -z "$port" ]]; then
        echo "No $t Arduino found; is it plugged in and running the APA firmware?" >&2
        echo "(A blank board: flash it by hand with arduino-cli upload -p <port>.)" >&2
        exit 1
    fi
    echo "Uploading ${SKETCH[$t]} to $port"
    arduino-cli upload --fqbn "$FQBN" --input-dir "$BUILD/$t" -p "$port"
    [[ "$(identify "$port")" == "$t" ]] \
        || { echo "$t did not identify after upload on $port" >&2; exit 1; }
    echo "  $t OK on $port"
done
echo "Done."
