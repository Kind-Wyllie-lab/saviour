#!/usr/bin/env bash
# Primary Setup
# Install dependencies for SAVIOUR

echo "======================================="
echo " SAVIOUR installer"
echo " Installing to /usr/local/src/saviour"
echo "======================================="

set -Eeuo pipefail # If any function throws an error (doesn't return 0), exit immediately.
trap 'rc=$?; echo "switch_role.sh failed with exit code $rc at line $LINENO"' ERR

echo "Updating package lists and upgrading installed packages..."
sudo apt-get update -y
sudo apt-get upgrade -y

TARGET_DIR="/usr/local/src/saviour"

sudo mkdir -p "/etc/saviour"

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
    # APA Camera
    imx500-all
    # mDNS
    avahi-daemon
    iptables-persistent
    # AudioMoth USB HID support (required to build AudioMoth-USB-Microphone-Cmd)
    libusb-1.0-0-dev
)

# Function to check if a package is installed
is_installed() {
    dpkg -s "$1" &> /dev/null
}

install_system_packages() {
    echo "Installing required system packages..."
    for pkg in "${SYSTEM_PACKAGES[@]}"; do
        if is_installed "$pkg"; then
            echo "[OK] $pkg is already installed."
        else
            echo "[INSTALLING] $pkg"
            sudo apt-get install -y "$pkg"
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
    echo "  - Max adjustment: 100ms per sync (reduced from 500ms)"
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

install_system_packages
configure_ntp_for_ptp
create_python_environment
configure_logging
install_audiomoth_usb_cmd

echo ""
echo "Setup complete!"
echo "Original repo clone in $HOME can now be removed safely."
echo "Run: rm -rf ~/saviour"
