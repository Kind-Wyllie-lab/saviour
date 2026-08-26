#!/usr/bin/env bash
# Primary Setup
# Install dependencies for SAVIOUR

echo "======================================="
echo " SAVIOUR installer"
echo " Installing to /usr/local/src/saviour"
echo "======================================="

set -Eeuo pipefail # If any function throws an error (doesn't return 0), exit immediately.
trap 'rc=$?; echo "switch_role.sh failed with exit code $rc at line $LINENO"' ERR

echo "Updating package lists..."
sudo apt-get update -y

TARGET_DIR="/usr/local/src/saviour"

sudo mkdir -p "/etc/saviour" "/etc/saviour/controller" "/etc/saviour/module"

sudo tee /etc/saviour/config > /dev/null <<EOF
ROLE=none
TYPE=none
EOF

# Resolve absolute path of this script
SCRIPT_PATH="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

if [[ "$SCRIPT_DIR" != "$TARGET_DIR" ]]; then
    echo "Relocating SAVIOUR to $TARGET_DIR..."

    sudo mkdir -p "$TARGET_DIR"

    # Move entire repo contents (not parent dir)
    sudo rsync -a --delete "$SCRIPT_DIR/" "$TARGET_DIR/"

    # Fix ownership so pi can work there
    sudo chown -R "$USER:$USER" "$TARGET_DIR"

    # Mark it as safe
    sudo git config --global --add safe.directory /usr/local/src/saviour

    echo "Re-running setup from $TARGET_DIR"
    exec "$TARGET_DIR/$(basename "$SCRIPT_PATH")"
fi

cd "$TARGET_DIR"

# List of required system packages
SYSTEM_PACKAGES=(
    linuxptp
    ffmpeg
    libavcodec-extra
    # Camera dependencies
    python3-picamera2
    python3-libcamera
    python3-kms++
    libcap-dev
    python3-dev
    build-essential
    libopenjp2-7
    libtiff6
    # Additional dependencies for image processing
    libjpeg-dev
    libpng-dev
    # File sharing dependencies
    samba
    samba-common-bin
    cifs-utils
    # DHCP server dependency
    dnsmasq
    # mDNS
    avahi-daemon
    iptables-persistent
    # AudioMoth USB HID support (required to build AudioMoth-USB-Microphone-Cmd)
    libusb-1.0-0-dev
)

# Optional packages that may not be available on all Pi OS versions or hardware
# configurations.  Failures are warned but do not abort the install.
OPTIONAL_PACKAGES=(
    imx500-all   # APA camera (Sony IMX500) — requires Pi OS Bookworm + Pi repo
)

# Function to check if a package is installed
is_installed() {
    dpkg -s "$1" &> /dev/null
}

install_system_packages() {
    echo "Installing required system packages..."

    # Suppress iptables-persistent's interactive "save current rules?" prompts.
    # Template owner is the binary package "iptables-persistent", not "iptables" --
    # a wrong prefix here preseeds a key debconf never reads, so the real
    # template keeps its default (true) and still prompts on install.
    echo "iptables-persistent iptables-persistent/autosave_v4 boolean false" | sudo debconf-set-selections
    echo "iptables-persistent iptables-persistent/autosave_v6 boolean false" | sudo debconf-set-selections

    for pkg in "${SYSTEM_PACKAGES[@]}"; do
        if is_installed "$pkg"; then
            echo "[OK] $pkg is already installed."
        else
            echo "[INSTALLING] $pkg"
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg"
        fi
    done

    echo "Installing optional system packages..."
    for pkg in "${OPTIONAL_PACKAGES[@]}"; do
        if is_installed "$pkg"; then
            echo "[OK] $pkg is already installed."
        else
            echo "[INSTALLING] $pkg"
            if ! sudo apt-get install -y "$pkg" 2>&1; then
                echo "[WARN] $pkg could not be installed — skipping. This is only needed for APA camera modules."
            fi
        fi
    done
}

create_python_environment() {
    if [ ! -d "env" ]; then
        python3 -m venv env --system-site-packages
        # python3 -m venv env 
    fi

    source env/bin/activate

    pip install --upgrade pip

    pip install -e .

    # Fix simplejpeg issue
    pip install --force-reinstall simplejpeg

    echo "Python dependencies installed"
}


configure_logging() {
    # Make logging persistent
    echo "Setting journald.conf to have persistent logging"
    sudo tee /etc/systemd/journald.conf > /dev/null <<EOF
[Journal]
Storage=persistent
EOF
}


# Function to configure NTP for PTP coexistence
configure_ntp_for_ptp() {
    echo "Configuring NTP for PTP Coexistence"
    
    # Backup original timesyncd config
    if [ -f /etc/systemd/timesyncd.conf ]; then
        sudo cp /etc/systemd/timesyncd.conf /etc/systemd/timesyncd.conf.backup
        echo "Backed up original timesyncd.conf"
    fi
    
    # Create optimized timesyncd configuration for PTP coexistence
    echo "Creating timesyncd configuration for PTP coexistence..."
    sudo tee /etc/systemd/timesyncd.conf > /dev/null <<EOF
# timesyncd configuration optimized for PTP coexistence
[Time]
# Use multiple time servers for redundancy
NTP=time.nist.gov time.google.com pool.ntp.org

# Reduce NTP adjustment frequency to minimize interference with PTP
# 5 minutes minimum (default is 32s)
PollIntervalMinSec=300
# 1 hour maximum (default is 34min)
PollIntervalMaxSec=3600

# Increase root distance to be more tolerant
# 5 second tolerance (default is 5s)
RootDistanceMaxSec=5

# Use hardware timestamping if available
# Hardware timestamping reduces interference with PTP
EOF

    # Enable NTP but with reduced frequency
    echo "Enabling NTP with reduced frequency..."
    sudo timedatectl set-ntp true
    
    # Restart timesyncd to apply new configuration
    sudo systemctl restart systemd-timesyncd
    
    echo "NTP configured for PTP coexistence"
    echo ""
    echo "NTP Configuration:"
    echo "  - Poll interval: 5 minutes to 1 hour (reduced frequency)"
    echo "  - Multiple time servers for redundancy"
    echo ""
    echo "NTP control commands:"
    echo "  Status: timedatectl status"
    echo "  Logs: sudo journalctl -u systemd-timesyncd -f"
    echo "  Restart: sudo systemctl restart systemd-timesyncd"
    echo "  Disable: sudo timedatectl set-ntp false"
    echo "  Enable: sudo timedatectl set-ntp true"
}

install_audiomoth_usb_cmd() {
    BINARY_PATH="/usr/local/bin/AudioMoth-USB-Microphone"
    REPO="OpenAcousticDevices/AudioMoth-USB-Microphone-Cmd"

    if [ -f "$BINARY_PATH" ]; then
        echo "[OK] AudioMoth-USB-Microphone already installed at $BINARY_PATH"
        return
    fi

    echo "Building AudioMoth-USB-Microphone from source..."

    BUILD_DIR=$(mktemp -d)
    git clone --depth 1 "https://github.com/${REPO}.git" "$BUILD_DIR"

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

    echo "[OK] AudioMoth-USB-Microphone installed at $BINARY_PATH"
}

configure_nvme_power_management() {
    # Raspberry Pi 5 + NVMe SSD root: the kernel's default NVMe autonomous
    # power-state transitions (APST) let the drive drop into a deep
    # power-saving state between writes. On this hardware combination that
    # can cause the drive to fail to wake up in time, hitting the kernel's
    # I/O timeout ("nvme nvme0: I/O tag ... timeout, aborting req_op:WRITE")
    # -- usually recoverable, but occasionally the controller never comes
    # back and the whole device hangs until a manual power cycle (found
    # 2026-08-25, live on a habitat controller: three such timeouts in one
    # ~22h boot, the third one fatal, ~5h of total unresponsiveness). Not an
    # undervoltage issue (`vcgencmd get_throttled` was 0x0 throughout).
    # Disabling APST (max latency 0) is the standard fix and only matters if
    # this device actually has an NVMe drive.
    if [ ! -e /sys/class/nvme/nvme0 ]; then
        return
    fi

    CMDLINE_FILE="/boot/firmware/cmdline.txt"
    if [ ! -f "$CMDLINE_FILE" ]; then
        echo "[WARN] $CMDLINE_FILE not found — skipping NVMe power-management fix"
        return
    fi

    if grep -q "nvme_core.default_ps_max_latency_us=" "$CMDLINE_FILE"; then
        echo "[OK] NVMe power-management fix already applied"
    else
        echo "[FIXING] Disabling NVMe autonomous power-state transitions (APST)"
        sudo sed -i -E "s/\$/ nvme_core.default_ps_max_latency_us=0/" "$CMDLINE_FILE"
        echo "[WARN] NVMe APST fix requires a reboot to take effect"
    fi
}

configure_psu_max_current() {
    # Every device in the fleet is a Pi 5 powered over PoE, not USB-C -- so
    # the Type-C PD negotiation the Pi 5 bootloader relies on to detect a
    # high-amp supply never happens, and it defaults to a conservative 3A
    # current budget regardless of which PoE HAT is actually fitted:
    # controllers use the 52Pi EP-0240 M.2 NVMe PoE+ HAT (up to 4.5A/25W),
    # modules mostly use the Waveshare "PoE HAT (F)" (also up to 4.5A over
    # its GPIO header). Both vendors document the same required fix (`sudo
    # rpi-eeprom-config --edit`, add `PSU_MAX_CURRENT=5000`) for the same
    # underlying Pi 5 firmware behaviour -- this isn't NVMe-specific, so
    # unlike the APST fix above it applies to every device, not just
    # controllers. Found completely unset on a live habitat controller
    # (2026-08-25) that had just needed a manual power cycle after an NVMe
    # hang -- a plausible contributing factor there alongside APST, and
    # worth closing fleet-wide regardless of whether a given device has
    # ever actually hit a power-starvation symptom.
    if ! command -v rpi-eeprom-config &> /dev/null; then
        echo "[WARN] rpi-eeprom-config not found — not a Raspberry Pi 4/5 bootloader, skipping"
        return
    fi

    if sudo rpi-eeprom-config 2>/dev/null | grep -q "^PSU_MAX_CURRENT=5000$"; then
        echo "[OK] PSU_MAX_CURRENT already set to 5000"
        return
    fi

    echo "[FIXING] Setting PSU_MAX_CURRENT=5000 in bootloader EEPROM config"
    TMP_CONF=$(mktemp)
    sudo rpi-eeprom-config > "$TMP_CONF"
    # Drop any existing (missing or wrong) value before appending the correct one
    sed -i '/^PSU_MAX_CURRENT=/d' "$TMP_CONF"
    echo "PSU_MAX_CURRENT=5000" >> "$TMP_CONF"
    sudo rpi-eeprom-config --apply "$TMP_CONF"
    rm -f "$TMP_CONF"
    echo "[WARN] PSU_MAX_CURRENT fix requires a reboot to take effect"
}

install_provision_service() {
    # Applies /etc/saviour/config's declared ROLE/TYPE (and, for a
    # controller, its network settings) non-interactively before
    # saviour.service starts -- what makes a card configurable purely by
    # editing that file over a USB mount and booting, no console/SSH
    # session needed. saviour-config --apply is idempotent: a no-op once
    # the declared config matches what was last actually applied.
    sudo tee /etc/systemd/system/saviour-provision.service > /dev/null <<EOF
[Unit]
Description=Apply SAVIOUR role/type from /etc/saviour/config
Before=saviour.service
ConditionPathExists=/etc/saviour/config

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/saviour-config --apply

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable saviour-provision.service
}

install_system_packages
configure_ntp_for_ptp
create_python_environment
configure_logging
configure_nvme_power_management
configure_psu_max_current
install_audiomoth_usb_cmd

# Install saviour-config as a system-wide command
sudo ln -sf "$TARGET_DIR/saviour-config" /usr/local/bin/saviour-config
sudo chmod +x "$TARGET_DIR/saviour-config"
install_provision_service

echo ""
echo "Setup complete!"
echo ""
echo "Next step: assign this device a role by running:"
echo "  sudo saviour-config"
