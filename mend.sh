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
#   6. Applies logging and NTP configuration; turns off background OS
#      update jobs on modules
#   7. Disables NVMe APST (power-state transitions) on devices with an NVMe root
#   8. Sets the PoE+ HAT's PSU_MAX_CURRENT bootloader budget (all Pi 4/5 devices)
#   9. Regenerates the saviour.service systemd unit and restarts it if running
#
# Steps 7 and 8 only take effect after a reboot. If either changed something,
# mend.sh marks /run/reboot-required, prints a REBOOT REQUIRED banner, and
# exits 10 (rather than 0) so a caller can tell "mended, reboot pending" from
# "mended, done". Pass --reboot to have it reboot the device itself when (and
# only when) a reboot is actually pending. Runs fully non-interactively either
# way (it is also invoked headless via the `run_mend` module command).
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
# A systemd-run unit (run_mend's detached launch) has no HOME, and nvm.sh reads
# it -- under set -u that aborted the controller's frontend-build step.
: "${HOME:=/root}"
export HOME
trap 'echo "mend.sh failed at line $LINENO (exit $?)" >&2' ERR

TARGET_DIR="/usr/local/src/saviour"
LOG="/var/log/saviour-mend.log"

# --reboot: reboot the device at the end IFF a reboot-dependent step changed
# something this run. Any other/unknown arg is rejected so a typo can't be
# silently ignored.
AUTO_REBOOT=0
for arg in "$@"; do
    case "$arg" in
        --reboot) AUTO_REBOOT=1 ;;
        -h|--help)
            sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "mend.sh: unknown argument '$arg' (accepts: --reboot)" >&2
            exit 2
            ;;
    esac
done

# Set to 1 by any step whose change only activates on reboot (7: NVMe APST,
# 8: PSU_MAX_CURRENT).
REBOOT_REQUIRED=0

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

section "1/8  System packages"

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

section "2/8  Python environment"

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

section "3/8  Frontend build"

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

    # Same installer saviour-config uses (official LTS build into /usr/local):
    # before 2026-10-02 nothing installed Node, so a fresh controller had no
    # web UI and this step only ever said "npm not found".
    if ! command -v npm &>/dev/null && [ -x /usr/local/bin/saviour-config ]; then
        fix "Node.js not found -- installing the official LTS build"
        /usr/local/bin/saviour-config --ensure-node >> "$LOG" 2>&1 && hash -r \
            || warn "Node.js install failed (no internet?)"
    fi
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

section "4/8  AudioMoth-USB-Microphone"

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

section "5/8  saviour-config"

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

section "6/8  Logging + NTP"

# Persistent journald logging, with a disk-use cap so a chatty run can't
# fill the filesystem (disk-full is itself a data-loss trigger).
# Raspberry Pi OS ships /usr/lib/systemd/journald.conf.d/
# 40-rpi-volatile-storage.conf (Storage=volatile), and drop-ins override
# /etc/systemd/journald.conf -- so writing Storage=persistent there never
# took effect on any Pi in the fleet (found 2026-09-30: every module's
# journal was RAM-only, which is why reboots left nothing to diagnose).
# A drop-in that sorts after 40-rpi-* wins.
# PTP units: phc2sys's per-sample output out of the journal (see
# saviour-config's note above configure_ptp_timetransmitter).
if [ -f /etc/systemd/system/phc2sys.service ] \
        && { ! grep -q "/run/linuxptp/phc2sys.log" /etc/systemd/system/phc2sys.service \
             || ! grep -q -- " -S 1.0" /etc/systemd/system/phc2sys.service; }; then
    # Also catches units without the 1 s step threshold (saviour-config's
    # step-threshold note above _ptp_log_dir): without it a grandmaster clock jump leaves
    # modules slewing for weeks.
    fix "Rewriting PTP units (log path + step threshold; brief PTP resync)"
    saviour-config --apply-ptp-units >> "$LOG" 2>&1 \
        || warn "Could not rewrite PTP units — run 'sudo saviour-config --apply-ptp-units'"
else
    ok "PTP units up to date"
fi

JOURNALD_DROPIN=/etc/systemd/journald.conf.d/99-saviour.conf
effective_storage=$(systemd-analyze cat-config systemd/journald.conf 2>/dev/null \
    | grep -E '^Storage=' | tail -1 | cut -d= -f2)
if [ -f "$JOURNALD_DROPIN" ] && [ "$effective_storage" = "persistent" ] \
        && [ -n "$(ls -A /var/log/journal 2>/dev/null)" ]; then
    ok "Persistent logging already configured"
else
    fix "Enabling persistent journald logging (capped at 500M; was: ${effective_storage:-unknown})"
    mkdir -p /etc/systemd/journald.conf.d /var/log/journal
    tee "$JOURNALD_DROPIN" > /dev/null <<EOF
[Journal]
Storage=persistent
SystemMaxUse=500M
SystemKeepFree=1G
EOF
    systemctl restart systemd-journald
    journalctl --flush || true
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

# Modules run no OS update activity in the background: their code arrives
# through SAVIOUR (ZIP update / mend.sh), unattended-upgrades isn't installed,
# and these jobs compete with recording for CPU and I/O. The desktop panel's
# daily update check (PackageKit, at the boot time of day) and apt's daily
# list refresh each preceded capture stalls on a loaded camera. Undo with
# `systemctl unmask packagekit` and
# `systemctl enable --now apt-daily.timer apt-daily-upgrade.timer`.
if [ "$DETECTED_ROLE" = "module" ]; then
    if [ "$(systemctl is-enabled packagekit.service 2>/dev/null)" = "masked" ] \
            && ! systemctl is-enabled --quiet apt-daily.timer 2>/dev/null \
            && ! systemctl is-enabled --quiet apt-daily-upgrade.timer 2>/dev/null; then
        ok "Background OS update jobs already off"
    else
        fix "Turning off background OS update jobs (PackageKit, apt-daily timers)"
        systemctl stop packagekit.service 2>/dev/null || true
        systemctl mask packagekit.service >/dev/null 2>&1 \
            || warn "Could not mask packagekit.service"
        systemctl disable --now apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 \
            || warn "Could not disable the apt-daily timers"
    fi
fi

# ── 7. NVMe power management ───────────────────────────────────────────────────

section "7/9  NVMe power management"

# Raspberry Pi 5 + NVMe SSD root: default NVMe autonomous power-state
# transitions (APST) let the drive drop into a deep power-saving state
# between writes, and on this hardware combination that can cause it to fail
# to wake in time -- usually a recoverable I/O timeout, but occasionally the
# controller never comes back and the whole device hangs until a manual
# power cycle (found 2026-08-25, live on a habitat controller: three such
# timeouts in one ~22h boot, the third fatal, ~5h unresponsive). Not an
# undervoltage issue (`vcgencmd get_throttled` was 0x0 throughout).
if [ ! -e /sys/class/nvme/nvme0 ]; then
    ok "No NVMe device — nothing to do"
else
    CMDLINE_FILE="/boot/firmware/cmdline.txt"
    if [ ! -f "$CMDLINE_FILE" ]; then
        warn "$CMDLINE_FILE not found — skipping NVMe power-management fix"
    elif grep -q "nvme_core.default_ps_max_latency_us=" "$CMDLINE_FILE"; then
        ok "NVMe APST fix already applied"
    else
        fix "Disabling NVMe autonomous power-state transitions (APST)"
        sed -i -E "s/\$/ nvme_core.default_ps_max_latency_us=0/" "$CMDLINE_FILE"
        warn "NVMe APST fix requires a reboot to take effect"
        REBOOT_REQUIRED=1
    fi
fi

# ── 8. PSU_MAX_CURRENT (fleet-wide: every device is Pi 5 + a PoE HAT) ─────────

section "8/9  PoE HAT power budget"

# Every device in the fleet is a Pi 5 powered over PoE, not USB-C -- so the
# Type-C PD negotiation the Pi 5 bootloader relies on to detect a high-amp
# supply never happens, and it defaults to a conservative 3A current budget
# regardless of which PoE HAT is actually fitted: controllers use the 52Pi
# EP-0240 M.2 NVMe PoE+ HAT (up to 4.5A/25W), modules mostly use the
# Waveshare "PoE HAT (F)" (also up to 4.5A over its GPIO header). Both
# vendors document the same required fix (PSU_MAX_CURRENT=5000) for the same
# underlying Pi 5 firmware behaviour -- this isn't NVMe-specific, so unlike
# the APST step above it applies to every device, not just controllers.
# Found completely unset on a live habitat controller (2026-08-25) that had
# just needed a manual power cycle after an NVMe hang -- a plausible
# contributing factor there alongside APST, worth closing fleet-wide
# regardless of whether a given device has ever actually hit a
# power-starvation symptom.
if ! command -v rpi-eeprom-config &>/dev/null; then
    warn "rpi-eeprom-config not found — not a Raspberry Pi 4/5 bootloader, skipping"
elif rpi-eeprom-config 2>/dev/null | grep -q "^PSU_MAX_CURRENT=5000$"; then
    ok "PSU_MAX_CURRENT already set to 5000"
else
    fix "Setting PSU_MAX_CURRENT=5000 in bootloader EEPROM config"
    TMP_CONF=$(mktemp)
    rpi-eeprom-config > "$TMP_CONF"
    sed -i '/^PSU_MAX_CURRENT=/d' "$TMP_CONF"
    echo "PSU_MAX_CURRENT=5000" >> "$TMP_CONF"
    if rpi-eeprom-config --apply "$TMP_CONF" >> "$LOG" 2>&1; then
        warn "PSU_MAX_CURRENT fix requires a reboot to take effect"
        REBOOT_REQUIRED=1
    else
        warn "Failed to apply PSU_MAX_CURRENT — check $LOG"
    fi
    rm -f "$TMP_CONF"
fi

# ── 9. Config re-apply + service restart ──────────────────────────────────────

section "9/9  Config re-apply + service restart"

# saviour-config bakes the current code layout (e.g. src/*/variants/<type>)
# into /etc/systemd/system/saviour.service as literal text; a plain code
# update (this script never touches the unit file otherwise) leaves a
# device's unit file pointing at wherever the layout was when
# configure_service() last ran. Regenerating it here means mend.sh alone is
# enough to recover a device after a code-layout change, without a full
# interactive `sudo saviour-config` run.
if [ -f /etc/saviour/config ]; then
    # shellcheck source=/dev/null
    source /etc/saviour/config
    if [ "${ROLE:-none}" != "none" ] && [ "${TYPE:-none}" != "none" ]; then
        if [ -x "$SAVIOUR_CONFIG_LINK" ]; then
            fix "Regenerating saviour.service unit"
            "$SAVIOUR_CONFIG_LINK" --regenerate-service >> "$LOG" 2>&1 \
                && ok "saviour.service unit regenerated" \
                || warn "Could not regenerate saviour.service unit — run 'sudo saviour-config' manually"

            # apply_from_config() (the boot-time --apply path) is a no-op
            # unless ROLE/TYPE/GATEWAY/IP changed, so the wlan0/WAN firewall
            # and smb.conf interface binding won't reach an existing fleet on
            # a plain reboot. Re-apply them explicitly here.
            fix "Applying wlan0/WAN firewall + smb.conf interface binding"
            "$SAVIOUR_CONFIG_LINK" --apply-firewall >> "$LOG" 2>&1 \
                && ok "Firewall / interface binding applied" \
                || warn "Could not apply firewall — run 'sudo saviour-config --apply-firewall' manually"
        else
            warn "saviour-config not installed yet (see step 5 above) — skipping unit regeneration"
        fi
    fi
fi

# Chromium profile locks inherited from a cloned image: SingletonLock is a
# symlink to "<hostname>-<pid>", and one naming another machine makes the
# browser refuse with "profile in use by another computer" (2026-10-02).
# Only locks naming a *different* host are removed, so a Chromium actually
# running on this device is never touched.
for lock in /home/*/.config/chromium/SingletonLock /root/.config/chromium/SingletonLock; do
    [ -L "$lock" ] || continue
    owner=$(readlink "$lock")
    if [ "${owner%-*}" != "$(hostname)" ]; then
        fix "Removing stale Chromium profile lock from ${owner%-*} ($lock)"
        rm -f "$lock" "$(dirname "$lock")/SingletonCookie" "$(dirname "$lock")/SingletonSocket"
    fi
done

# Legacy per-type units (e.g. saviour-camera-module.service) from before the
# single saviour.service. Found 2026-10-02 crash-looping every 2 s on a desk
# camera (restart counter 15136, CHDIR into a path that no longer exists).
for legacy in /etc/systemd/system/saviour-*-module.service; do
    [ -f "$legacy" ] || continue
    unit=$(basename "$legacy")
    fix "Removing legacy unit $unit"
    systemctl disable --now "$unit" >> "$LOG" 2>&1 || true
    rm -f "$legacy"
    systemctl daemon-reload
done

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

# ── Reboot handling ───────────────────────────────────────────────────────────
# Steps 7/8 only take effect on reboot. If either changed something this run,
# leave a marker (the same one Debian's update-notifier uses so any existing
# tooling sees it), print an unmissable banner, and exit 10 so a caller can
# distinguish "reboot pending" from a clean run. With --reboot, do it here.
if [ "$REBOOT_REQUIRED" -eq 1 ]; then
    {
        echo "*** REBOOT REQUIRED ***"
        echo "mend.sh applied a bootloader/kernel setting (NVMe APST and/or"
        echo "PSU_MAX_CURRENT) that only activates after a reboot."
    } | tee -a "$LOG"
    printf 'saviour mend: NVMe APST / PSU_MAX_CURRENT change pending reboot\n' \
        > /run/reboot-required 2>/dev/null || true

    if [ "$AUTO_REBOOT" -eq 1 ]; then
        log "--reboot given and a reboot is pending — rebooting now"
        echo " Rebooting now (--reboot)."
        echo "======================================="
        sync
        systemctl reboot
        exit 0
    fi

    echo ""
    echo " Reboot this device when convenient:  sudo reboot"
    echo " (re-run with --reboot to have mend.sh do it automatically)"
    echo "======================================="
    echo ""
    exit 10
fi
