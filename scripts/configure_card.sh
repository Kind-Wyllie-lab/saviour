#!/bin/bash
# SAVIOUR SD Card Configurator
#
# Declares a SAVIOUR role/type on one or more SD cards connected to this
# (Linux) host over a USB reader -- no monitor, keyboard or SSH on the target
# Pi needed. Typical use: straight after multiclone.sh / clone_direct.sh, on
# the same host, with the cards still in the hub.
#
# Writes /etc/saviour/config on each card's root (ext4) partition. Nothing is
# installed or configured here: on the card's next boot,
# saviour-provision.service runs `saviour-config --apply`, which sees the
# declared role/type differs from /etc/saviour/.provisioned and runs the full
# configuration (MAC-derived hostname, PTP role, systemd unit, variant apt
# deps, ...) non-interactively. Progress lands in /var/log/saviour-config.log
# and `journalctl -u saviour-provision` on the device. Allow a few minutes;
# a variant whose APT_PACKAGES aren't already in the image needs internet on
# that first boot.
#
# By default it also deletes /etc/saviour/.provisioned, forcing that full run
# even when the declared role/type matches what the master image was
# provisioned as -- without it, a clone of a camera master declared "camera"
# is a no-op on boot and keeps multiclone's temporary saviour-unprov-XXXX
# hostname and the master's active_config.json.
#
# Windows can't do this: it only sees the FAT boot partition, and
# /etc/saviour/config lives on the ext4 root.
#
# Usage: sudo scripts/configure_card.sh                            (interactive TUI)
#        sudo scripts/configure_card.sh [--keep-provisioned] <role> <type> <device1> [device2] ...
# Example: sudo scripts/configure_card.sh module camera sda sdb sdc sdd
#
# Controller network settings (GATEWAY_MODE etc.) are only asked for in the
# TUI; a controller configured via the scriptable form gets saviour-config's
# own defaults (offline, 10.0.0.1/16).

set -uo pipefail

INSTALL_DIR="/usr/local/src/saviour"

ROOT_DEV=$(findmnt -n -o SOURCE / | sed -E 's/p?[0-9]+$//')
ROOT_DISK=$(basename "$(readlink -f "$ROOT_DEV")")

if [ "$EUID" -ne 0 ]; then
  echo "Must be run as root:  sudo $0" >&2
  exit 1
fi

GATEWAY_MODE="" GATEWAY="" WAN_INTERFACE="" DEVICE_IP=""

# /dev/sda -> /dev/sda2, /dev/mmcblk1 -> /dev/mmcblk1p2
part_path() {
  if [[ "$1" =~ [0-9]$ ]]; then echo "/dev/${1}p${2}"; else echo "/dev/${1}${2}"; fi
}

read_value() {
  [ -f "$1" ] || return 0
  grep -E "^${2}=" "$1" | tail -n1 | cut -d= -f2-
}

read_variant_value() {
  local v
  v=$(read_value "$1" "$2")
  v="${v%\"}"
  echo "${v#\"}"
}

# A SAVIOUR card: vfat partition 1, ext4 partition 2, and a SAVIOUR install
# on the latter (checked once mounted).
looks_like_pi_card() {
  [ "$(blkid -s TYPE -o value "$(part_path "$1" 1)" 2>/dev/null)" = "vfat" ] &&
    [ "$(blkid -s TYPE -o value "$(part_path "$1" 2)" 2>/dev/null)" = "ext4" ]
}

# Mount point of the card currently being worked on; cleared by unmount_card.
CARD_MNT=""
mount_card() {
  CARD_MNT=$(mktemp -d "/tmp/cfgcard-${1}-XXXX") || return 1
  if ! mount "$(part_path "$1" 2)" "$CARD_MNT" 2>/dev/null; then
    rmdir "$CARD_MNT"; CARD_MNT=""
    return 1
  fi
}
unmount_card() {
  [ -n "$CARD_MNT" ] || return 0
  sync
  umount "$CARD_MNT" 2>/dev/null || umount -l "$CARD_MNT" 2>/dev/null || true
  rmdir "$CARD_MNT" 2>/dev/null || true
  CARD_MNT=""
}
trap unmount_card EXIT

variant_dir() {  # <mnt> <role> <type>
  if [ "$2" = "controller" ]; then
    echo "$1$INSTALL_DIR/src/controller/variants/$3"
  else
    echo "$1$INSTALL_DIR/src/modules/variants/$3"
  fi
}

# Writes the config onto one card. Same key set/order as saviour-config's
# write_config(), preserving a hand-added FIREWALL_WLAN_SSH break-glass.
# Prints a one-line result.
write_card() {  # <dev> <role> <type> <force yes|no>
  local dev="$1" role="$2" type="$3" force="$4" cfg fw warn=""

  if [ "$dev" = "$ROOT_DISK" ] || [[ "$dev" == mmcblk0* ]]; then
    echo "/dev/$dev: SKIPPED -- looks like the running system disk"
    return 1
  fi
  if ! looks_like_pi_card "$dev" || ! mount_card "$dev"; then
    echo "/dev/$dev: SKIPPED -- not a Raspberry Pi OS card (or root partition won't mount)"
    return 1
  fi
  if [ ! -f "$(variant_dir "$CARD_MNT" "$role" "$type")/variant.conf" ]; then
    echo "/dev/$dev: SKIPPED -- no SAVIOUR install, or its code has no $role type '$type'"
    unmount_card
    return 1
  fi

  cfg="$CARD_MNT/etc/saviour/config"
  fw=$(read_value "$cfg" FIREWALL_WLAN_SSH)
  mkdir -p "$CARD_MNT/etc/saviour"
  cat > "$cfg" <<EOF
ROLE=${role}
TYPE=${type}
GATEWAY_MODE=${GATEWAY_MODE}
GATEWAY=${GATEWAY}
WAN_INTERFACE=${WAN_INTERFACE}
DEVICE_IP=${DEVICE_IP}
EOF
  if [ -n "$fw" ]; then echo "FIREWALL_WLAN_SSH=${fw}" >> "$cfg"; fi
  chmod 644 "$cfg"

  if [ "$force" = "yes" ]; then rm -f "$CARD_MNT/etc/saviour/.provisioned"; fi

  # setup.sh enables it with WantedBy=multi-user.target; an image built
  # before boot-time provisioning existed has no such link and won't act on
  # the config file at all.
  if [ ! -e "$CARD_MNT/etc/systemd/system/multi-user.target.wants/saviour-provision.service" ]; then
    warn=" -- WARNING: saviour-provision.service not enabled on this card, it will NOT self-configure (run 'sudo saviour-config' on the Pi instead)"
  fi
  unmount_card
  echo "/dev/$dev: set to ${type} ${role}${warn}"
}

if [ "$#" -gt 0 ]; then
  # ── Scriptable path ────────────────────────────────────────────────────────
  FORCE="yes"
  if [ "${1:-}" = "--keep-provisioned" ]; then FORCE="no"; shift; fi
  if [ "$#" -lt 3 ]; then
    echo "Usage: $0 [--keep-provisioned] <module|controller> <type> <device1> [device2] ..."
    echo "Example: $0 module camera sda sdb sdc sdd"
    echo "(or run with no arguments for the interactive TUI)"
    exit 1
  fi
  ROLE="$1" TYPE="$2"
  shift 2
  if [ "$ROLE" != "module" ] && [ "$ROLE" != "controller" ]; then
    echo "ERROR: role must be 'module' or 'controller', got '$ROLE'"
    exit 1
  fi
  if [ "$ROLE" = "controller" ] && [ "$#" -gt 1 ]; then
    echo "ERROR: only one card at a time can be a controller (they'd all claim the same static IP)"
    exit 1
  fi

  fail=0
  for d in "$@"; do
    write_card "$d" "$ROLE" "$TYPE" "$FORCE" || fail=1
  done
  exit "$fail"
fi

# ── Interactive path ─────────────────────────────────────────────────────────
if [ ! -t 0 ]; then
  echo "ERROR: no arguments given and this isn't an interactive terminal."
  echo "Usage: $0 [--keep-provisioned] <module|controller> <type> <device1> [device2] ..."
  exit 1
fi

if ! command -v whiptail &>/dev/null; then
  echo "whiptail not found -- installing..."
  apt-get install -y whiptail
fi

source "$(dirname "$(readlink -f "$0")")/lib/identify_disk.sh"

W=78
H=20
wt() { whiptail "$@" 3>&1 1>&2 2>&3; }

echo "Identifying connected cards (mounting each briefly, read-only)..."
candidates=()
while IFS= read -r line; do
  NAME="" SIZE="" MODEL="" TRAN="" TYPE=""
  eval "$line"
  [ "$TYPE" = "disk" ] || continue
  [ "$NAME" = "$ROOT_DISK" ] && continue
  [[ "$NAME" == mmcblk0* ]] && continue
  looks_like_pi_card "$NAME" || continue
  id=$(identify_disk "$NAME")
  echo "  /dev/$NAME: $id"
  candidates+=("$NAME" "${SIZE:-?} -- ${id}" "OFF")
done < <(lsblk -dn -P -o NAME,SIZE,MODEL,TRAN,TYPE)

if [ ${#candidates[@]} -eq 0 ]; then
  whiptail --title "No Cards Found" \
    --msgbox "\nNo Raspberry Pi OS cards found (other than the running system disk).\n\nCheck the SD card readers are plugged in, then re-run." 12 $W
  exit 1
fi

sel_raw=$(wt --title "Select Cards" --checklist \
  "\nWhich cards should be configured?\nUse space to select, enter to confirm.\n" \
  $H $W $((${#candidates[@]} / 3)) \
  "${candidates[@]}") || { echo "Aborted."; exit 1; }
if [ -z "$sel_raw" ]; then
  whiptail --title "No Selection" --msgbox "\nNo cards selected." 8 $W
  exit 1
fi
DEVICES=()
eval "DEVICES=($sel_raw)"

ROLE=$(wt --title "Device Role" --menu "\nWhat role should the selected card(s) play?" \
  $H $W 2 \
  "module"     "Peripheral device -- records data, reports to controller" \
  "controller" "Central device -- coordinates modules and presents the GUI") || { echo "Aborted."; exit 1; }

if [ "$ROLE" = "controller" ] && [ ${#DEVICES[@]} -gt 1 ]; then
  whiptail --title "One Controller Only" \
    --msgbox "\nOnly one card at a time can be configured as a controller (they'd all claim the same static IP)." 10 $W
  exit 1
fi

# The type menu is built from the first selected card's own variant.conf
# files, not this host's checkout -- the card's code is what saviour-config
# will actually load at boot. write_card re-checks the type exists on every
# other card.
if ! mount_card "${DEVICES[0]}"; then
  whiptail --title "Mount Failed" --msgbox "\nCould not mount the root partition of /dev/${DEVICES[0]}." 8 $W
  exit 1
fi
type_args=()
for conf in "$(variant_dir "$CARD_MNT" "$ROLE" "")"*/variant.conf; do
  [ -f "$conf" ] || continue
  name=$(read_variant_value "$conf" NAME)
  desc=$(read_variant_value "$conf" DESCRIPTION)
  type_args+=("$(basename "$(dirname "$conf")")" "${desc:-$name}")
done
unmount_card
if [ ${#type_args[@]} -eq 0 ]; then
  whiptail --title "No SAVIOUR Install" \
    --msgbox "\n/dev/${DEVICES[0]} has no SAVIOUR install (no $ROLE variant.conf files under $INSTALL_DIR)." 10 $W
  exit 1
fi
TYPE=$(wt --title "Type" --menu "\nWhich $ROLE type?" \
  $H $W $((${#type_args[@]} / 2)) "${type_args[@]}") || { echo "Aborted."; exit 1; }

if [ "$ROLE" = "controller" ]; then
  GATEWAY_MODE=$(wt --title "Internet Gateway" \
    --menu "\nHow does the PoE network reach the internet?" $H $W 3 \
    "none"       "Offline -- no internet access for modules" \
    "external"   "External router at a known IP" \
    "controller" "Controller shares internet from wlan0 / another interface") || { echo "Aborted."; exit 1; }
  case "$GATEWAY_MODE" in
    external)
      GATEWAY=$(wt --title "Gateway IP" \
        --inputbox "\nExternal router's IP address:" 9 $W "192.168.1.1") || { echo "Aborted."; exit 1; } ;;
    controller)
      WAN_INTERFACE=$(wt --title "WAN Interface" \
        --inputbox "\nInternet-facing interface (e.g. wlan0, eth1):" 9 $W "wlan0") || { echo "Aborted."; exit 1; } ;;
  esac
  a=10 b=0 c=0
  if [ -n "$GATEWAY" ]; then IFS='.' read -r a b c _ <<< "$GATEWAY"; fi
  DEVICE_IP=$(wt --title "Controller IP" \
    --inputbox "\nStatic IP for the controller's PoE interface (x.x.x.x/prefix):" \
    9 $W "$a.$b.$c.1/16") || { echo "Aborted."; exit 1; }
fi

FORCE="yes"
if ! whiptail --title "Full Re-provision" --yesno \
  "\nForce a full re-provision on first boot?\n\nRecommended for freshly cloned cards: deletes /etc/saviour/.provisioned so each card gets its own MAC-derived hostname and a fresh active_config.json, even if the master image was already provisioned as this role/type.\n\nChoose No only to keep a card's existing tuned config." \
  16 $W; then
  FORCE="no"
fi

summary="Cards: ${DEVICES[*]}\nRole:  $ROLE\nType:  $TYPE\n"
if [ "$ROLE" = "controller" ]; then
  summary+="Gateway: $GATEWAY_MODE ${GATEWAY}${WAN_INTERFACE}\nIP:    $DEVICE_IP\n"
fi
summary+="Full re-provision: $FORCE\n\nWrite this to the card(s)?"
if ! whiptail --title "Confirm" --yesno "\n$summary" 16 $W --yes-button "Write" --no-button "Cancel"; then
  echo "Aborted."
  exit 1
fi

results=""
fail=0
for d in "${DEVICES[@]}"; do
  line=$(write_card "$d" "$ROLE" "$TYPE" "$FORCE") || fail=1
  echo "$line"
  results+="$line\n"
done

whiptail --title "Done" --msgbox \
  "\n${results}\nCards unmounted -- safe to remove. Each configures itself on first boot (allow a few minutes; progress in /var/log/saviour-config.log on the device)." \
  $H $W
exit "$fail"
