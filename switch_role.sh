#!/usr/bin/env bash
# Switch Role
# Script used to switch which role the SAVIOUR device is configured for - controller or module and variant.

set -Eeuo pipefail # If any function throws an error (doesn't return 0), exit immediately.
trap 'rc=$?; echo "switch_role.sh failed with exit code $rc at line $LINENO"' ERR

USER=`whoami`
SCRIPT_PATH="$(readlink -f "$0")"
DIR="$(dirname "$SCRIPT_PATH")"
SHARENAME="controller_share"


# Function to check current role
get_current_role() {
    if [ -f /etc/saviour/config ]; then
        source /etc/saviour/config
        CURRENT_ROLE=$ROLE
        CURRENT_TYPE=$TYPE
    else
        CURRENT_ROLE=""
        CURRENT_TYPE=""
    fi
}

write_new_role_to_file() {
    sudo tee /etc/saviour/config >/dev/null <<EOF
ROLE=${DEVICE_ROLE}
TYPE=${DEVICE_TYPE}
EOF
}

# Function to ask user about their role
ask_user_role() {
    echo "Please specify the new role of this device:"
    echo "1) Controller - Central device that coordinates other modules and presents a GUI"
    echo "2) Module - Peripheral device that connects to a controller and executes received commands"
    echo ""
    
    while true; do
        read -p "Enter your choice (1 or 2): " choice
        case $choice in
            1)
                DEVICE_ROLE="controller"
                echo "Device configured as CONTROLLER"
                break
                ;;
            2)
                DEVICE_ROLE="module"
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
        echo "Please specify the type of module:"
        echo "1) CAMERA - Basic camera module"
        echo "2) MICROPHONE - Audiomoth module that can support up to 4 connected microphones"
        echo "3) TTL - TTL pulse generating and capturing module (e.g. for syncing with Open Ephys)"
        echo "4) RFID - For gathering data from RFID transponders (NOT YET IMPLEMENTED)"
        echo "5) APA CAMERA - Top mounted camera module that tracks rat location"
        echo "6) APA RIG - Arduino module that drives rig motor and shock generator"
        echo "7) SOUND - HifiBerry sound producing module that can drive speakers"
        echo ""
        
        while true; do
            read -p "Enter your choice: " choice
            case $choice in
                1) 
                    DEVICE_TYPE="camera"
                    break
                    ;;
                2)
                    DEVICE_TYPE="microphone"
                    break
                    ;;
                3)
                    DEVICE_TYPE="ttl"
                    break
                    ;;
                4)
                    DEVICE_TYPE="rfid"
                    break
                    ;;
                5)
                    DEVICE_TYPE="apa_camera"
                    break
                    ;;
                6)
                    DEVICE_TYPE="apa_arduino"
                    break
                    ;;
                7)
                    DEVICE_TYPE="sound"
                    break
                    ;;
                *)
                    echo "Invalid choice. Please enter 1-2."
                    ;;
            esac
        done
    fi
}

ask_controller_type() {
    if [ "$DEVICE_ROLE" = "controller" ]; then
        echo "Please specify the type of controller - this will affect the GUI primarily:"
        echo "1) Basic SAVIOUR"
        echo "2) APA - Active Place Avoidance"
        echo "3) Habitat"
        echo "4) Acoustic Startle (NOT YET IMPLEMENTED)"
        echo ""
        
        while true; do
            read -p "Enter your choice (1-5): " choice
            case $choice in
                1)
                    DEVICE_TYPE="basic"
                    break
                    ;;
                2)
                    DEVICE_TYPE="apa"
                    break
                    ;;
                3) 
                    DEVICE_TYPE="habitat"
                    break
                    ;;
                4) 
                    DEVICE_TYPE="acoustic_startle"
                    break
                    ;;
                *)
                    echo "Invalid choice. Please enter 1-2."
                    ;;
            esac
        done
    fi
}


# Find the directory and name of the python script for the systemd service
get_python_directory() {
    if [ "$DEVICE_ROLE" = "controller" ]; then
        PYTHON_PATH="src/controller/examples/${DEVICE_TYPE}"
        PYTHON_FILE="${DEVICE}.py"
    fi
    if [ "$DEVICE_ROLE" = "module" ]; then
        PYTHON_PATH="src/modules/examples/${DEVICE_TYPE}"
        PYTHON_FILE="${DEVICE}.py"
    fi
}


# Function to configure systemd service
configure_service() {
    echo "Configuring SAVIOUR Systemd Service"
        
    # Stop existing service if running
    sudo systemctl stop saviour.service 2>/dev/null || true
    
    # Create service file 
    sudo tee /etc/systemd/system/saviour.service > /dev/null <<EOF
[Unit]
Description=Saviour Service
After=network.target ptp4l.service phc2sys.service
Wants=network.target ptp4l.service phc2sys.service

[Service]
Type=simple
User=root
WorkingDirectory=${DIR}/${PYTHON_PATH}
ExecStart=${DIR}/env/bin/python ${PYTHON_FILE}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Environment variables
Environment=PYTHONPATH=${DIR}/src

[Install]
WantedBy=multi-user.target
EOF

    # Reload systemd and enable service
    sudo systemctl daemon-reload
    sudo systemctl enable saviour.service

    echo "Saviour service configured and enabled at boot."
}


configure_samba_share() {
    echo "Configuring Samba Share"
    
    # Create the share directory
    sudo mkdir -p /home/pi/${SHARENAME}
    sudo chown ${USER}:pi /home/pi/${SHARENAME}
    sudo chmod 755 /home/pi/${SHARENAME}
    echo "Created controller_share directory: /home/pi/${SHARENAME}"
    
    # Backup original samba config
    if [ -f /etc/samba/smb.conf ]; then
        sudo cp /etc/samba/smb.conf /etc/samba/smb.conf.backup
        echo "Backed up original smb.conf"
    fi
    
    # Create new samba configuration
    echo "Creating Samba configuration..."
    sudo tee /etc/samba/smb.conf > /dev/null <<EOF
[global]
   workgroup = WORKGROUP
   server string = SAVIOUR Controller
   server role = standalone server
   map to guest = bad user
   dns proxy = no
   log level = 1
   log file = /var/log/samba/%m.log
   max log size = 50

[${SHARENAME}]
   comment = Saviour Controller Share
   path = /home/pi/${SHARENAME}
   browseable = yes
   writable = yes
   guest ok = yes
   create mask = 0644
   directory mask = 0755
   force user = ${USER}
   force group = pi
EOF

    # Set Samba password for pi user (default password: saviour)
    echo "Setting Samba password for {$USER} user..."
    echo -e "saviour\nsaviour" | sudo smbpasswd -s -a pi
    
    # Restart Samba services
    sudo systemctl restart smbd
    sudo systemctl restart nmbd
    
    # Enable Samba services at boot
    sudo systemctl enable smbd
    sudo systemctl enable nmbd
    
    echo "Samba share configured successfully!"
    echo "Share name: ${SHARENAME}"
    echo "Path: /home/pi/${SHARENAME}"
    echo "Username: ${USER}"
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

disable_samba_share() {
    sudo systemctl stop smbd nmbd
    sudo systemctl disable smbd nmbd
    echo Stopped and disabled smbd and nmbd
}

# Function to configure DHCP server
configure_dhcp_server() {
    echo "Configuring DHCP Server"
    
    # Setup static ip
    echo "Setting static IP to 10.0.0.1 with nmcli"
    sudo nmcli connection modify "Wired connection 1" ipv4.method manual
    sudo nmcli connection modify "Wired connection 1" ipv4.addresses 10.0.0.1/24

    # Install dnsmasq
    if ! is_installed "dnsmasq"; then
        echo "[INSTALLING] dnsmasq"
        sudo apt-get install -y dnsmasq
    else
        echo "[OK] dnsmasq is already installed."
    fi

    # Modify DNSMasq service description to start after interfaces are up
    echo "Modifying DNSMasq service description..."
    if [ -f /lib/systemd/system/dnsmasq.service ]; then
        # Backup original service file
        sudo cp /lib/systemd/system/dnsmasq.service /lib/systemd/system/dnsmasq.service.backup
        echo "Backed up original dnsmasq.service"
        
        # Modify the service file to start after network interfaces are up
        sudo sed -i 's/Requires=network.target/Requires=network-online.target/g' /lib/systemd/system/dnsmasq.service
        sudo sed -i 's/After=network.target/After=network-online.target/g' /lib/systemd/system/dnsmasq.service
        echo "Modified DNSMasq service to start after network interfaces are up"
    else
        echo "Warning: dnsmasq.service not found at /lib/systemd/system/dnsmasq.service"
    fi
    
    # Backup original config
    if [ -f /etc/dnsmasq.conf ]; then
        sudo cp /etc/dnsmasq.conf /etc/dnsmasq.conf.backup
        echo "Backed up original dnsmasq.conf"
    fi
    
    # Create new dnsmasq configuration for local network only
    echo "Creating dnsmasq configuration for local network..."
    sudo tee /etc/dnsmasq.conf > /dev/null <<EOF
# dnsmasq configuration for Saviour local network
# This Pi acts as DHCP server for local network only
# No internet routing - devices must use wlan0 for internet

# Listen on ethernet interface only (not wlan0)
interface=eth0
bind-interfaces

# DHCP range for local network (adjust as needed)
dhcp-range=10.0.0.128,10.0.0.255,12h

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
    
    echo "DHCP server configured and enabled."
    echo "To start DHCP server: sudo systemctl start dnsmasq"
    echo "To stop DHCP server: sudo systemctl stop dnsmasq"
}

disable_dhcp_server() {
    echo Disabling DHCP server and reverting IP address to automatic assignment

    # Change IP to automatic assignment
    sudo nmcli connection modify "Wired connection 1" ipv4.method auto

    # Stop DHCP server
    sudo systemctl stop dnsmasq.service
    sudo systemctl disable dnsmasq.service

    echo DHCP server disabled
}

configure_mdns() {
    echo "Configuring controller mDNS via avahi daemon"
    if ! is_installed "avahi-daemon"; then
        echo "Installing avahi-daemon";
        sudo apt install avahi-daemon -y
    else
        echo "avahi-daemon is already installed"
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

    echo "mDNS server configured and enabled."
    echo "Controller will appear on network as saviour.local"

    echo "Configuring iptables to forward port 80 traffic to port 5000"
    sudo iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000
    sudo netfilter-persistent save
}

disable_mdns() {
    echo Disabling mDNS 
    sudo systemctl disable avahi-daemon.service
    sudo systemctl stop avahi-daemon.service

    # Stop forwarding port 80 traffic
    # Check if the rule exists before trying to delete it
    if sudo iptables -t nat -C PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000 2>/dev/null; then
        sudo iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000
        echo "Port forwarding rule deleted"
    else
        echo "Port forwarding rule does not exist"
    fi
    sudo netfilter-persistent save
    echo mDNS disabled
}


configure_ptp_timetransmitter() {
    echo "Configuring PTP to act as timeTransmitter"
    
    # Stop existing services if running
    sudo systemctl stop ptp4l 2>/dev/null || true
    sudo systemctl stop phc2sys 2>/dev/null || true
    
    # Create ptp4l service file
    echo "Creating ptp4l systemd service..."
    sudo tee /etc/systemd/system/ptp4l.service > /dev/null <<EOF
[Unit]
Description=PTP4L (Precision Time Protocol daemon)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStartPre=/bin/sleep 5
ExecStart=/usr/sbin/ptp4l -i eth0 -2 -m -l 6
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    # Create phc2sys service file
    echo "Creating phc2sys systemd service..."
    sudo tee /etc/systemd/system/phc2sys.service > /dev/null <<EOF
[Unit]
Description=PHC2SYS (PTP Hardware Clock to System Clock synchronization)
After=ptp4l.service
Wants=ptp4l.service

[Service]
Type=simple
User=root
ExecStart=/usr/sbin/phc2sys -a -r -r
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    echo "PTP configuration:"
    echo "  - ptp4l: timeTransmitter mode (-m flag, log level 6)"
    echo "  - phc2sys: Autoconfiguration with system clock sync (-a -r -r)"

    # Reload systemd and enable services
    sudo systemctl daemon-reload
    
    # Enable services to start at boot
    sudo systemctl enable ptp4l
    sudo systemctl enable phc2sys
}


configure_ptp_timereceiver() {
    echo "Configuring PTP to act as timeReceiver"
    
    # Stop existing services if running
    sudo systemctl stop ptp4l 2>/dev/null || true
    sudo systemctl stop phc2sys 2>/dev/null || true
    
    # Create ptp4l service file
    echo "Creating ptp4l systemd service..."
    sudo tee /etc/systemd/system/ptp4l.service > /dev/null <<EOF
[Unit]
Description=PTP4L (Precision Time Protocol daemon)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStartPre=/bin/sleep 5
ExecStart=/usr/sbin/ptp4l -i eth0 -m -s -2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    # Create phc2sys service file
    echo "Creating phc2sys systemd service..."
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

    echo "PTP configuration:"
    echo "  - ptp4l: timeReceiver mode (-s -m flags)"
    echo "  - phc2sys: Manual configuration with PTP hardware clock (-s /dev/ptp0 -w -m)"

    # Reload systemd and enable services
    sudo systemctl daemon-reload
    
    # Enable services to start at boot
    sudo systemctl enable ptp4l
    sudo systemctl enable phc2sys
}

build_frontend() {
    echo "Installing nvm, Node.js, vite, and building frontend"
    # Check if nvm is installed
    if [ -d "$HOME/.nvm" ]; then
        echo "[OK] nvm already installed"
    else
        echo "Installing nvm"
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
    fi
    # Load nvm into the current shell session
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

    # Check if Node.js v22 is installed
    if nvm ls 22 >/dev/null 2>&1; then
        echo "[OK] Node.js v22 already installed"
    else
        echo "Installing Node.js v22"
        nvm install 22
    fi

    echo "Node version: $(node -v)"
    echo "NPM version: $(npm -v)"

    echo "Setting correct frontend for ${DEVICE}"
    sudo tee src/controller/frontend/src/main.jsx > /dev/null <<EOF
import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './${DEVICE_TYPE}/App';
import { BrowserRouter } from "react-router-dom";

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <BrowserRouter>
      <App /> {/*Change this to reflect which controller frontend is being used.*/}
    </BrowserRouter>
  </React.StrictMode>
);
EOF

    echo "Installing vite and building frontend"
    cd src/controller/frontend/
    npm install
    npm run build
    echo "Frontend built"
    echo "nvm, Node.js, vite installed and frontend built"
    cd ../../../
}

# TODO: Configure ptp, ntp

# Main Program
get_current_role
echo "Currently a $CURRENT_TYPE $CURRENT_ROLE";
ask_user_role
ask_module_type
ask_controller_type

DEVICE="${DEVICE_TYPE}_${DEVICE_ROLE}"

echo ""
echo Device will be configured as a "${DEVICE}."

ROLE_CHANGED=false
TYPE_CHANGED=false

[ "$DEVICE_ROLE" != "$CURRENT_ROLE" ] && ROLE_CHANGED=true
[ "$DEVICE_TYPE" != "$CURRENT_TYPE" ] && TYPE_CHANGED=true

if ! $ROLE_CHANGED && ! $TYPE_CHANGED; then
    echo "No changes detected. Device is already configured as ${DEVICE_TYPE} ${DEVICE_ROLE}."
    exit 0
fi

if $ROLE_CHANGED; then
    if [ "$DEVICE_ROLE" = "controller" ]; then
        configure_ptp_timetransmitter
        configure_samba_share
        configure_dhcp_server
        configure_mdns
    fi
    if [ "$DEVICE_ROLE" = "module" ]; then
        configure_ptp_timereceiver
        disable_samba_share
        disable_dhcp_server
        disable_mdns
    fi
fi

if $TYPE_CHANGED; then
    if [ "$DEVICE_ROLE" = "controller" ]; then
        build_frontend
    fi
fi

get_python_directory

echo ""
echo Python file at "${PYTHON_PATH}/${PYTHON_FILE}"
echo Checking it is there: `ls ${PYTHON_PATH} | grep ${PYTHON_FILE}` # Check it's there, grep returns 0 if it finds a match.

configure_service
echo ""
echo File was created: /etc/systemd/system/`ls /etc/systemd/system/ | grep saviour`


echo ""

if [ "$DEVICE_TYPE" = "microphone" ]; then
    sudo install -d /etc/pipewire/pipewire.conf.d
    sudo tee /etc/pipewire/pipewire.conf.d/99-sample-rates.conf >/dev/null <<'EOF'
    context.properties = {
        default.clock.rate = 192000
        default.clock.allowed-rates = [ 96000 192000 384000]
    }
EOF
    sudo -u pi pkill -9 pipewire
    sudo -u pi pkill -9 wireplumber
    sudo -u pi pipewire &
    sudo -u pi wireplumber &
fi

echo ""
echo "Writing new role to config file /etc/saviour/config"
write_new_role_to_file


# Run pytest?
echo ""
echo "Running test suite"
source env/bin/activate
pytest "src/$DEVICE_ROLE/"

echo ""
echo "Restarting saviour.service"
sudo systemctl restart saviour.service


echo ""
echo "Device successfully set to ${DEVICE}."
if [ $ROLE_CHANGED == true]; then
    echo "Please reboot now."
fi
