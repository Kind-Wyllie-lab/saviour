#!/usr/bin/env bash
# SAVIOUR Uninstall Script
# Reverses the changes made by setup.sh and switch_role.sh.

set -Eeuo pipefail
trap 'rc=$?; echo "uninstall.sh failed at line $LINENO (exit $rc)"' ERR

INSTALL_DIR="/usr/local/src/saviour"
SHARENAME="controller_share"

echo "======================================="
echo " SAVIOUR Uninstaller"
echo "======================================="
echo ""
echo "This will remove SAVIOUR services, configuration, and optionally"
echo "the source directory and recorded data."
echo ""
read -p "Continue? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi


# ── Helpers ──────────────────────────────────────────────────────────────────

get_current_role() {
    CURRENT_ROLE=""
    CURRENT_TYPE=""
    if [ -f /etc/saviour/config ]; then
        source /etc/saviour/config
        CURRENT_ROLE="${ROLE:-}"
        CURRENT_TYPE="${TYPE:-}"
    fi
}

service_exists() {
    systemctl list-unit-files "$1" &>/dev/null && \
        systemctl list-unit-files "$1" | grep -q "$1"
}

stop_disable_remove() {
    local svc="$1"
    if service_exists "$svc"; then
        echo "  Stopping and disabling $svc..."
        sudo systemctl stop    "$svc" 2>/dev/null || true
        sudo systemctl disable "$svc" 2>/dev/null || true
    fi
    local unit_file="/etc/systemd/system/${svc}"
    if [ -f "$unit_file" ]; then
        sudo rm -f "$unit_file"
        echo "  Removed $unit_file"
    fi
}

restore_or_remove() {
    # Restore a config file from its .backup, or remove it if no backup exists.
    local target="$1"
    if [ -f "${target}.backup" ]; then
        sudo cp "${target}.backup" "$target"
        sudo rm -f "${target}.backup"
        echo "  Restored ${target} from backup"
    elif [ -f "$target" ]; then
        sudo rm -f "$target"
        echo "  Removed ${target} (no backup)"
    fi
}


get_current_role
echo "Detected role: ${CURRENT_ROLE:-unknown} / ${CURRENT_TYPE:-unknown}"
echo ""


# ── 1. SAVIOUR service ────────────────────────────────────────────────────────
echo "[1/11] SAVIOUR service"
stop_disable_remove "saviour.service"


# ── 2. PTP services ──────────────────────────────────────────────────────────
echo "[2/11] PTP services (ptp4l, phc2sys)"
stop_disable_remove "ptp4l.service"
stop_disable_remove "phc2sys.service"

# Re-enable timesyncd if it was disabled for a module
if [ "$CURRENT_ROLE" = "module" ]; then
    echo "  Re-enabling systemd-timesyncd (was disabled for module role)"
    sudo systemctl enable systemd-timesyncd 2>/dev/null || true
    sudo systemctl start  systemd-timesyncd 2>/dev/null || true
    sudo timedatectl set-ntp true 2>/dev/null || true
fi


# ── 3. Samba (controller only) ────────────────────────────────────────────────
echo "[3/11] Samba"
if [ "$CURRENT_ROLE" = "controller" ]; then
    echo "  Stopping and disabling smbd / nmbd..."
    sudo systemctl stop    smbd nmbd 2>/dev/null || true
    sudo systemctl disable smbd nmbd 2>/dev/null || true

    restore_or_remove /etc/samba/smb.conf

    # Remove Samba accounts
    if sudo pdbedit -L 2>/dev/null | grep -q "^saviour_module:"; then
        echo "  Removing Samba account: saviour_module"
        sudo smbpasswd -x saviour_module 2>/dev/null || true
    fi
    # Leave pi's Samba password — it pre-existed and the pi user is not ours to remove.

    if [ -d "/home/pi/${SHARENAME}" ]; then
        echo ""
        echo "  /home/pi/${SHARENAME}/ may contain recording data from modules."
        read -p "  Delete /home/pi/${SHARENAME}/ and its contents? (yes/no): " DEL_SHARE
        if [ "$DEL_SHARE" = "yes" ]; then
            sudo rm -rf "/home/pi/${SHARENAME}"
            echo "  Deleted /home/pi/${SHARENAME}/"
        else
            echo "  Kept /home/pi/${SHARENAME}/"
        fi
    fi
else
    echo "  Skipped (not controller)"
fi


# ── 4. DHCP / dnsmasq (controller only) ──────────────────────────────────────
echo "[4/11] DHCP server (dnsmasq)"
if [ "$CURRENT_ROLE" = "controller" ]; then
    sudo systemctl stop    dnsmasq 2>/dev/null || true
    sudo systemctl disable dnsmasq 2>/dev/null || true

    restore_or_remove /etc/dnsmasq.conf

    # Remove the service override drop-in
    if [ -f /etc/systemd/system/dnsmasq.service.d/override.conf ]; then
        sudo rm -f /etc/systemd/system/dnsmasq.service.d/override.conf
        sudo rmdir /etc/systemd/system/dnsmasq.service.d 2>/dev/null || true
        echo "  Removed dnsmasq service override"
    fi

    # Restore original dnsmasq.service if backed up
    restore_or_remove /lib/systemd/system/dnsmasq.service

    # Reset ethernet to DHCP
    echo "  Resetting eth0 IP assignment to automatic (DHCP)..."
    INTERFACE=$(nmcli -t -f GENERAL.CONNECTION device show eth0 2>/dev/null | cut -d: -f2- || true)
    if [ -n "$INTERFACE" ]; then
        sudo nmcli connection modify "$INTERFACE" ipv4.method auto \
            ipv4.addresses "" ipv4.gateway "" ipv4.dns "" 2>/dev/null || true
        echo "  Reset $INTERFACE to auto"
    else
        echo "  Could not detect NetworkManager connection name for eth0 — reset IP manually if needed"
    fi
else
    echo "  Skipped (not controller)"
fi


# ── 5. mDNS / avahi (controller only) ────────────────────────────────────────
echo "[5/11] mDNS (avahi)"
if [ "$CURRENT_ROLE" = "controller" ]; then
    sudo systemctl stop    avahi-daemon 2>/dev/null || true
    sudo systemctl disable avahi-daemon 2>/dev/null || true

    # Remove our custom avahi config (no backup was taken in setup)
    if [ -f /etc/avahi/avahi-daemon.conf ]; then
        sudo rm -f /etc/avahi/avahi-daemon.conf
        echo "  Removed /etc/avahi/avahi-daemon.conf"
    fi

    # Remove iptables port-forward rule (80 → 5000)
    if sudo iptables -t nat -C PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000 2>/dev/null; then
        sudo iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000
        sudo netfilter-persistent save 2>/dev/null || true
        echo "  Removed iptables port-forward 80 → 5000"
    fi
else
    echo "  Skipped (not controller)"
fi


# ── 6. Microphone / PipeWire (microphone module only) ─────────────────────────
echo "[6/11] PipeWire (microphone)"
if [ "$CURRENT_TYPE" = "microphone" ]; then
    if [ -f /etc/pipewire/pipewire.conf.d/99-sample-rates.conf ]; then
        sudo rm -f /etc/pipewire/pipewire.conf.d/99-sample-rates.conf
        echo "  Removed 99-sample-rates.conf"
    fi
    systemctl --user disable pipewire      2>/dev/null || true
    systemctl --user disable pipewire-pulse 2>/dev/null || true
    systemctl --user disable wireplumber   2>/dev/null || true
    echo "  Disabled user pipewire/wireplumber services"
else
    echo "  Skipped (not microphone)"
fi


# ── 7. Linux user / group (controller only) ──────────────────────────────────
echo "[7/11] saviour_module user and saviour group"
if [ "$CURRENT_ROLE" = "controller" ]; then
    if id saviour_module &>/dev/null; then
        sudo userdel saviour_module
        echo "  Deleted user: saviour_module"
    fi
    if getent group saviour &>/dev/null; then
        sudo groupdel saviour
        echo "  Deleted group: saviour"
    fi
    # Restore /home/pi permissions to default (755)
    sudo chmod 755 /home/pi
    echo "  Restored /home/pi to 755"
else
    echo "  Skipped (not controller)"
fi


# ── 8. journald config ────────────────────────────────────────────────────────
echo "[8/11] journald config"
# setup.sh set Storage=persistent with no backup — restore to default (auto)
if grep -q "Storage=persistent" /etc/systemd/journald.conf 2>/dev/null; then
    sudo tee /etc/systemd/journald.conf > /dev/null <<'EOF'
[Journal]
EOF
    echo "  Restored journald.conf to defaults"
fi


# ── 9. timesyncd config ───────────────────────────────────────────────────────
echo "[9/11] timesyncd config"
restore_or_remove /etc/systemd/timesyncd.conf


# ── 10. /etc/saviour ─────────────────────────────────────────────────────────
echo "[10/11] /etc/saviour"
if [ -d /etc/saviour ]; then
    sudo rm -rf /etc/saviour
    echo "  Removed /etc/saviour"
fi


# ── 11. Recordings and source directory ───────────────────────────────────────
echo "[11/11] Data and source"

# Recording data — warn loudly
if [ -d /var/lib/saviour ]; then
    echo ""
    echo "  !! /var/lib/saviour contains recording data !!"
    read -p "  Delete /var/lib/saviour and ALL recordings? (yes/no): " DEL_DATA
    if [ "$DEL_DATA" = "yes" ]; then
        sudo rm -rf /var/lib/saviour
        echo "  Deleted /var/lib/saviour"
    else
        echo "  Kept /var/lib/saviour"
    fi
fi

# Python venv
if [ -d "${INSTALL_DIR}/env" ]; then
    read -p "  Delete Python venv (${INSTALL_DIR}/env)? (yes/no): " DEL_VENV
    if [ "$DEL_VENV" = "yes" ]; then
        sudo rm -rf "${INSTALL_DIR}/env"
        echo "  Deleted venv"
    fi
fi

# Source directory
echo ""
echo "  The source directory ${INSTALL_DIR} contains the SAVIOUR codebase."
read -p "  Delete ${INSTALL_DIR}? (yes/no): " DEL_SRC
if [ "$DEL_SRC" = "yes" ]; then
    sudo rm -rf "${INSTALL_DIR}"
    echo "  Deleted ${INSTALL_DIR}"
else
    # Clear generated Samba credentials from base_config.json if source dir is kept
    BASE_CONFIG="${INSTALL_DIR}/src/modules/config/base_config.json"
    if [ -f "$BASE_CONFIG" ]; then
        python3 - "$BASE_CONFIG" <<'PYEOF' 2>/dev/null && \
            echo "  Cleared Samba credentials from base_config.json" || \
            echo "  Could not clear base_config.json credentials — edit manually"
import sys, json
path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)
export = cfg.get("export", {})
export.pop("share_username", None)
export.pop("share_password", None)
if not export:
    cfg.pop("export", None)
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PYEOF
    fi
fi

# Remove git safe.directory entry added by setup.sh
if git config --global --get safe.directory "${INSTALL_DIR}" &>/dev/null; then
    git config --global --unset safe.directory "${INSTALL_DIR}" 2>/dev/null || true
    echo "  Removed git safe.directory entry for ${INSTALL_DIR}"
fi


# ── Final reload ──────────────────────────────────────────────────────────────
sudo systemctl daemon-reload

echo ""
echo "======================================="
echo " SAVIOUR uninstall complete."
echo "======================================="
echo ""
echo "Not removed (manual cleanup if needed):"
echo "  - System packages (linuxptp, samba, dnsmasq, ffmpeg, avahi-daemon, etc.)"
echo "    Remove with: sudo apt-get remove <package>"
echo "  - Hostname (currently $(hostname)) — change with: sudo hostnamectl set-hostname <name>"
echo "  - /etc/cloud/cloud-init.disabled — if present, cloud-init is disabled; re-enable with: sudo rm /etc/cloud/cloud-init.disabled"
if [ "$CURRENT_ROLE" = "controller" ]; then
echo "  - pi Samba password (unchanged)"
echo "  - nvm / Node.js (~/.nvm) — remove with: rm -rf ~/.nvm"
fi
