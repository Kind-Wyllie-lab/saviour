#!/usr/bin/env bash
# Switch Role
# Script used to switch which role the SAVIOUR device is configured for - controller or module and variant.

set -Eeuo pipefail # If any function throws an error (doesn't return 0), exit immediately.
trap 'rc=$?; echo "switch_role.sh failed with exit code $rc at line $LINENO"' ERR

USER=`whoami`
SCRIPT_PATH="$(readlink -f "$0")"
DIR="$(dirname "$SCRIPT_PATH")"
SHARENAME="controller_share"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

is_installed() {
    dpkg -s "$1" &>/dev/null
}

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

# ---------------------------------------------------------------------------
# Role / type selection
# ---------------------------------------------------------------------------

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
            read -p "Enter your choice (1-7): " choice
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
                    echo "Invalid choice. Please enter 1-7."
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
        echo "4) Acoustic Startle"
        echo ""

        while true; do
            read -p "Enter your choice (1-4): " choice
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
                    echo "Invalid choice. Please enter 1-4."
                    ;;
            esac
        done
    fi
}


# ---------------------------------------------------------------------------
# Hostname / path helpers
# ---------------------------------------------------------------------------

# Find the directory and name of the python script for the systemd service
get_python_directory() {
    : "${DEVICE_ROLE:?DEVICE_ROLE not set}"
    : "${DEVICE_TYPE:?DEVICE_TYPE not set}"
    : "${DEVICE:?DEVICE not set}"
    if [ "${DEVICE_ROLE}" = "controller" ]; then
        PYTHON_PATH="src/controller/examples/${DEVICE_TYPE}"
        PYTHON_FILE="${DEVICE}.py"
    fi
    if [ "$DEVICE_ROLE" = "module" ]; then
        PYTHON_PATH="src/modules/examples/${DEVICE_TYPE}"
        PYTHON_FILE="${DEVICE}.py"
    fi
}


get_mac_suffix() {
    MAC=$(cat /sys/class/net/eth0/address 2>/dev/null)

    # Remove colons
    MAC_CLEAN=${MAC//:/}

    # Last 8 hex chars = last 4 bytes
    MAC_SUFFIX=${MAC_CLEAN: -4}

    echo "$MAC_SUFFIX"
}


generate_hostname() {
    MAC_SUFFIX=$(get_mac_suffix)
    NEW_HOSTNAME="${DEVICE_TYPE}-${DEVICE_ROLE}-${MAC_SUFFIX}"
    NEW_HOSTNAME=$(echo "$NEW_HOSTNAME" | tr '_' '-') # Sanitise hostname, no underscores allowed
    echo "$NEW_HOSTNAME"
}


set_device_hostname() {
    NEW_HOSTNAME=$(generate_hostname)

    echo "Setting hostname to $NEW_HOSTNAME"

    # hostnamectl first — /etc/hosts still has the old name so sudo can resolve it.
    sudo hostnamectl set-hostname "$NEW_HOSTNAME"

    # Now update /etc/hosts with the new name.
    sudo tee /etc/hosts >/dev/null <<EOF
127.0.0.1  localhost
127.0.1.1  $NEW_HOSTNAME

::1        localhost ip6-localhost ip6-loopback
EOF

    # Trixie bug: cloud-init re-runs on every boot (fixed instance_id) and
    # overrides hostname and /etc/hosts from its cache and user-data.
    # These are dedicated SAVIOUR devices — disable cloud-init so it cannot
    # interfere after initial imaging setup is done.
    if [ -d /etc/cloud ]; then
        sudo touch /etc/cloud/cloud-init.disabled
        echo "  Disabled cloud-init (prevents hostname revert on reboot)"
    fi
}



create_recording_folder() {
    sudo mkdir -p /var/lib/saviour/recordings
    sudo chown -R root:root /var/lib/saviour
}


# ---------------------------------------------------------------------------
# Systemd service
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Samba share
# ---------------------------------------------------------------------------

configure_samba_share() {
    echo "Configuring Samba Share"

    # ── 1. Linux group and users ──────────────────────────────────────────
    # Create shared group for the share
    if ! getent group saviour > /dev/null 2>&1; then
        sudo groupadd saviour
        echo "Created group: saviour"
    fi

    # Add pi to the saviour group so it can write to the share directory
    sudo usermod -aG saviour pi

    # Create saviour_module system user (no login shell, no home dir)
    if ! id saviour_module > /dev/null 2>&1; then
        sudo useradd --system --no-create-home --shell /usr/sbin/nologin \
            --gid saviour saviour_module
        echo "Created system user: saviour_module"
    fi

    # Create researcher system user (read-only access to the share)
    if ! id researcher > /dev/null 2>&1; then
        sudo useradd --system --no-create-home --shell /usr/sbin/nologin \
            --gid saviour researcher
        echo "Created system user: researcher"
    fi

    # Create sidbit system user (IT admin, full access to the share)
    if ! id sidbit > /dev/null 2>&1; then
        sudo useradd --system --no-create-home --shell /usr/sbin/nologin \
            --gid saviour sidbit
        echo "Created system user: sidbit"
    fi

    # ── 2. Share directory ────────────────────────────────────────────────
    # Allow non-pi users to traverse /home/pi to reach the share
    sudo chmod 711 /home/pi
    sudo mkdir -p /home/pi/${SHARENAME}
    sudo chown pi:saviour /home/pi/${SHARENAME}
    # 2775 = rwxrwsr-x: setgid so new files inherit the saviour group;
    # group-write lets saviour_module, researcher, and sidbit all create and delete files.
    sudo chmod 2775 /home/pi/${SHARENAME}
    echo "Share directory: /home/pi/${SHARENAME} (mode 2775, owner pi:saviour)"

    # ── 3. Generate a random password for saviour_module ─────────────────
    sudo mkdir -p /etc/saviour
    MODULE_PASS=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 20)
    sudo tee /etc/saviour/samba_credentials > /dev/null <<CREDS
username=saviour_module
password=${MODULE_PASS}
CREDS
    sudo chmod 600 /etc/saviour/samba_credentials
    echo "Generated saviour_module password → /etc/saviour/samba_credentials"

    # ── 4. Write credentials into modules/config/base_config.json ────────
    BASE_CONFIG="${DIR}/src/modules/config/base_config.json"
    if [ -f "${BASE_CONFIG}" ]; then
        python3 - "${BASE_CONFIG}" "${MODULE_PASS}" <<'PYEOF'
import sys, json
path, password = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)
export = cfg.setdefault("export", {})
export["share_username"] = "saviour_module"
export["share_password"] = password
# Remove old underscore-prefixed keys if present
export.pop("_share_username", None)
export.pop("_share_password", None)
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print("Updated base_config.json with saviour_module credentials")
PYEOF
    else
        echo "WARNING: ${BASE_CONFIG} not found — module credentials not written"
    fi

    # ── 5. Samba configuration ────────────────────────────────────────────
    if [ -f /etc/samba/smb.conf ]; then
        sudo cp /etc/samba/smb.conf /etc/samba/smb.conf.backup
        echo "Backed up original smb.conf"
    fi

    sudo tee /etc/samba/smb.conf > /dev/null <<EOF
[global]
   workgroup = WORKGROUP
   server string = SAVIOUR Controller
   server role = standalone server
   dns proxy = no
   log level = 1
   log file = /var/log/samba/%m.log
   max log size = 50

[${SHARENAME}]
   comment = SAVIOUR Controller Share
   path = /home/pi/${SHARENAME}
   browseable = yes
   read only = yes
   valid users = researcher, saviour_module, sidbit
   write list = researcher, saviour_module, sidbit
   admin users = sidbit
   create mask = 0664
   directory mask = 0775
   oplocks = no
   level2 oplocks = no
   notify daemon = yes
EOF

    # ── 6. Set Samba passwords ────────────────────────────────────────────
    echo "Setting Samba password for saviour_module..."
    printf '%s\n%s\n' "${MODULE_PASS}" "${MODULE_PASS}" | sudo smbpasswd -s -a saviour_module

    echo "Setting Samba password for researcher..."
    printf 'getmyfiles\ngetmyfiles\n' | sudo smbpasswd -s -a researcher

    echo "Setting Samba password for sidbit..."
    printf 'espressocreme\nespressocreme\n' | sudo smbpasswd -s -a sidbit

    # ── 7. Restart and enable Samba ───────────────────────────────────────
    sudo systemctl restart smbd nmbd
    sudo systemctl enable smbd nmbd

    CONTROLLER_IP=$(hostname -I | awk '{print $1}')
    echo ""
    echo "Samba share configured successfully!"
    echo "  Share name : ${SHARENAME}"
    echo "  Path       : /home/pi/${SHARENAME}"
    echo ""
    echo "Access tiers:"
    echo "  Researcher (read/write/delete) : username=researcher   password=getmyfiles"
    echo "  Module (write)         : username=saviour_module  password stored in /etc/saviour/samba_credentials"
    echo "  Admin (full)           : username=sidbit       password=espressocreme"
}

disable_samba_share() {
    sudo systemctl stop smbd nmbd
    sudo systemctl disable smbd nmbd
    echo Stopped and disabled smbd and nmbd
}


# ---------------------------------------------------------------------------
# Gateway / network configuration
# ---------------------------------------------------------------------------

# Ask how the PoE network connects to the internet (or doesn't).
# Sets globals: GATEWAY_MODE ("none" | "external" | "controller")
#               GATEWAY      (IP of external router, if GATEWAY_MODE=external)
#               WAN_INTERFACE (outbound interface, if GATEWAY_MODE=controller)
ask_gateway_mode() {
    GATEWAY=""
    GATEWAY_MODE=""
    WAN_INTERFACE=""

    echo ""
    echo "How should this controller handle internet connectivity for the PoE network?"
    echo "1) No gateway — offline network (modules have no internet access)"
    echo "2) External gateway — a router at a known IP provides internet"
    echo "3) Controller is the gateway — this device shares internet from wlan0 (or another interface)"
    echo ""

    while true; do
        read -p "Enter your choice (1-3): " choice
        case $choice in
            1)
                GATEWAY_MODE="none"
                echo "  Offline network — no gateway will be advertised to modules."
                break
                ;;
            2)
                GATEWAY_MODE="external"
                # Try to detect an existing default route on this device
                DETECTED_GW=$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')
                if [ -n "$DETECTED_GW" ]; then
                    echo "  Detected existing default gateway: $DETECTED_GW"
                    read -p "  Use $DETECTED_GW? (y/n): " use_detected
                    if [ "$use_detected" = "y" ] || [ "$use_detected" = "Y" ]; then
                        GATEWAY="$DETECTED_GW"
                    fi
                fi
                if [ -z "$GATEWAY" ]; then
                    read -p "  Enter gateway IP (e.g. 10.0.0.2 or 192.168.1.1): " GATEWAY
                fi
                echo "  External gateway: $GATEWAY"
                break
                ;;
            3)
                GATEWAY_MODE="controller"
                # Detect the WAN interface (the default route that isn't eth0)
                DETECTED_WAN=$(ip route show default 2>/dev/null | awk '/default/ && $5 != "eth0" {print $5; exit}')
                if [ -n "$DETECTED_WAN" ]; then
                    echo "  Detected internet-facing interface: $DETECTED_WAN"
                    read -p "  Use $DETECTED_WAN as the WAN interface? (y/n): " use_wan
                    if [ "$use_wan" = "y" ] || [ "$use_wan" = "Y" ]; then
                        WAN_INTERFACE="$DETECTED_WAN"
                    fi
                fi
                if [ -z "$WAN_INTERFACE" ]; then
                    read -p "  Enter the internet-facing interface (e.g. wlan0, eth1): " WAN_INTERFACE
                fi
                echo "  Controller will share internet from $WAN_INTERFACE to the PoE network."
                break
                ;;
            *)
                echo "Invalid choice. Please enter 1, 2, or 3."
                ;;
        esac
    done
}

set_own_ip() {
    # Derive the first three octets from the external gateway IP, or use 10.0.0 as default.
    if [ "$GATEWAY_MODE" = "external" ] && [ -n "$GATEWAY" ]; then
        IFS='.' read -r a b c d <<< "$GATEWAY"
    else
        a="10"; b="0"; c="0"
    fi

    DEVICE_IP="$a.$b.$c.1/16"
    echo "Proposed controller IP: $DEVICE_IP on $INTERFACE"
    read -p "Accept this IP? (press Enter to accept, or type a custom address e.g. 10.0.0.1/16): " ip_choice

    if [ -n "$ip_choice" ]; then
        DEVICE_IP="$ip_choice"
    fi

    # Re-parse octets from the final chosen IP (strip prefix length for later use in DHCP config)
    IFS='.' read -r a b c d <<< "${DEVICE_IP%%/*}"

    echo "Setting controller IP to $DEVICE_IP on $INTERFACE"

    if [ "$GATEWAY_MODE" = "external" ] && [ -n "$GATEWAY" ]; then
        sudo nmcli connection modify "$INTERFACE" \
            ipv4.addresses "$DEVICE_IP" \
            ipv4.gateway "$GATEWAY" \
            ipv4.dns "8.8.8.8,1.1.1.1" \
            ipv4.method manual
    else
        # none or controller modes: no upstream gateway on the PoE interface
        sudo nmcli connection modify "$INTERFACE" \
            ipv4.addresses "$DEVICE_IP" \
            ipv4.gateway "" \
            ipv4.dns "" \
            ipv4.method manual
    fi
}

detect_interface_name() {
    INTERFACE=$(nmcli -t -f GENERAL.CONNECTION device show eth0 | cut -d: -f2-)
}


# Enable IP forwarding and NAT masquerade so the PoE network can reach the
# internet through this controller's WAN_INTERFACE (e.g. wlan0).
configure_ip_forwarding() {
    echo "Enabling IP forwarding and NAT masquerade on ${WAN_INTERFACE}..."

    # Persist IP forwarding across reboots
    sudo tee /etc/sysctl.d/99-saviour-forwarding.conf > /dev/null <<EOF
net.ipv4.ip_forward = 1
EOF
    sudo sysctl -p /etc/sysctl.d/99-saviour-forwarding.conf

    # NAT masquerade: rewrite source addresses for traffic leaving via WAN_INTERFACE
    # so return packets are routed back correctly.
    # Delete first to avoid duplicates on re-run.
    sudo iptables -t nat -D POSTROUTING -o "$WAN_INTERFACE" -j MASQUERADE 2>/dev/null || true
    sudo iptables -t nat -A POSTROUTING -o "$WAN_INTERFACE" -j MASQUERADE

    # Allow forwarded traffic in both directions
    sudo iptables -D FORWARD -i eth0 -o "$WAN_INTERFACE" -j ACCEPT 2>/dev/null || true
    sudo iptables -D FORWARD -i "$WAN_INTERFACE" -o eth0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
    sudo iptables -A FORWARD -i eth0 -o "$WAN_INTERFACE" -j ACCEPT
    sudo iptables -A FORWARD -i "$WAN_INTERFACE" -o eth0 -m state --state RELATED,ESTABLISHED -j ACCEPT

    # Ensure iptables-persistent is installed so rules survive reboots
    if ! is_installed "iptables-persistent"; then
        echo "[INSTALLING] iptables-persistent"
        echo "iptables-persistent iptables-persistent/autosave_v4 boolean true" | sudo debconf-set-selections
        echo "iptables-persistent iptables-persistent/autosave_v6 boolean true" | sudo debconf-set-selections
        sudo apt-get install -y iptables-persistent
    else
        echo "[OK] iptables-persistent already installed."
    fi
    sudo netfilter-persistent save

    echo "  IP forwarding and NAT masquerade configured on ${WAN_INTERFACE}."
}


# ---------------------------------------------------------------------------
# DHCP server
# ---------------------------------------------------------------------------

# Function to configure DHCP server
configure_dhcp_server() {
    echo "Configuring DHCP Server"

    # Setup static IP
    echo "Setting static IP with nmcli"
    detect_interface_name
    set_own_ip

    # Install dnsmasq
    if ! is_installed "dnsmasq"; then
        echo "[INSTALLING] dnsmasq"
        sudo apt-get install -y dnsmasq
    else
        echo "[OK] dnsmasq is already installed."
    fi

    # Modify DNSMasq service to start after interfaces are up
    echo "Modifying DNSMasq service description..."
    if [ -f /lib/systemd/system/dnsmasq.service ]; then
        sudo cp /lib/systemd/system/dnsmasq.service /lib/systemd/system/dnsmasq.service.backup
        echo "Backed up original dnsmasq.service"

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

    # Build the dhcp-option=3 line based on gateway mode:
    #   none       → no default gateway advertised to modules
    #   external   → advertise the upstream router
    #   controller → advertise this controller's own PoE IP as the gateway
    case "$GATEWAY_MODE" in
        external)
            DHCP_OPTION="dhcp-option=3,$GATEWAY"
            ;;
        controller)
            DHCP_OPTION="dhcp-option=3,$a.$b.$c.1"
            ;;
        *)
            DHCP_OPTION="dhcp-option=3"
            ;;
    esac

    # DHCP range: .128–.255 within the same /24 segment as the controller,
    # with an explicit /16 subnet mask so all 10.0.x.x addresses are reachable.
    # .1–.127 reserved for static assignments (controller, switches, etc).
    sudo tee /etc/dnsmasq.conf > /dev/null <<EOF
# dnsmasq configuration for SAVIOUR local network
interface=eth0
bind-interfaces

# DHCP range — .128 to .255 in the controller's /24, /16 subnet mask
dhcp-range=$a.$b.$c.128,$a.$b.$c.255,255.255.0.0,12h

# Default gateway advertised to DHCP clients
$DHCP_OPTION

# Disable DNS server (DHCP only)
port=0

# Log DHCP leases
dhcp-leasefile=/var/lib/misc/dnsmasq.leases

dhcp-authoritative
log-queries
log-dhcp
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable dnsmasq
    sudo systemctl restart dnsmasq.service

    echo "DHCP server configured and enabled."
}

disable_dhcp_server() {
    echo Disabling DHCP server and reverting IP address to automatic assignment

    detect_interface_name
    sudo nmcli connection modify "$INTERFACE" ipv4.method auto

    sudo systemctl stop dnsmasq.service
    sudo systemctl disable dnsmasq.service

    echo DHCP server disabled
}


# ---------------------------------------------------------------------------
# mDNS / iptables
# ---------------------------------------------------------------------------

configure_mdns() {
    echo "Configuring controller mDNS via avahi daemon"
    if ! is_installed "avahi-daemon"; then
        echo "[INSTALLING] avahi-daemon"
        sudo apt install avahi-daemon -y
    else
        echo "[OK] avahi-daemon is already installed."
    fi

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
    sudo systemctl daemon-reload
    sudo systemctl enable --now avahi-daemon
    sudo systemctl restart avahi-daemon.service

    echo "mDNS server configured and enabled."
    echo "Controller will appear on network as saviour.local"

    # Ensure iptables-persistent is installed before saving rules
    if ! is_installed "iptables-persistent"; then
        echo "[INSTALLING] iptables-persistent"
        echo "iptables-persistent iptables-persistent/autosave_v4 boolean true" | sudo debconf-set-selections
        echo "iptables-persistent iptables-persistent/autosave_v6 boolean true" | sudo debconf-set-selections
        sudo apt-get install -y iptables-persistent
    else
        echo "[OK] iptables-persistent already installed."
    fi

    echo "Configuring iptables to forward port 80 traffic to port 5000"
    # Remove existing rule first to avoid duplicates on re-run
    sudo iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000 2>/dev/null || true
    sudo iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000
    sudo netfilter-persistent save
}

disable_mdns() {
    echo Disabling mDNS
    sudo systemctl disable avahi-daemon.service
    sudo systemctl stop avahi-daemon.service

    if sudo iptables -t nat -C PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000 2>/dev/null; then
        sudo iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000
        echo "Port forwarding rule deleted"
    else
        echo "Port forwarding rule does not exist"
    fi
    if is_installed "iptables-persistent"; then
        sudo netfilter-persistent save
    fi
    echo mDNS disabled
}


# ---------------------------------------------------------------------------
# PTP
# ---------------------------------------------------------------------------

configure_ptp_timetransmitter() {
    echo "Configuring PTP to act as timeTransmitter"

    sudo systemctl stop ptp4l 2>/dev/null || true
    sudo systemctl stop phc2sys 2>/dev/null || true

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

    echo "Creating phc2sys systemd service..."
    sudo tee /etc/systemd/system/phc2sys.service > /dev/null <<EOF
[Unit]
Description=PHC2SYS (PTP Hardware Clock to System Clock synchronization)
After=ptp4l.service
Wants=ptp4l.service

[Service]
Type=simple
User=root
ExecStartPre=/bin/sleep 3
ExecStart=/usr/sbin/phc2sys -a -r -r -m
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    echo "PTP configuration:"
    echo "  - ptp4l: timeTransmitter mode (-m flag, log level 6)"
    echo "  - phc2sys: Autoconfiguration with system clock sync (-a -r -r)"

    sudo systemctl daemon-reload
    sudo systemctl enable ptp4l
    sudo systemctl enable phc2sys
}


configure_ptp_timereceiver() {
    echo "Configuring PTP to act as timeReceiver"

    # Disable timesyncd — it conflicts with phc2sys on modules.
    echo "Disabling systemd-timesyncd (conflicts with phc2sys on timeReceiver)"
    sudo timedatectl set-ntp false
    sudo systemctl disable --now systemd-timesyncd 2>/dev/null || true

    sudo systemctl stop ptp4l 2>/dev/null || true
    sudo systemctl stop phc2sys 2>/dev/null || true

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

    echo "Creating phc2sys systemd service..."
    sudo tee /etc/systemd/system/phc2sys.service > /dev/null <<EOF
[Unit]
Description=PHC2SYS (PTP Hardware Clock to System Clock synchronization)
After=ptp4l.service
Wants=ptp4l.service

[Service]
Type=simple
User=root
ExecStart=/usr/sbin/phc2sys -s /dev/ptp0 -w -m -R 8
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    echo "PTP configuration:"
    echo "  - ptp4l: timeReceiver mode (-s -m flags)"
    echo "  - phc2sys: Manual configuration with PTP hardware clock (-s /dev/ptp0 -w -m)"

    sudo systemctl daemon-reload
    sudo systemctl enable ptp4l
    sudo systemctl enable phc2sys
}


# ---------------------------------------------------------------------------
# Module-specific setup
# ---------------------------------------------------------------------------

configure_microphone() {
    echo "Installing AudioMoth udev rule..."
    sudo tee /etc/udev/rules.d/99-audiomoth.rules > /dev/null <<'EOF'
# Allow non-root access to AudioMoth USB Microphone (VID 16d0, PID 06f3)
SUBSYSTEM=="usb", ATTRS{idVendor}=="16d0", ATTRS{idProduct}=="06f3", MODE="0666"
EOF
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    echo "AudioMoth udev rule installed."

    echo "Installing pipewire"

    sudo mkdir -p /etc/pipewire/pipewire.conf.d

    sudo tee /etc/pipewire/pipewire.conf.d/99-sample-rates.conf >/dev/null <<'EOF'
context.properties = {
    default.clock.rate = 192000
    default.clock.allowed-rates = [ 96000 192000 384000]
}
EOF
    systemctl --user enable pipewire
    systemctl --user enable pipewire-pulse
    systemctl --user enable wireplumber
    systemctl --user restart pipewire
    systemctl --user restart pipewire-pulse
    systemctl --user restart wireplumber

    sudo tee /etc/systemd/system/saviour.service >/dev/null <<EOF
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
Environment=XDG_RUNTIME_DIR=/run/user/1000
Environment=PULSE_SERVER=unix:/run/user/1000/pulse/native

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable saviour.service

    echo "Microphone SAVIOUR service updated."
}


configure_apa_camera() {
    echo "Installing APA camera (IMX500) dependencies..."

    if ! is_installed "python3-openexr"; then
        echo "[INSTALLING] python3-openexr"
        sudo apt-get install -y python3-openexr
    else
        echo "[OK] python3-openexr is already installed."
    fi

    if ! is_installed "imx500-all"; then
        echo "[INSTALLING] imx500-all"
        sudo apt-get install -y imx500-all
    else
        echo "[OK] imx500-all is already installed."
    fi

    if ! is_installed "hailo-all"; then
        echo "[INSTALLING] hailo-all"
        sudo apt-get install -y hailo-all
    else
        echo "[OK] hailo-all is already installed."
    fi
}


# ---------------------------------------------------------------------------
# Frontend build
# ---------------------------------------------------------------------------

build_frontend() {
    echo "Installing nvm, Node.js, vite, and building frontend"
    if [ -d "$HOME/.nvm" ]; then
        echo "[OK] nvm already installed"
    else
        echo "Installing nvm"
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
    fi
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

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


# ---------------------------------------------------------------------------
# Main Program
# ---------------------------------------------------------------------------

get_current_role
echo "Device is currently configured as a(n) $CURRENT_TYPE $CURRENT_ROLE";

# Get the role (controller/module) and type (habitat, camera, sound etc)
ask_user_role
ask_module_type
ask_controller_type

# Parse the device e.g. apa_controller, camera_module
DEVICE="${DEVICE_TYPE}_${DEVICE_ROLE}"
echo ""
echo Device will be configured as a "${DEVICE}."

# Determine whether the device role has changed, and if so in what way
ROLE_CHANGED=false
TYPE_CHANGED=false
[ "$DEVICE_ROLE" != "$CURRENT_ROLE" ] && ROLE_CHANGED=true
[ "$DEVICE_TYPE" != "$CURRENT_TYPE" ] && TYPE_CHANGED=true
if ! $ROLE_CHANGED && ! $TYPE_CHANGED; then
    echo "No changes detected. Device is already configured as ${DEVICE_TYPE} ${DEVICE_ROLE}."
    exit 0
fi

if $TYPE_CHANGED; then
    set_device_hostname
    if [ "$DEVICE_ROLE" = "controller" ]; then
        build_frontend
    fi
fi


if $ROLE_CHANGED; then
    if [ "$DEVICE_ROLE" = "controller" ]; then
        ask_gateway_mode
        configure_ptp_timetransmitter
        configure_samba_share
        configure_dhcp_server
        if [ "$GATEWAY_MODE" = "controller" ]; then
            configure_ip_forwarding
        fi
        configure_mdns
    fi
    if [ "$DEVICE_ROLE" = "module" ]; then
        configure_ptp_timereceiver
        disable_samba_share
        disable_dhcp_server
        disable_mdns
    fi
fi


if [ "$DEVICE_ROLE" = "module" ]; then
    create_recording_folder
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
    echo "Configuring microphone"
    configure_microphone
fi

if [ "$DEVICE_TYPE" = "apa_camera" ]; then
    echo "Configuring APA camera"
    configure_apa_camera
fi



# Run pytest?
echo ""
echo "Running test suite"
#source env/bin/activate
#if [ $DEVICE_ROLE == "module" ]; then
#    pytest "src/modules"
#else
#    pytest "src/controller"
#fi

echo ""
echo "Restarting saviour.service"
sudo systemctl restart saviour.service


echo ""
echo "Device successfully set to ${DEVICE}."

echo ""
echo "Writing new role to config file /etc/saviour/config"
write_new_role_to_file

if [ $ROLE_CHANGED == true ]; then
    echo "Please reboot now."
fi
