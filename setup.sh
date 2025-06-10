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

echo "=== Setting up Virtual Environment ==="
# Remove existing environment if it exists
if [ -d "env" ]; then
    echo "Removing existing virtual environment..."
    rm -rf env
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