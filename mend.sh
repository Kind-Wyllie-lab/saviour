#!/usr/bin/env bash
# mend.sh — SAVIOUR repair installer
#
# Brings an existing SAVIOUR device up to date without touching its role/type
# configuration.  Safe to run on any device at any time.
#
# What it does:
#   1. Installs any missing system packages
#   2. Rebuilds the Python virtual environment
#   3. Rebuilds the frontend (controller only)
#   4. Rebuilds AudioMoth-USB-Microphone if missing or binary is stale
#   5. Installs / refreshes the saviour-config symlink
#   6. Applies logging and NTP configuration
#   7. Restarts the saviour service if it is running
#
# What it does NOT do:
#   - Pull or otherwise change the SAVIOUR code itself -- code version is
#     handled entirely by deploying a ZIP package via the web UI, not git
#   - Overwrite /etc/saviour/config (role/type/IP are preserved)
#   - Upgrade the OS (apt-get upgrade is intentionally omitted)

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    echo "mend.sh must not be sourced.  Run:  sudo bash mend.sh" >&2
    return 1
fi

set -Eeuo pipefail
trap 'echo "mend.sh failed at line $LINENO (exit $?)" >&2' ERR

TARGET_DIR="/usr/local/src/saviour"
LOG="/var/log/saviour-mend.log"

# ── Helpers ────────────────────────────────────────────────────────────────────

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

section() {
    echo ""
    echo "───────────────────────────────────────"
    echo "  $1"
    echo "───────────────────────────────────────"
}

ok()   { echo "  [OK]      $1"; }
fix()  { echo "  [FIXING]  $1"; }
warn() { echo "  [WARN]    $1"; }

is_installed() { dpkg -s "$1" &>/dev/null; }

# ── Root check ─────────────────────────────────────────────────────────────────

if [ "$EUID" -ne 0 ]; then
    echo "mend.sh must be run as root:  sudo bash mend.sh" >&2
    exit 1
fi

echo ""
echo "======================================="
echo " SAVIOUR mend installer"
echo " Device: $(hostname)"
echo "======================================="

# Show current role so the user can confirm they're on the right device
if [ -f /etc/saviour/config ]; then
    # shellcheck source=/dev/null
    source /etc/saviour/config
    echo " Role: ${ROLE:-none}  |  Type: ${TYPE:-none}"
else
    warn "/etc/saviour/config not found — device may never have been configured"
fi
echo ""

cd "$TARGET_DIR"

# ── 1. System packages ─────────────────────────────────────────────────────────

section "1/7  System packages"

sudo apt-get update -y -qq || warn "apt-get update had errors — some repositories may be unavailable; continuing"

# Suppress iptables-persistent's interactive "save current rules?" prompts --
# without this, a fresh install of iptables-persistent under a non-interactive
# shell (e.g. run over the update pipeline) hangs on a prompt with no TTY.
echo "iptables-persistent iptables-persistent/autosave_v4 boolean false" | sudo debconf-set-selections
echo "iptables-persistent iptables-persistent/autosave_v6 boolean false" | sudo debconf-set-selections

SYSTEM_PACKAGES=(
    linuxptp
    ffmpeg
    libavcodec-extra
    python3-picamera2
    python3-libcamera
    python3-kms++
    libcap-dev
    python3-dev
    build-essential
    libopenjp2-7
    libtiff6
    libjpeg-dev
    libpng-dev
    samba
    samba-common-bin
    cifs-utils
    dnsmasq
    avahi-daemon
    iptables-persistent
    libusb-1.0-0-dev
)

OPTIONAL_PACKAGES=(
    imx500-all
)

for pkg in "${SYSTEM_PACKAGES[@]}"; do
    if is_installed "$pkg"; then
        ok "$pkg"
    else
        fix "$pkg"
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg" >> "$LOG" 2>&1
    fi
done

for pkg in "${OPTIONAL_PACKAGES[@]}"; do
    if is_installed "$pkg"; then
        ok "$pkg (optional)"
    else
        fix "$pkg (optional)"
        if ! sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg" >> "$LOG" 2>&1; then
            warn "$pkg could not be installed — skipping (only needed for APA camera)"
        fi
    fi
done

# ── 2. Python environment ──────────────────────────────────────────────────────

section "2/7  Python environment"

if [ ! -d "$TARGET_DIR/env" ]; then
    fix "Creating virtual environment"
    python3 -m venv "$TARGET_DIR/env" --system-site-packages
fi

source "$TARGET_DIR/env/bin/activate"

pip install --quiet --upgrade pip >> "$LOG" 2>&1
pip install --quiet -e "$TARGET_DIR" >> "$LOG" 2>&1 || warn "pip install -e . failed (may lack build deps on offline device) — continuing"
pip install --quiet --force-reinstall simplejpeg >> "$LOG" 2>&1

ok "Python environment up to date"

# ── 3. Frontend build (controller only) ───────────────────────────────────────

section "3/7  Frontend build"

# Determine role: prefer /etc/saviour/config, fall back to detecting a running controller service
DETECTED_ROLE="none"
if [ -f /etc/saviour/config ]; then
    # shellcheck source=/dev/null
    source /etc/saviour/config
    DETECTED_ROLE="${ROLE:-none}"
elif systemctl is-active --quiet saviour.service && \
     journalctl -u saviour.service -n 50 --no-pager 2>/dev/null | grep -q "controller"; then
    warn "No /etc/saviour/config — detected running controller service, assuming role=controller"
    DETECTED_ROLE="controller"
    TYPE="${TYPE:-unknown}"
else
    warn "No /etc/saviour/config found — skipping frontend build"
fi

if [ "$DETECTED_ROLE" = "controller" ]; then
    NVM_DIR="/home/${SUDO_USER:-pi}/.nvm"
    export NVM_DIR
    # shellcheck source=/dev/null
    [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"

    if command -v npm &>/dev/null; then
        fix "Rebuilding frontend for ${TYPE:-unknown} controller"
        cd "$TARGET_DIR/src/controller/frontend"
        npm install --silent >> "$LOG" 2>&1
        npm run build >> "$LOG" 2>&1
        cd "$TARGET_DIR"
        ok "Frontend rebuilt"
    else
        warn "npm not found — skipping frontend build"
        warn "Run manually: cd $TARGET_DIR/src/controller/frontend && npm run build"
    fi
elif [ "$DETECTED_ROLE" != "none" ]; then
    ok "Module device — no frontend to rebuild"
fi

# ── 4. AudioMoth USB command ───────────────────────────────────────────────────

section "4/7  AudioMoth-USB-Microphone"

BINARY_PATH="/usr/local/bin/AudioMoth-USB-Microphone"
REPO="OpenAcousticDevices/AudioMoth-USB-Microphone-Cmd"

if [ -f "$BINARY_PATH" ]; then
    ok "Already installed at $BINARY_PATH"
else
    fix "Building from source"
    BUILD_DIR=$(mktemp -d)
    git clone --depth 1 "https://github.com/${REPO}.git" "$BUILD_DIR" >> "$LOG" 2>&1
    gcc -Wall -std=c99 \
        -I/usr/include/libusb-1.0 \
        -I"${BUILD_DIR}/src/linux/" \
        "${BUILD_DIR}/src/main.c" \
        "${BUILD_DIR}/src/linux/hid.c" \
        -o "${BUILD_DIR}/AudioMoth-USB-Microphone" \
        -lusb-1.0 -lrt -lpthread
    sudo cp "${BUILD_DIR}/AudioMoth-USB-Microphone" "$BINARY_PATH"
    sudo chmod +x "$BINARY_PATH"
    rm -rf "$BUILD_DIR"
    ok "Installed at $BINARY_PATH"
fi

# ── 5. saviour-config symlink ──────────────────────────────────────────────────

section "5/7  saviour-config"

SAVIOUR_CONFIG_SRC="$TARGET_DIR/saviour-config"
SAVIOUR_CONFIG_LINK="/usr/local/bin/saviour-config"

if [ ! -f "$SAVIOUR_CONFIG_SRC" ]; then
    warn "saviour-config not found at $SAVIOUR_CONFIG_SRC — skipping"
else
    chmod +x "$SAVIOUR_CONFIG_SRC"
    if [ "$(readlink -f "$SAVIOUR_CONFIG_LINK" 2>/dev/null)" = "$SAVIOUR_CONFIG_SRC" ]; then
        ok "Symlink already correct"
    else
        fix "Installing symlink $SAVIOUR_CONFIG_LINK → $SAVIOUR_CONFIG_SRC"
        ln -sf "$SAVIOUR_CONFIG_SRC" "$SAVIOUR_CONFIG_LINK"
    fi
fi

# ── 6. Logging + NTP ──────────────────────────────────────────────────────────

section "6/7  Logging + NTP"

# Persistent journald logging
if grep -q "Storage=persistent" /etc/systemd/journald.conf 2>/dev/null; then
    ok "Persistent logging already configured"
else
    fix "Enabling persistent journald logging"
    tee /etc/systemd/journald.conf > /dev/null <<EOF
[Journal]
Storage=persistent
EOF
    systemctl restart systemd-journald
fi

# NTP poll interval (reduce interference with PTP)
if grep -q "PollIntervalMinSec=300" /etc/systemd/timesyncd.conf 2>/dev/null; then
    ok "NTP already configured for PTP coexistence"
else
    fix "Configuring NTP for PTP coexistence"
    tee /etc/systemd/timesyncd.conf > /dev/null <<EOF
[Time]
NTP=time.nist.gov time.google.com pool.ntp.org
PollIntervalMinSec=300
PollIntervalMaxSec=3600
RootDistanceMaxSec=5
EOF
    timedatectl set-ntp true
    systemctl restart systemd-timesyncd
fi

# ── 7. Restart service ─────────────────────────────────────────────────────────

section "7/7  Service restart"

if systemctl is-active --quiet saviour.service; then
    fix "Restarting saviour.service to pick up code changes"
    systemctl restart saviour.service
    ok "saviour.service restarted"
elif systemctl is-enabled --quiet saviour.service 2>/dev/null; then
    fix "saviour.service is enabled but not running — starting it"
    systemctl start saviour.service
    ok "saviour.service started"
else
    warn "saviour.service is not installed — skipping (run sudo saviour-config to set up)"
fi

# ── Done ───────────────────────────────────────────────────────────────────────

echo ""
echo "======================================="
echo " Mend complete."
echo " Log: $LOG"

if [ -f /etc/saviour/config ]; then
    source /etc/saviour/config
    echo " Role: ${ROLE:-none}  |  Type: ${TYPE:-none}"
    if [ "${ROLE:-none}" = "none" ] || [ "${TYPE:-none}" = "none" ]; then
        echo ""
        echo " This device has no role assigned."
        echo " Run:  sudo saviour-config"
    fi
fi

echo "======================================="
echo ""
