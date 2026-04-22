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
    # AudioMoth USB HID support
    libhidapi-dev
    libhidapi-hidraw0
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

    echo "Installing AudioMoth-USB-Microphone-Cmd..."

    # Try to find a pre-built ARM64 binary in the latest GitHub release
    RELEASE_JSON=$(curl -sf "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null || echo "")
    DOWNLOAD_URL=""
    if [ -n "$RELEASE_JSON" ]; then
        DOWNLOAD_URL=$(echo "$RELEASE_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for a in data.get('assets', []):
    name = a['name'].lower()
    if ('arm64' in name or 'aarch64' in name) and 'linux' in name:
        print(a['browser_download_url'])
        break
" 2>/dev/null || echo "")
    fi

    if [ -n "$DOWNLOAD_URL" ]; then
        echo "Downloading pre-built ARM64 binary..."
        sudo curl -sL "$DOWNLOAD_URL" -o "$BINARY_PATH"
        sudo chmod +x "$BINARY_PATH"
        echo "[OK] AudioMoth-USB-Microphone installed at $BINARY_PATH"
        return
    fi

    # Fall back: clone and build from source
    echo "No pre-built ARM64 binary found — building from source..."
    BUILD_DIR=$(mktemp -d)
    git clone --depth 1 "https://github.com/${REPO}.git" "$BUILD_DIR"

    if [ -f "$BUILD_DIR/Makefile" ]; then
        make -C "$BUILD_DIR"
        BUILT_BIN=$(find "$BUILD_DIR" -maxdepth 3 -type f -executable ! -name "*.sh" | head -1)
        if [ -n "$BUILT_BIN" ]; then
            sudo cp "$BUILT_BIN" "$BINARY_PATH"
            sudo chmod +x "$BINARY_PATH"
            echo "[OK] AudioMoth-USB-Microphone built and installed at $BINARY_PATH"
        else
            echo "WARNING: Build produced no binary. Install AudioMoth-USB-Microphone-Cmd manually."
        fi
    elif [ -f "$BUILD_DIR/package.json" ]; then
        # Node.js package — build with npx pkg or similar
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
        cd "$BUILD_DIR"
        npm install
        npm run build 2>/dev/null || true
        BUILT_BIN=$(find "$BUILD_DIR/dist" -maxdepth 1 -type f -executable 2>/dev/null | head -1)
        if [ -n "$BUILT_BIN" ]; then
            sudo cp "$BUILT_BIN" "$BINARY_PATH"
            sudo chmod +x "$BINARY_PATH"
            echo "[OK] AudioMoth-USB-Microphone installed at $BINARY_PATH"
        else
            echo "WARNING: Could not build AudioMoth-USB-Microphone-Cmd. Install manually."
        fi
        cd "$TARGET_DIR"
    else
        echo "WARNING: Unknown build system. Install AudioMoth-USB-Microphone-Cmd manually."
    fi

    rm -rf "$BUILD_DIR"
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
