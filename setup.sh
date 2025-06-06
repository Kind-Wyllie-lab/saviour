#!/bin/bash
# setup_system_deps.sh
# Install system-level dependencies for the habitat project
# Usage: bash setup_system_deps.sh

set -e

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
)

# Function to check if a package is installed
is_installed() {
    dpkg -s "$1" &> /dev/null
}

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

echo "All required system dependencies are installed."
echo "Note: You may need to reboot your Raspberry Pi for all changes to take effect." 