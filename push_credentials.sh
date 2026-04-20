#!/usr/bin/env bash
# SAVIOUR Push Credentials Script
# Run this on the controller after switch_role.sh to update a module's
# Samba credentials and controller IP in its base_config.json.
#
# Usage:
#   ./push_credentials.sh <module_ip> [<module_ip> ...]
#
# Examples:
#   ./push_credentials.sh 10.0.3.10
#   ./push_credentials.sh 10.0.3.10 10.0.3.11 10.0.3.12

set -Eeuo pipefail
trap 'rc=$?; echo "push_credentials.sh failed at line $LINENO (exit $rc)"' ERR

INSTALL_DIR="/usr/local/src/saviour"
CREDS_FILE="/etc/saviour/samba_credentials"
BASE_CONFIG="${INSTALL_DIR}/src/modules/config/base_config.json"
SSH_USER="pi"

if [ "$#" -eq 0 ]; then
    echo "Usage: $0 <module_ip> [<module_ip> ...]"
    exit 1
fi

# ── Read controller credentials ───────────────────────────────────────────────
if [ ! -f "$CREDS_FILE" ]; then
    echo "ERROR: $CREDS_FILE not found — has switch_role.sh been run on this controller?"
    exit 1
fi

CONTROLLER_IP=$(nmcli -g IP4.ADDRESS device show eth0 2>/dev/null | cut -d/ -f1)
if [ -z "$CONTROLLER_IP" ]; then
    echo "ERROR: Could not determine controller IP from eth0"
    exit 1
fi

MODULE_USER=$(grep '^username=' "$CREDS_FILE" | cut -d= -f2)
MODULE_PASS=$(grep '^password=' "$CREDS_FILE" | cut -d= -f2)

echo "Controller IP : $CONTROLLER_IP"
echo "Samba user    : $MODULE_USER"
echo "Samba password: (read from $CREDS_FILE)"
echo ""

# ── Push to each module ───────────────────────────────────────────────────────
for MODULE_IP in "$@"; do
    echo "── Pushing to $MODULE_IP ──────────────────────────────────────────"
    ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
        "${SSH_USER}@${MODULE_IP}" \
        "python3 - '${BASE_CONFIG}' '${CONTROLLER_IP}' '${MODULE_USER}' '${MODULE_PASS}'" <<'PYEOF'
import sys, json
path, controller_ip, username, password = sys.argv[1:]
with open(path) as f:
    cfg = json.load(f)
export = cfg.setdefault("export", {})
export["share_ip"] = controller_ip
export["share_username"] = username
export["share_password"] = password
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print(f"  Updated {path}")
print(f"  share_ip       = {controller_ip}")
print(f"  share_username = {username}")
PYEOF
    echo "  Done."
    echo ""
done

echo "======================================="
echo " Credentials pushed to all modules."
echo "======================================="
echo ""
echo "Each module's saviour.service will pick up the new credentials"
echo "on next recording export. No restart required."
