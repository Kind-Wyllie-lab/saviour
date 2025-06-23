#!/bin/bash
# setup.sh
# Install system dependencies and set up virtual environment for the habitat project
# Usage: bash setup.sh

set -e

# Function to check if this is a controller Pi
is_controller() {
    # Check if controller-specific packages are installed
    if dpkg -s python3-picamera2 &> /dev/null; then
        return 0  # This is a controller Pi
    else
        return 1  # This is not a controller Pi
    fi
}

# Function to configure DHCP server
configure_dhcp_server() {
    echo "=== Configuring DHCP Server ==="
    
    # Install dnsmasq
    if ! is_installed "dnsmasq"; then
        echo "[INSTALLING] dnsmasq"
        sudo apt-get install -y dnsmasq
    else
        echo "[OK] dnsmasq is already installed."
    fi
    
    # Backup original config
    if [ -f /etc/dnsmasq.conf ]; then
        sudo cp /etc/dnsmasq.conf /etc/dnsmasq.conf.backup
        echo "Backed up original dnsmasq.conf"
    fi
    
    # Create new dnsmasq configuration for local network only
    echo "Creating dnsmasq configuration for local network..."
    sudo tee /etc/dnsmasq.conf > /dev/null <<EOF
# dnsmasq configuration for Habitat local network
# This Pi acts as DHCP server for local network only
# No internet routing - devices must use wlan0 for internet

# Listen on ethernet interface only (not wlan0)
interface=eth0
bind-interfaces

# DHCP range for local network (adjust as needed)
dhcp-range=192.168.1.100,192.168.1.200,12h

# Set the Pi as the gateway for local network
dhcp-option=3,192.168.1.1

# Set DNS servers (optional - devices will use wlan0 for internet)
# dhcp-option=6,8.8.8.8,8.8.4.4

# Disable DNS server functionality (we only want DHCP)
port=0

# Log DHCP leases
dhcp-leasefile=/var/lib/misc/dnsmasq.leases

# Additional options
dhcp-authoritative
log-queries
log-dhcp
EOF

    # Create systemd service override to disable at boot by default
    sudo mkdir -p /etc/systemd/system/dnsmasq.service.d
    sudo tee /etc/systemd/system/dnsmasq.service.d/override.conf > /dev/null <<EOF
[Unit]
Description=DHCP Server for Habitat Local Network

[Service]
# Don't start automatically at boot
ExecStartPre=/bin/true

[Install]
# Don't enable at boot by default
WantedBy=multi-user.target
EOF

    # Reload systemd and disable dnsmasq at boot
    sudo systemctl daemon-reload
    sudo systemctl disable dnsmasq
    
    echo "DHCP server configured but disabled by default."
    echo "To start DHCP server: sudo systemctl start dnsmasq"
    echo "To stop DHCP server: sudo systemctl stop dnsmasq"
    echo "To enable at boot: sudo systemctl enable dnsmasq"
}

# Synchronize system time before proceeding
echo "=== Synchronizing System Time ==="
sudo timedatectl set-ntp true
sleep 5  # Wait for time sync to complete

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
    libatlas-base-dev
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
)

# Function to check if a package is installed
is_installed() {
    dpkg -s "$1" &> /dev/null
}

echo "=== Installing System Dependencies ==="
# Update package list
sudo apt-get update

echo "Installing required system packages..."
for pkg in "${SYSTEM_PACKAGES[@]}"; do
    if is_installed "$pkg"; then
        echo "[OK] $pkg is already installed."
    else
        echo "[INSTALLING] $pkg"
        sudo apt-get install -y "$pkg"
    fi
done

# Enable camera interface if not already enabled
if ! grep -q "camera_auto_detect=1" /boot/config.txt; then
    echo "Enabling camera interface..."
    echo "camera_auto_detect=1" | sudo tee -a /boot/config.txt
    echo "Camera interface enabled. A reboot may be required."
fi

# Configure DHCP server if this is a controller Pi
if is_controller; then
    echo "Controller Pi detected. Configuring DHCP server..."
    configure_dhcp_server
else
    echo "Not a controller Pi. Skipping DHCP server configuration."
fi

echo "=== Setting up Virtual Environment ==="
# Remove existing environment if it exists
if [ -d "env" ]; then
    echo "Removing existing virtual environment..."
    sudo rm -rf env
fi

# Create new virtual environment with system packages
echo "Creating new virtual environment with system packages..."
python3 -m venv env --system-site-packages

# Activate the environment
echo "Activating virtual environment..."
source env/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install the project in editable mode
echo "Installing project in editable mode..."
pip install -e .

echo "=== Setup Complete! ==="
echo "All system dependencies are installed and virtual environment is ready."
echo "To activate the environment, run: source env/bin/activate"

if is_controller; then
    echo ""
    echo "=== DHCP Server Setup ==="
    echo "DHCP server is configured but disabled by default."
    echo "Commands to manage DHCP server:"
    echo "  Start: sudo systemctl start dnsmasq"
    echo "  Stop:  sudo systemctl stop dnsmasq"
    echo "  Status: sudo systemctl status dnsmasq"
    echo "  Enable at boot: sudo systemctl enable dnsmasq"
    echo "  Disable at boot: sudo systemctl disable dnsmasq"
    echo ""
    echo "Network Configuration:"
    echo "  - DHCP range: 192.168.1.100-192.168.1.200"
    echo "  - Gateway: 192.168.1.1 (this Pi)"
    echo "  - Interface: eth0 only (no wlan0)"
    echo "  - No internet routing - devices use wlan0 for internet"
fi

echo "Note: You may need to reboot your Raspberry Pi for camera changes to take effect."

# Ask if user wants to run tests
read -p "Would you like to run the test suite now? (y/n) " -n 1 -r
echo    # Move to a new line
if [[ $REPLY =~ ^[Yy]$ ]]
then
    echo "Running test suite..."
    pytest
    echo "Tests completed!"
fi 