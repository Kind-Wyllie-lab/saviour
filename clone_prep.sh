#!/usr/bin/env bash
# SAVIOUR Clone Prep Script
# Run this on a Pi whose SD card was copied from another SAVIOUR device.
# Resets all instance-specific state so switch_role.sh can be run fresh.

set -Eeuo pipefail
trap 'rc=$?; echo "clone_prep.sh failed at line $LINENO (exit $rc)"' ERR

INSTALL_DIR="/usr/local/src/saviour"

echo "======================================="
echo " SAVIOUR Clone Prep"
echo "======================================="
echo ""
echo "This script resets instance-specific state on a cloned Pi:"
echo "  - SSH host keys       (prevents fingerprint conflicts)"
echo "  - Machine ID          (unique per device)"
echo "  - /etc/saviour/config (clears role so switch_role.sh runs fresh)"
echo "  - Hostname            (set to 'saviour-unconfigured')"
echo "  - eth0 IP assignment  (reset to DHCP — was static if source was controller)"
echo "  - iptables port-forward rule (80 → 5000, controller-only)"
echo "  - SAVIOUR active config files"
echo "  - Samba credentials   (switch_role.sh will regenerate)"
echo ""
echo "After this script you should run: sudo ./switch_role.sh"
echo ""
read -p "Continue? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi
echo ""


# ── 1. SSH host keys ──────────────────────────────────────────────────────────
echo "[1/8] Regenerating SSH host keys..."
sudo rm -f /etc/ssh/ssh_host_*
sudo ssh-keygen -A
echo "  Done — new host keys generated."
echo "  Note: any existing SSH clients will see a changed host key warning."


# ── 2. Machine ID ─────────────────────────────────────────────────────────────
echo "[2/8] Resetting machine ID..."
sudo rm -f /etc/machine-id /var/lib/dbus/machine-id
sudo systemd-machine-id-setup
# Ensure dbus symlink is in place (modern systemd uses it, older Debian may not)
if [ ! -e /var/lib/dbus/machine-id ]; then
    sudo ln -s /etc/machine-id /var/lib/dbus/machine-id
fi
echo "  New machine ID: $(cat /etc/machine-id)"


# ── 3. SAVIOUR role config ────────────────────────────────────────────────────
echo "[3/8] Clearing SAVIOUR role config..."
if [ -f /etc/saviour/config ]; then
    OLD_ROLE=$(grep '^ROLE=' /etc/saviour/config | cut -d= -f2 || true)
    OLD_TYPE=$(grep '^TYPE=' /etc/saviour/config | cut -d= -f2 || true)
    echo "  Source device was: ${OLD_TYPE:-unknown} ${OLD_ROLE:-unknown}"
    sudo rm -f /etc/saviour/config
    echo "  Removed /etc/saviour/config"
else
    echo "  /etc/saviour/config not found — already clean"
fi

# Remove Samba credentials — switch_role.sh regenerates these with a fresh password
if [ -f /etc/saviour/samba_credentials ]; then
    sudo rm -f /etc/saviour/samba_credentials
    echo "  Removed /etc/saviour/samba_credentials"
fi


# ── 4. Hostname ───────────────────────────────────────────────────────────────
echo "[4/8] Setting placeholder hostname..."
TEMP_HOSTNAME="saviour-unconfigured"
sudo bash -c "
    echo '${TEMP_HOSTNAME}' > /etc/hostname
    hostname '${TEMP_HOSTNAME}'
    cat > /etc/hosts <<'HOSTS'
127.0.0.1  localhost
127.0.1.1  ${TEMP_HOSTNAME}

::1        localhost ip6-localhost ip6-loopback
HOSTS
"
echo "  Hostname set to: $TEMP_HOSTNAME"
echo "  (switch_role.sh will assign the correct MAC-derived hostname)"


# ── 5. eth0 IP — reset to DHCP ───────────────────────────────────────────────
echo "[5/8] Resetting eth0 to DHCP..."
INTERFACE=$(nmcli -t -f GENERAL.CONNECTION device show eth0 2>/dev/null | cut -d: -f2- || true)
if [ -n "$INTERFACE" ]; then
    CURRENT_METHOD=$(nmcli -t -f ipv4.method connection show "$INTERFACE" 2>/dev/null | cut -d: -f2 || true)
    if [ "$CURRENT_METHOD" = "manual" ]; then
        sudo nmcli connection modify "$INTERFACE" \
            ipv4.method auto \
            ipv4.addresses "" \
            ipv4.gateway "" \
            ipv4.dns "" 2>/dev/null || true
        echo "  Reset $INTERFACE from static to DHCP"
    else
        echo "  $INTERFACE is already set to auto — no change needed"
    fi
else
    echo "  Could not detect NetworkManager connection for eth0 — check manually if needed"
fi


# ── 6. iptables port-forward (80 → 5000) ─────────────────────────────────────
echo "[6/8] Removing controller iptables port-forward rule (if present)..."
if sudo iptables -t nat -C PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000 2>/dev/null; then
    sudo iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000
    sudo netfilter-persistent save 2>/dev/null || true
    echo "  Removed iptables rule 80 → 5000"
else
    echo "  Rule not present — skipped"
fi


# ── 7. SAVIOUR active config files ───────────────────────────────────────────
echo "[7/8] Removing stale active config files..."
MODULE_ACTIVE="${INSTALL_DIR}/src/modules/config/active_config.json"
CONTROLLER_ACTIVE="${INSTALL_DIR}/src/controller/config/active_config.json"
for cfg in "$MODULE_ACTIVE" "$CONTROLLER_ACTIVE"; do
    if [ -f "$cfg" ]; then
        sudo rm -f "$cfg"
        echo "  Removed $cfg"
    fi
done
echo "  Active configs cleared — will be rebuilt from base_config on first run"


# ── 8. Old log and recording data (optional) ─────────────────────────────────
echo "[8/8] Checking for data from source device..."

if [ -d /var/log/saviour ] && [ -n "$(ls -A /var/log/saviour 2>/dev/null)" ]; then
    echo ""
    echo "  /var/log/saviour/ contains logs from the source device."
    read -p "  Clear old log files? (yes/no): " DEL_LOGS
    if [ "$DEL_LOGS" = "yes" ]; then
        sudo rm -rf /var/log/saviour/*
        echo "  Cleared /var/log/saviour/"
    else
        echo "  Kept logs"
    fi
fi

if [ -d /var/lib/saviour/recordings ] && [ -n "$(ls -A /var/lib/saviour/recordings 2>/dev/null)" ]; then
    echo ""
    echo "  !! /var/lib/saviour/recordings/ contains recording data from the source device !!"
    read -p "  Delete these recordings? (yes/no): " DEL_REC
    if [ "$DEL_REC" = "yes" ]; then
        sudo rm -rf /var/lib/saviour/recordings/*
        echo "  Deleted recordings"
    else
        echo "  Kept recordings"
    fi
fi


# ── Reload systemd ────────────────────────────────────────────────────────────
sudo systemctl daemon-reload


echo ""
echo "======================================="
echo " Clone prep complete."
echo "======================================="
echo ""
echo "Next steps:"
echo "  1. Run: sudo ${INSTALL_DIR}/switch_role.sh"
echo "     This assigns the role (controller/module) and configures services."
echo ""
echo "Items left unchanged (intentional):"
echo "  - Python venv (${INSTALL_DIR}/env)"
echo "  - SSH authorised keys (~/.ssh/authorized_keys)"
echo "  - base_config.json — if this device will be a module connecting to the"
echo "    same controller as the source device, the Samba credentials in"
echo "    src/modules/config/base_config.json are still valid."
echo "    If it's a new controller, run switch_role.sh (controller) first — it"
echo "    writes fresh credentials — then switch_role.sh on each module."
echo "  - wlan0 / Wi-Fi configuration"
