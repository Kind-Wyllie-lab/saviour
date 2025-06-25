#!/bin/bash
# setup.sh
# Install system dependencies and set up virtual environment for the habitat project
# Usage: bash setup.sh

set -e

# Setup logging
LOG_FILE="system_setup.log"
SUMMARY_FILE="system_setup_summary.txt"

# Function to log messages
log_message() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $1" | tee -a "$LOG_FILE"
}

# Function to log section headers
log_section() {
    log_message "=== $1 ==="
}

# Function to save summary
save_summary() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    cat > "$SUMMARY_FILE" <<EOF
Habitat Setup Summary
Generated: $timestamp
Device Role: $DEVICE_ROLE
EOF

    if [ "$DEVICE_ROLE" = "module" ]; then
        echo "Module Type: $MODULE_TYPE" >> "$SUMMARY_FILE"
    fi

    cat >> "$SUMMARY_FILE" <<EOF

$1

EOF
    log_message "Summary saved to: $SUMMARY_FILE"
}

# Function to check if this is a controller Pi
is_controller() {
    # Check if controller-specific packages are installed
    if dpkg -s python3-picamera2 &> /dev/null; then
        return 0  # This is a controller Pi
    else
        return 1  # This is not a controller Pi
    fi
}

# Function to ask user about their role
ask_user_role() {
    log_section "Device Role Configuration"
    echo "Please specify the role of this device:"
    echo "1) Controller - Master device that coordinates other modules"
    echo "2) Module - Slave device that connects to a controller"
    echo ""
    
    while true; do
        read -p "Enter your choice (1 or 2): " choice
        case $choice in
            1)
                DEVICE_ROLE="controller"
                log_message "Device configured as CONTROLLER"
                echo "Device configured as CONTROLLER"
                break
                ;;
            2)
                DEVICE_ROLE="module"
                log_message "Device configured as MODULE"
                echo "Device configured as MODULE"
                break
                ;;
            *)
                echo "Invalid choice. Please enter 1 or 2."
                ;;
        esac
    done
}

# Function to ask user about module type
ask_module_type() {
    if [ "$DEVICE_ROLE" = "module" ]; then
        log_section "Module Type Configuration"
        echo "Please specify the type of module:"
        echo "1) Camera - Video recording and streaming module"
        echo "2) Microphone - Audio recording module"
        echo "3) RFID - RFID reader module"
        echo "4) TTL - TTL signal module"
        echo "5) Generic - Generic module template"
        echo ""
        
        while true; do
            read -p "Enter your choice (1-5): " choice
            case $choice in
                1)
                    MODULE_TYPE="camera"
                    log_message "Module type configured as CAMERA"
                    echo "Module type configured as CAMERA"
                    break
                    ;;
                2)
                    MODULE_TYPE="microphone"
                    log_message "Module type configured as MICROPHONE"
                    echo "Module type configured as MICROPHONE"
                    break
                    ;;
                3)
                    MODULE_TYPE="rfid"
                    log_message "Module type configured as RFID"
                    echo "Module type configured as RFID"
                    break
                    ;;
                4)
                    MODULE_TYPE="ttl"
                    log_message "Module type configured as TTL"
                    echo "Module type configured as TTL"
                    break
                    ;;
                5)
                    MODULE_TYPE="generic"
                    log_message "Module type configured as GENERIC"
                    echo "Module type configured as GENERIC"
                    break
                    ;;
                *)
                    echo "Invalid choice. Please enter 1-5."
                    ;;
            esac
        done
    fi
}

# Function to configure PTP services
configure_ptp_services() {
    log_section "Configuring PTP Services"
    
    # Stop existing services if running
    sudo systemctl stop ptp4l 2>/dev/null || true
    sudo systemctl stop phc2sys 2>/dev/null || true
    
    # Create ptp4l service file
    log_message "Creating ptp4l systemd service..."
    sudo tee /etc/systemd/system/ptp4l.service > /dev/null <<EOF
[Unit]
Description=PTP4L (Precision Time Protocol daemon)
After=network.target
Wants=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/sbin/ptp4l -i eth0 -s -m
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    # Create phc2sys service file
    log_message "Creating phc2sys systemd service..."
    sudo tee /etc/systemd/system/phc2sys.service > /dev/null <<EOF
[Unit]
Description=PHC2SYS (PTP Hardware Clock to System Clock synchronization)
After=ptp4l.service
Wants=ptp4l.service

[Service]
Type=simple
User=root
ExecStart=/usr/sbin/phc2sys -s /dev/ptp0 -w -m
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    # Configure ptp4l based on device role
    if [ "$DEVICE_ROLE" = "controller" ]; then
        log_message "Configuring ptp4l as MASTER (controller)..."
        sudo sed -i 's|ExecStart=/usr/sbin/ptp4l -i eth0 -s -m|ExecStart=/usr/sbin/ptp4l -i eth0 -m -l 6|' /etc/systemd/system/ptp4l.service
        log_message "Configuring phc2sys for MASTER mode..."
        sudo sed -i 's|ExecStart=/usr/sbin/phc2sys -s /dev/ptp0 -w -m|ExecStart=/usr/sbin/phc2sys -a -r -r|' /etc/systemd/system/phc2sys.service
        log_message "Controller PTP configuration:"
        log_message "  - ptp4l: Master mode (-m flag, log level 6)"
        log_message "  - phc2sys: Autoconfiguration with system clock sync (-a -r -r)"
        echo "Controller PTP configuration:"
        echo "  - ptp4l: Master mode (-m flag, log level 6)"
        echo "  - phc2sys: Autoconfiguration with system clock sync (-a -r -r)"
    else
        log_message "Configuring ptp4l as SLAVE (module)..."
        sudo sed -i 's|ExecStart=/usr/sbin/ptp4l -i eth0 -s -m|ExecStart=/usr/sbin/ptp4l -i eth0 -s -m|' /etc/systemd/system/ptp4l.service
        log_message "Configuring phc2sys for SLAVE mode..."
        sudo sed -i 's|ExecStart=/usr/sbin/phc2sys -s /dev/ptp0 -w -m|ExecStart=/usr/sbin/phc2sys -s /dev/ptp0 -w -m|' /etc/systemd/system/phc2sys.service
        log_message "Module PTP configuration:"
        log_message "  - ptp4l: Slave mode (-s -m flags)"
        log_message "  - phc2sys: Manual configuration with PTP hardware clock (-s /dev/ptp0 -w -m)"
        echo "Module PTP configuration:"
        echo "  - ptp4l: Slave mode (-s -m flags)"
        echo "  - phc2sys: Manual configuration with PTP hardware clock (-s /dev/ptp0 -w -m)"
    fi

    # Reload systemd and enable services
    sudo systemctl daemon-reload
    
    # Enable services to start at boot
    sudo systemctl enable ptp4l
    sudo systemctl enable phc2sys
    
    log_message "PTP services configured and enabled at boot."
    echo "PTP services configured and enabled at boot."
    echo "Services will start automatically on next boot."
    echo ""
    echo "Manual control commands:"
    echo "  Start PTP: sudo systemctl start ptp4l && sudo systemctl start phc2sys"
    echo "  Stop PTP:  sudo systemctl stop phc2sys && sudo systemctl stop ptp4l"
    echo "  Status:    sudo systemctl status ptp4l && sudo systemctl status phc2sys"
    echo "  Logs:      sudo journalctl -u ptp4l -f && sudo journalctl -u phc2sys -f"
}

# Function to configure Samba share for controller
configure_samba_share() {
    log_section "Configuring Samba Share"
    
    # Create the share directory
    sudo mkdir -p /home/pi/controller_share
    sudo chown pi:pi /home/pi/controller_share
    sudo chmod 755 /home/pi/controller_share
    log_message "Created controller_share directory: /home/pi/controller_share"
    
    # Backup original samba config
    if [ -f /etc/samba/smb.conf ]; then
        sudo cp /etc/samba/smb.conf /etc/samba/smb.conf.backup
        log_message "Backed up original smb.conf"
    fi
    
    # Create new samba configuration
    log_message "Creating Samba configuration..."
    sudo tee /etc/samba/smb.conf > /dev/null <<EOF
[global]
   workgroup = WORKGROUP
   server string = Habitat Controller
   server role = standalone server
   map to guest = bad user
   dns proxy = no
   log level = 1
   log file = /var/log/samba/%m.log
   max log size = 50

[controller_share]
   comment = Habitat Controller Share
   path = /home/pi/controller_share
   browseable = yes
   writable = yes
   guest ok = yes
   create mask = 0644
   directory mask = 0755
   force user = pi
   force group = pi
EOF

    # Set Samba password for pi user (default password: habitat)
    log_message "Setting Samba password for pi user..."
    echo -e "habitat\nhabitat" | sudo smbpasswd -s -a pi
    
    # Restart Samba services
    sudo systemctl restart smbd
    sudo systemctl restart nmbd
    
    # Enable Samba services at boot
    sudo systemctl enable smbd
    sudo systemctl enable nmbd
    
    log_message "Samba share configured successfully!"
    echo "Samba share configured successfully!"
    echo "Share name: controller_share"
    echo "Path: /home/pi/controller_share"
    echo "Username: pi"
    echo "Password: habitat"
    echo ""
    echo "Access from other devices:"
    echo "  Windows: \\\\$(hostname -I | awk '{print $1}')\\controller_share"
    echo "  Linux/Mac: smb://$(hostname -I | awk '{print $1}')/controller_share"
    echo ""
    echo "Samba control commands:"
    echo "  Start: sudo systemctl start smbd nmbd"
    echo "  Stop:  sudo systemctl stop smbd nmbd"
    echo "  Status: sudo systemctl status smbd nmbd"
    echo "  Restart: sudo systemctl restart smbd nmbd"
}

# Function to configure DHCP server
configure_dhcp_server() {
    log_section "Configuring DHCP Server"
    
    # Install dnsmasq
    if ! is_installed "dnsmasq"; then
        log_message "[INSTALLING] dnsmasq"
        sudo apt-get install -y dnsmasq
    else
        log_message "[OK] dnsmasq is already installed."
    fi
    
    # Backup original config
    if [ -f /etc/dnsmasq.conf ]; then
        sudo cp /etc/dnsmasq.conf /etc/dnsmasq.conf.backup
        log_message "Backed up original dnsmasq.conf"
    fi
    
    # Create new dnsmasq configuration for local network only
    log_message "Creating dnsmasq configuration for local network..."
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
    
    log_message "DHCP server configured but disabled by default."
    echo "DHCP server configured but disabled by default."
    echo "To start DHCP server: sudo systemctl start dnsmasq"
    echo "To stop DHCP server: sudo systemctl stop dnsmasq"
    echo "To enable at boot: sudo systemctl enable dnsmasq"
}

# Function to configure module systemd service
configure_module_service() {
    if [ "$DEVICE_ROLE" = "module" ]; then
        log_section "Configuring Module Systemd Service"
        
        # Stop existing service if running
        sudo systemctl stop habitat-${MODULE_TYPE}-module 2>/dev/null || true
        
        # Create service file based on module type
        log_message "Creating habitat-${MODULE_TYPE}-module systemd service..."
        sudo tee /etc/systemd/system/habitat-${MODULE_TYPE}-module.service > /dev/null <<EOF
[Unit]
Description=Habitat ${MODULE_TYPE^} Module
After=network.target ptp4l.service phc2sys.service
Wants=network.target ptp4l.service phc2sys.service

[Service]
Type=simple
User=root
WorkingDirectory=/home/pi/Desktop/habitat/src/modules/examples
ExecStart=/home/pi/Desktop/habitat/env/bin/python ${MODULE_TYPE}_example.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Environment variables
Environment=PYTHONPATH=/home/pi/Desktop/habitat/src

[Install]
WantedBy=multi-user.target
EOF

        # Reload systemd and enable service
        sudo systemctl daemon-reload
        sudo systemctl enable habitat-${MODULE_TYPE}-module
        
        log_message "Habitat ${MODULE_TYPE} module service configured and enabled at boot."
        echo "Habitat ${MODULE_TYPE} module service configured and enabled at boot."
        echo ""
        echo "Module service control commands:"
        echo "  Start: sudo systemctl start habitat-${MODULE_TYPE}-module"
        echo "  Stop:  sudo systemctl stop habitat-${MODULE_TYPE}-module"
        echo "  Status: sudo systemctl status habitat-${MODULE_TYPE}-module"
        echo "  Logs: sudo journalctl -u habitat-${MODULE_TYPE}-module -f"
        echo "  Restart: sudo systemctl restart habitat-${MODULE_TYPE}-module"
    fi
}

# Function to configure controller systemd service
configure_controller_service() {
    if [ "$DEVICE_ROLE" = "controller" ]; then
        log_section "Configuring Controller Systemd Service"
        
        # Stop existing service if running
        sudo systemctl stop habitat-controller-service 2>/dev/null || true
        sudo systemctl stop habitat-controller 2>/dev/null || true
        
        # Create service file for controller (match module service style)
        log_message "Creating habitat-controller-service systemd service..."
        sudo tee /etc/systemd/system/habitat-controller-service.service > /dev/null <<EOF
[Unit]
Description=Habitat Controller Service
After=network.target ptp4l.service phc2sys.service
Wants=network.target ptp4l.service phc2sys.service

[Service]
Type=simple
User=root
WorkingDirectory=/home/pi/Desktop/habitat/src/controller/examples
ExecStart=/home/pi/Desktop/habitat/env/bin/python controller_example.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Environment variables
Environment=PYTHONPATH=/home/pi/Desktop/habitat/src

[Install]
WantedBy=multi-user.target
EOF

        # Reload systemd and enable service
        sudo systemctl daemon-reload
        sudo systemctl enable habitat-controller-service
        
        log_message "Habitat controller service configured and enabled at boot. (module style)"
        echo "Habitat controller service configured and enabled at boot. (module style)"
        echo ""
        echo "Controller service control commands:"
        echo "  Start: sudo systemctl start habitat-controller-service"
        echo "  Stop:  sudo systemctl stop habitat-controller-service"
        echo "  Status: sudo systemctl status habitat-controller-service"
        echo "  Logs: sudo journalctl -u habitat-controller-service -f"
        echo "  Restart: sudo systemctl restart habitat-controller-service"
    fi
}

# Initialize logging
log_section "Habitat Setup Started"
log_message "Setup script version: $(date '+%Y-%m-%d %H:%M:%S')"
log_message "System: $(uname -a)"
log_message "User: $(whoami)"

# Synchronize system time before proceeding
log_section "Synchronizing System Time"
sudo timedatectl set-ntp true
sleep 5  # Wait for time sync to complete
log_message "System time synchronized"

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

log_section "Installing System Dependencies"
# Update package list
sudo apt-get update
log_message "Package list updated"

echo "Installing required system packages..."
for pkg in "${SYSTEM_PACKAGES[@]}"; do
    if is_installed "$pkg"; then
        log_message "[OK] $pkg is already installed."
        echo "[OK] $pkg is already installed."
    else
        log_message "[INSTALLING] $pkg"
        echo "[INSTALLING] $pkg"
        sudo apt-get install -y "$pkg"
    fi
done

# Enable camera interface if not already enabled
if ! grep -q "camera_auto_detect=1" /boot/config.txt; then
    log_message "Enabling camera interface..."
    echo "camera_auto_detect=1" | sudo tee -a /boot/config.txt
    log_message "Camera interface enabled. A reboot may be required."
    echo "Camera interface enabled. A reboot may be required."
fi

# Ask user about their device role
ask_user_role

# Ask user about module type
ask_module_type

# Configure PTP services based on device role
configure_ptp_services

# Configure Samba share for controller
if [ "$DEVICE_ROLE" = "controller" ]; then
    log_message "Controller Pi detected. Configuring Samba share..."
    configure_samba_share
else
    log_message "Module Pi detected. Skipping Samba share configuration."
fi

# Configure DHCP server if this is a controller Pi
if [ "$DEVICE_ROLE" = "controller" ]; then
    log_message "Controller Pi detected. Configuring DHCP server..."
    configure_dhcp_server
else
    log_message "Module Pi detected. Skipping DHCP server configuration."
fi

# Configure module systemd service
configure_module_service

# Configure controller systemd service
configure_controller_service

log_section "Setting up Virtual Environment"
# Remove existing environment if it exists
if [ -d "env" ]; then
    log_message "Removing existing virtual environment..."
    sudo rm -rf env
fi

# Create new virtual environment with system packages
log_message "Creating new virtual environment with system packages..."
python3 -m venv env --system-site-packages

# Activate the environment
log_message "Activating virtual environment..."
source env/bin/activate

# Upgrade pip
log_message "Upgrading pip..."
pip install --upgrade pip

# Install the project in editable mode
log_message "Installing project in editable mode..."
pip install -e .

log_section "Setup Complete"
log_message "All system dependencies are installed and virtual environment is ready."

# Generate summary content
SUMMARY_CONTENT=""

if [ "$DEVICE_ROLE" = "controller" ]; then
    SUMMARY_CONTENT="Device Role: CONTROLLER
PTP Configuration: MASTER mode

=== Samba Share Setup ===
Samba share configured successfully!
Share name: controller_share
Path: /home/pi/controller_share
Username: pi
Password: habitat

Access from other devices:
  Windows: \\\\$(hostname -I | awk '{print $1}')\\controller_share
  Linux/Mac: smb://$(hostname -I | awk '{print $1}')/controller_share

Samba control commands:
  Start: sudo systemctl start smbd nmbd
  Stop:  sudo systemctl stop smbd nmbd
  Status: sudo systemctl status smbd nmbd
  Restart: sudo systemctl restart smbd nmbd

=== DHCP Server Setup ===
DHCP server is configured but disabled by default.
Commands to manage DHCP server:
  Start: sudo systemctl start dnsmasq
  Stop:  sudo systemctl stop dnsmasq
  Status: sudo systemctl status dnsmasq
  Enable at boot: sudo systemctl enable dnsmasq
  Disable at boot: sudo systemctl disable dnsmasq

Network Configuration:
  - DHCP range: 192.168.1.100-192.168.1.200
  - Gateway: 192.168.1.1 (this Pi)
  - Interface: eth0 only (no wlan0)
  - No internet routing - devices use wlan0 for internet

=== Controller Service Setup ===
Habitat controller service configured and enabled at boot. (module style)
Service: habitat-controller-service

Controller service control commands:
  Start: sudo systemctl start habitat-controller-service
  Stop:  sudo systemctl stop habitat-controller-service
  Status: sudo systemctl status habitat-controller-service
  Logs: sudo journalctl -u habitat-controller-service -f
  Restart: sudo systemctl restart habitat-controller-service"
else
    SUMMARY_CONTENT="Device Role: MODULE
Module Type: ${MODULE_TYPE^^}
PTP Configuration: SLAVE mode
This module will synchronize to the controller's PTP master.

=== Module Service Setup ===
Habitat ${MODULE_TYPE} module service configured and enabled at boot.
Service: habitat-${MODULE_TYPE}-module

Module service control commands:
  Start: sudo systemctl start habitat-${MODULE_TYPE}-module
  Stop:  sudo systemctl stop habitat-${MODULE_TYPE}-module
  Status: sudo systemctl status habitat-${MODULE_TYPE}-module
  Logs: sudo journalctl -u habitat-${MODULE_TYPE}-module -f
  Restart: sudo systemctl restart habitat-${MODULE_TYPE}-module"
fi

SUMMARY_CONTENT="$SUMMARY_CONTENT

=== PTP Services Status ===
PTP services are configured and enabled at boot:
  - ptp4l: PTP daemon (${DEVICE_ROLE} mode)
  - phc2sys: Hardware clock synchronization

PTP Control Commands:
  Start: sudo systemctl start ptp4l && sudo systemctl start phc2sys
  Stop:  sudo systemctl stop phc2sys && sudo systemctl stop ptp4l
  Status: sudo systemctl status ptp4l && sudo systemctl status phc2sys
  Logs: sudo journalctl -u ptp4l -f && sudo journalctl -u phc2sys -f

=== General Information ===
Virtual Environment: source env/bin/activate
Setup Log: $LOG_FILE
Summary File: $SUMMARY_FILE

Note: You may need to reboot your Raspberry Pi for camera changes to take effect."

# Save summary to file
save_summary "$SUMMARY_CONTENT"

# Display summary
echo "=== Setup Complete! ==="
echo "All system dependencies are installed and virtual environment is ready."
echo "To activate the environment, run: source env/bin/activate"
echo ""
echo "Setup log saved to: $LOG_FILE"
echo "Summary saved to: $SUMMARY_FILE"
echo ""

if [ "$DEVICE_ROLE" = "controller" ]; then
    echo "=== Controller Configuration Summary ==="
    echo "Device Role: CONTROLLER"
    echo "PTP Configuration: MASTER mode"
    echo ""
    echo "=== Samba Share Setup ==="
    echo "Samba share configured successfully!"
    echo "Share name: controller_share"
    echo "Path: /home/pi/controller_share"
    echo "Username: pi"
    echo "Password: habitat"
    echo ""
    echo "Access from other devices:"
    echo "  Windows: \\\\$(hostname -I | awk '{print $1}')\\controller_share"
    echo "  Linux/Mac: smb://$(hostname -I | awk '{print $1}')/controller_share"
    echo ""
    echo "Samba control commands:"
    echo "  Start: sudo systemctl start smbd nmbd"
    echo "  Stop:  sudo systemctl stop smbd nmbd"
    echo "  Status: sudo systemctl status smbd nmbd"
    echo "  Restart: sudo systemctl restart smbd nmbd"
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
    echo ""
    echo "=== Controller Service Setup ==="
    echo "Habitat controller service configured and enabled at boot. (module style)"
    echo "Service: habitat-controller-service"
    echo ""
    echo "Controller service control commands:"
    echo "  Start: sudo systemctl start habitat-controller-service"
    echo "  Stop:  sudo systemctl stop habitat-controller-service"
    echo "  Status: sudo systemctl status habitat-controller-service"
    echo "  Logs: sudo journalctl -u habitat-controller-service -f"
    echo "  Restart: sudo systemctl restart habitat-controller-service"
else
    echo "=== Module Configuration Summary ==="
    echo "Device Role: MODULE"
    echo "Module Type: ${MODULE_TYPE^^}"
    echo "PTP Configuration: SLAVE mode"
    echo "This module will synchronize to the controller's PTP master."
    echo ""
    echo "=== Module Service Setup ==="
    echo "Habitat ${MODULE_TYPE} module service configured and enabled at boot."
    echo "Service: habitat-${MODULE_TYPE}-module"
    echo ""
    echo "Module service control commands:"
    echo "  Start: sudo systemctl start habitat-${MODULE_TYPE}-module"
    echo "  Stop:  sudo systemctl stop habitat-${MODULE_TYPE}-module"
    echo "  Status: sudo systemctl status habitat-${MODULE_TYPE}-module"
    echo "  Logs: sudo journalctl -u habitat-${MODULE_TYPE}-module -f"
    echo "  Restart: sudo systemctl restart habitat-${MODULE_TYPE}-module"
fi

echo ""
echo "=== PTP Services Status ==="
echo "PTP services are configured and enabled at boot:"
echo "  - ptp4l: PTP daemon (${DEVICE_ROLE} mode)"
echo "  - phc2sys: Hardware clock synchronization"
echo ""
echo "PTP Control Commands:"
echo "  Start: sudo systemctl start ptp4l && sudo systemctl start phc2sys"
echo "  Stop:  sudo systemctl stop phc2sys && sudo systemctl stop ptp4l"
echo "  Status: sudo systemctl status ptp4l && sudo systemctl status phc2sys"
echo "  Logs: sudo journalctl -u ptp4l -f && sudo journalctl -u phc2sys -f"

echo "Note: You may need to reboot your Raspberry Pi for camera changes to take effect."

# Ask if user wants to run tests
read -p "Would you like to run the test suite now? (y/n) " -n 1 -r
echo    # Move to a new line
if [[ $REPLY =~ ^[Yy]$ ]]
then
    log_section "Running Test Suite"
    log_message "User chose to run test suite"
    echo "Running test suite..."
    pytest
    log_message "Test suite completed"
    echo "Tests completed!"
fi 

log_section "Setup Script Completed"
log_message "Setup script finished successfully" 