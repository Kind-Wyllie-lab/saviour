#!/usr/env/bin bash
# setup.sh
# Install system dependencies and set up virtual environment for the saviour system
# Usage: bash setup.sh
#working

set -Eeuo pipefail # If any function throws an error (doesn't return 0), exit immediately.
trap 'rc=$?; echo "setup.sh failed with exit code $rc at line $LINENO"' ERR

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
Saviour Setup Summary
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

# Function to configure NTP for PTP coexistence
configure_ntp_for_ptp() {
    log_section "Configuring NTP for PTP Coexistence"
    
    # Backup original timesyncd config
    if [ -f /etc/systemd/timesyncd.conf ]; then
        sudo cp /etc/systemd/timesyncd.conf /etc/systemd/timesyncd.conf.backup
        log_message "Backed up original timesyncd.conf"
    fi
    
    # Create optimized timesyncd configuration for PTP coexistence
    log_message "Creating timesyncd configuration for PTP coexistence..."
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
    log_message "Enabling NTP with reduced frequency..."
    sudo timedatectl set-ntp true
    
    # Restart timesyncd to apply new configuration
    sudo systemctl restart systemd-timesyncd
    
    log_message "NTP configured for PTP coexistence"
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
   server string = Saviour Controller
   server role = standalone server
   map to guest = bad user
   dns proxy = no
   log level = 1
   log file = /var/log/samba/%m.log
   max log size = 50

[controller_share]
   comment = Saviour Controller Share
   path = /home/pi/controller_share
   browseable = yes
   writable = yes
   guest ok = yes
   create mask = 0644
   directory mask = 0755
   force user = pi
   force group = pi
EOF

    # Set Samba password for pi user (default password: saviour)
    log_message "Setting Samba password for pi user..."
    echo -e "saviour\nsaviour" | sudo smbpasswd -s -a pi
    
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
    echo "Password: saviour"
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
    
    # Setup static ip
    log_message "Setting static IP to 192.168.1.1 with nmcli"
    sudo nmcli connection modify "Wired connection 1" ipv4.method manual
    sudo nmcli connection modify "Wired connection 1" ipv4.addresses 192.168.1.1/24

    # Install dnsmasq
    if ! is_installed "dnsmasq"; then
        log_message "[INSTALLING] dnsmasq"
        sudo apt-get install -y dnsmasq
    else
        log_message "[OK] dnsmasq is already installed."
    fi

    # Modify DNSMasq service description to start after interfaces are up
    log_message "Modifying DNSMasq service description..."
    if [ -f /lib/systemd/system/dnsmasq.service ]; then
        # Backup original service file
        sudo cp /lib/systemd/system/dnsmasq.service /lib/systemd/system/dnsmasq.service.backup
        log_message "Backed up original dnsmasq.service"
        
        # Modify the service file to start after network interfaces are up
        sudo sed -i 's/Requires=network.target/Requires=network-online.target/g' /lib/systemd/system/dnsmasq.service
        sudo sed -i 's/After=network.target/After=network-online.target/g' /lib/systemd/system/dnsmasq.service
        log_message "Modified DNSMasq service to start after network interfaces are up"
    else
        log_message "Warning: dnsmasq.service not found at /lib/systemd/system/dnsmasq.service"
    fi
    
    # Backup original config
    if [ -f /etc/dnsmasq.conf ]; then
        sudo cp /etc/dnsmasq.conf /etc/dnsmasq.conf.backup
        log_message "Backed up original dnsmasq.conf"
    fi
    
    # Create new dnsmasq configuration for local network only
    log_message "Creating dnsmasq configuration for local network..."
    sudo tee /etc/dnsmasq.conf > /dev/null <<EOF
# dnsmasq configuration for Saviour local network
# This Pi acts as DHCP server for local network only
# No internet routing - devices must use wlan0 for internet

# Listen on ethernet interface only (not wlan0)
interface=eth0
bind-interfaces

# DHCP range for local network (adjust as needed)
dhcp-range=192.168.1.100,192.168.1.200,12h

# Don't use controller as default gateway. Allows clients to still access internet on their other network interfaces.
dhcp-option=3

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
Description=DHCP Server for Saviour Local Network

[Service]
# Don't start automatically at boot
ExecStartPre=/bin/true

[Install]
# Don't enable at boot by default
WantedBy=multi-user.target
EOF

    # Reload systemd and disable dnsmasq at boot
    sudo systemctl daemon-reload
    sudo systemctl enable dnsmasq
    sudo systemctl restart dnsmasq.service
    
    log_message "DHCP server configured and enabled."
    echo "DHCP server configured and enabled."
    echo "To start DHCP server: sudo systemctl start dnsmasq"
    echo "To stop DHCP server: sudo systemctl stop dnsmasq"
}

configure_mdns() {
    log_section "Configuring controller mDNS via avahi daemon"
    if ! is_installed "avahi-daemon"; then
        log_message "Installing avahi-daemon";
        sudo apt install avahi-daemon -y
    else
        log_message "avahi-daemon is already installed"
    fi

    # Configure avahi
    sudo tee /etc/avahi/avahi-daemon.conf > /dev/null <<EOF
# avahi daemon configuration for SAVIOUR local network
[server]
host-name=saviour
use-ipv4=yes
use-ipv6=yes
allow-interfaces=eth0
deny-interfaces=wlan0
ratelimit-interval-usec=1000000
ratelimit-burst=1000

[wide-area]
enable-wide-area=yes

[publish]
publish-hinfo=no
publish-workstation=yes
EOF
    # Reload systemd and enable
    sudo systemctl daemon-reload
    sudo systemctl enable --now avahi-daemon
    sudo systemctl restart avahi-daemon.service

    log_message "mDNS configured and enabled - controller will appear on network as saviour.local"
    echo "mDNS server configured and enabled."
    echo "Controller will appear on network as saviour.local"

    log_message "Configuring iptables to forward port 80 traffic to port 5000"
    sudo apt-get install iptables-persistent -y
    sudo iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000
    sudo netfilter-persistent save
}

configure_frontend() {
    log_section "Configuring Node.js and frontend"
    echo "Installing nvm, Node.js, vite, and building frontend"
    log_message "Installing nvm"
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash # Install nvm
    \. "$HOME/.nvm/nvm.sh" # Instead of restarting the shell - from Node.js
    log_message "Installing Node.js v22"
    nvm install 22 # Install Node.js - make sure to keep version up to date
    node -v # Should print v22. something
    npm -v # Should print 10.9.3 or something

    log_message "Installing vite and building frontend"
    cd src/controller/frontend/
    npm install
    npm run build
    echo "Frontend built"
    log_message "nvm, Node.js, vite installed and frontend built"
    cd ../../../
}

# Function to configure module systemd service
configure_module_service() {
    if [ "$DEVICE_ROLE" = "module" ]; then
        log_section "Configuring Module Systemd Service"
        
        # Stop existing service if running
        sudo systemctl stop saviour-${MODULE_TYPE}-module 2>/dev/null || true
        
        # Create service file based on module type
        log_message "Creating saviour-${MODULE_TYPE}-module systemd service..."
        sudo tee /etc/systemd/system/saviour-${MODULE_TYPE}-module.service > /dev/null <<EOF
[Unit]
Description=Saviour ${MODULE_TYPE^} Module
After=network.target ptp4l.service phc2sys.service
Wants=network.target ptp4l.service phc2sys.service

[Service]
Type=simple
User=root
WorkingDirectory=/usr/local/src/saviour/src/modules/examples/${MODULE_TYPE}/
ExecStart=/usr/local/src/saviour/env/bin/python ${MODULE_TYPE}_module.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Environment variables
Environment=PYTHONPATH=/usr/local/src/saviour/src
Environment="XDG_RUNTIME_DIR=/run/user/1000"
Environment="PULSE_RUNTIME_PATH=/run/user/1000/pulse/"

[Install]
WantedBy=multi-user.target
EOF

        # Reload systemd and enable service
        sudo systemctl daemon-reload
        sudo systemctl enable saviour-${MODULE_TYPE}-module
        
        log_message "Saviour ${MODULE_TYPE} module service configured and enabled at boot."
        echo "Saviour ${MODULE_TYPE} module service configured and enabled at boot."
        echo ""
        echo "Module service control commands:"
        echo "  Start: sudo systemctl start saviour-${MODULE_TYPE}-module"
        echo "  Stop:  sudo systemctl stop saviour-${MODULE_TYPE}-module"
        echo "  Status: sudo systemctl status saviour-${MODULE_TYPE}-module"
        echo "  Logs: sudo journalctl -u saviour-${MODULE_TYPE}-module -f"
        echo "  Restart: sudo systemctl restart saviour-${MODULE_TYPE}-module"
    fi
}

# Function to configure controller systemd service
configure_controller_service() {
    if [ "$DEVICE_ROLE" = "controller" ]; then
        log_section "Configuring Controller Systemd Service"
        
        # Stop existing service if running
        sudo systemctl stop saviour-controller-service 2>/dev/null || true
        sudo systemctl stop saviour-controller 2>/dev/null || true
        
        # Create service file for controller (match module service style)
        log_message "Creating saviour-controller-service systemd service..."
        sudo tee /etc/systemd/system/saviour-controller-service.service > /dev/null <<EOF
[Unit]
Description=Saviour Controller Service
After=network.target ptp4l.service phc2sys.service
Wants=network.target ptp4l.service phc2sys.service

[Service]
Type=simple
User=root
WorkingDirectory=/usr/local/src/saviour/src/controller/
ExecStart=/usr/local/src/saviour/env/bin/python controller.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Environment variables
Environment=PYTHONPATH=/usr/local/src/saviour/src

[Install]
WantedBy=multi-user.target
EOF

        # Reload systemd and enable service
        sudo systemctl daemon-reload
        sudo systemctl enable saviour-controller-service
        
        log_message "Saviour controller service configured and enabled at boot. (module style)"
        echo "Saviour controller service configured and enabled at boot. (module style)"
        echo ""
        echo "Controller service control commands:"
        echo "  Start: sudo systemctl start saviour-controller-service"
        echo "  Stop:  sudo systemctl stop saviour-controller-service"
        echo "  Status: sudo systemctl status saviour-controller-service"
        echo "  Logs: sudo journalctl -u saviour-controller-service -f"
        echo "  Restart: sudo systemctl restart saviour-controller-service"
    fi
}

# Initialize logging
log_section "Saviour Setup Started"
log_message "Setup script version: $(date '+%Y-%m-%d %H:%M:%S')"
log_message "System: $(uname -a)"
log_message "User: $(whoami)"

# Ask user about their device role first
ask_user_role

# Ask user about module type if this is a module
ask_module_type

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

# Configure Pipewire sampling rate for Audiomoth
if [ "$DEVICE_ROLE" = "module" ]; then
    if [ "$MODULE_TYPE" = "microphone" ]; then
        sudo install -d /etc/pipewire/pipewire.conf.d
        sudo tee /etc/pipewire/pipewire.conf.d/99-sample-rates.conf >/dev/null <<'EOF'
        context.properties = {
            default.clock.rate = 192000
            default.clock.allowed-rates = [ 96000 192000 384000]
        }
EOF
        systemctl --user restart pipewire pipewire-pulse wireplumber
    fi
fi
# Enable camera interface if not already enabled
if ! grep -q "camera_auto_detect=1" /boot/config.txt; then
    log_message "Enabling camera interface..."
    echo "camera_auto_detect=1" | sudo tee -a /boot/config.txt
    log_message "Camera interface enabled. A reboot may be required."
    echo "Camera interface enabled. A reboot may be required."
fi

# Configure NTP for PTP coexistence (controllers only)
if [ "$DEVICE_ROLE" = "controller" ]; then
    log_message "Controller Pi detected. Configuring NTP for PTP coexistence..."
    configure_ntp_for_ptp
else
    log_message "Module Pi detected. Skipping NTP configuration (modules should not use NTP)."
fi

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

if [ "$DEVICE_ROLE" = "controller" ]; then
    log_message "Controller Pi detected. Configuring mDNS server..."
    configure_mdns
else
    log_message "Module Pi detected. Skipping mDNS server."
fi

if [ "$DEVICE_ROLE" = "controller" ]; then
    log_message "Controller Pi detected. Configuring and building frontend..."
    configure_frontend
else
    log_message "Module Pi detected. Skipping frontend build."
fi

# Configure module systemd service
if [ "$DEVICE_ROLE" = "module" ]; then
    configure_module_service
else
    log_message "Controller Pi detected. Skipping module service configuration."
fi

# Configure controller systemd service
if [ "$DEVICE_ROLE" = "controller" ]; then
    configure_controller_service
else
    log_message "Module Pi detected. Skipping controller service configuration."
fi

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

=== NTP Configuration for PTP Coexistence ===
NTP configured for PTP coexistence with reduced frequency:
  - Poll interval: 5 minutes to 1 hour (reduced frequency)
  - Max adjustment: 100ms per sync (reduced from 500ms)
  - Multiple time servers for redundancy
  - Optimized to minimize interference with PTP

NTP control commands:
  Status: timedatectl status
  Logs: sudo journalctl -u systemd-timesyncd -f
  Restart: sudo systemctl restart systemd-timesyncd
  Disable: sudo timedatectl set-ntp false
  Enable: sudo timedatectl set-ntp true

=== Samba Share Setup ===
Samba share configured successfully!
Share name: controller_share
Path: /home/pi/controller_share
Username: pi
Password: saviour

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
Saviour controller service configured and enabled at boot. (module style)
Service: saviour-controller-service

Controller service control commands:
  Start: sudo systemctl start saviour-controller-service
  Stop:  sudo systemctl stop saviour-controller-service
  Status: sudo systemctl status saviour-controller-service
  Logs: sudo journalctl -u saviour-controller-service -f
  Restart: sudo systemctl restart saviour-controller-service"
else
    SUMMARY_CONTENT="Device Role: MODULE
Module Type: ${MODULE_TYPE^^}
PTP Configuration: SLAVE mode
This module will synchronize to the controller's PTP master.

=== Module Service Setup ===
Saviour ${MODULE_TYPE} module service configured and enabled at boot.
Service: saviour-${MODULE_TYPE}-module

Module service control commands:
  Start: sudo systemctl start saviour-${MODULE_TYPE}-module
  Stop:  sudo systemctl stop saviour-${MODULE_TYPE}-module
  Status: sudo systemctl status saviour-${MODULE_TYPE}-module
  Logs: sudo journalctl -u saviour-${MODULE_TYPE}-module -f
  Restart: sudo systemctl restart saviour-${MODULE_TYPE}-module"
fi

SUMMARY_CONTENT="$SUMMARY_CONTENT

=== PTP Services Status ===
PTP services are configured and enabled at boot:
  - ptp4l: PTP daemon (${DEVICE_ROLE} mode)
  - phc2sys: Hardware clock synchronization"

if [ "$DEVICE_ROLE" = "controller" ]; then
    SUMMARY_CONTENT="$SUMMARY_CONTENT
  - NTP: Configured for coexistence with PTP (reduced frequency)"
fi

SUMMARY_CONTENT="$SUMMARY_CONTENT

PTP Control Commands:
  Start: sudo systemctl start ptp4l && sudo systemctl start phc2sys
  Stop:  sudo systemctl stop phc2sys && sudo systemctl stop ptp4l
  Status: sudo systemctl status ptp4l && sudo systemctl status phc2sys
  Logs: sudo journalctl -u ptp4l -f && sudo journalctl -u phc2sys -f"

SUMMARY_CONTENT="$SUMMARY_CONTENT

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
    echo "=== NTP Configuration for PTP Coexistence ==="
    echo "NTP configured for PTP coexistence with reduced frequency:"
    echo "  - Poll interval: 5 minutes to 1 hour (reduced frequency)"
    echo "  - Max adjustment: 100ms per sync (reduced from 500ms)"
    echo "  - Multiple time servers for redundancy"
    echo "  - Optimized to minimize interference with PTP"
    echo ""
    echo "NTP control commands:"
    echo "  Status: timedatectl status"
    echo "  Logs: sudo journalctl -u systemd-timesyncd -f"
    echo "  Restart: sudo systemctl restart systemd-timesyncd"
    echo "  Disable: sudo timedatectl set-ntp false"
    echo "  Enable: sudo timedatectl set-ntp true"
    echo ""
    echo "=== Samba Share Setup ==="
    echo "Samba share configured successfully!"
    echo "Share name: controller_share"
    echo "Path: /home/pi/controller_share"
    echo "Username: pi"
    echo "Password: saviour"
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
    echo "Saviour controller service configured and enabled at boot. (module style)"
    echo "Service: saviour-controller-service"
    echo ""
    echo "Controller service control commands:"
    echo "  Start: sudo systemctl start saviour-controller-service"
    echo "  Stop:  sudo systemctl stop saviour-controller-service"
    echo "  Status: sudo systemctl status saviour-controller-service"
    echo "  Logs: sudo journalctl -u saviour-controller-service -f"
    echo "  Restart: sudo systemctl restart saviour-controller-service"
else
    echo "=== Module Configuration Summary ==="
    echo "Device Role: MODULE"
    echo "Module Type: ${MODULE_TYPE^^}"
    echo "PTP Configuration: SLAVE mode"
    echo "This module will synchronize to the controller's PTP master."
    echo ""
    echo "=== Module Service Setup ==="
    echo "Saviour ${MODULE_TYPE} module service configured and enabled at boot."
    echo "Service: saviour-${MODULE_TYPE}-module"
    echo ""
    echo "Module service control commands:"
    echo "  Start: sudo systemctl start saviour-${MODULE_TYPE}-module"
    echo "  Stop:  sudo systemctl stop saviour-${MODULE_TYPE}-module"
    echo "  Status: sudo systemctl status saviour-${MODULE_TYPE}-module"
    echo "  Logs: sudo journalctl -u saviour-${MODULE_TYPE}-module -f"
    echo "  Restart: sudo systemctl restart saviour-${MODULE_TYPE}-module"
fi

echo ""
echo "=== PTP Services Status ==="
echo "PTP services are configured and enabled at boot:"
echo "  - ptp4l: PTP daemon (${DEVICE_ROLE} mode)"
echo "  - phc2sys: Hardware clock synchronization"

if [ "$DEVICE_ROLE" = "controller" ]; then
    echo "  - NTP: Configured for coexistence with PTP (reduced frequency)"
fi

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
