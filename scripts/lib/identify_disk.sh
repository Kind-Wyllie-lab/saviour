#!/bin/bash
# Shared helper: identify_disk <devname> [partition-suffix]
#
# Prints a short human-readable identification for a block device, sourced
# by clone_direct.sh, capture_master_image.sh and multiclone.sh so their
# device pickers show which physical card is which -- lsblk alone can't
# tell two identical SanDisk cards apart, but the card itself knows its own
# hostname, role/type and SAVIOUR version once you look inside it.
#
# Briefly mounts the device's root partition read-only, reads
# /etc/hostname, /etc/saviour/config and the installed __version__.py, then
# unmounts. Never writes anything. Safe to call on a device that isn't a
# Raspberry Pi OS card at all -- it just falls through to a generic label.
#
# Usage: source scripts/lib/identify_disk.sh
#        identify_disk sdb        # assumes partition 2 is root (the norm)
#        identify_disk sdb 2      # explicit partition number

identify_disk() {
  local dev="$1"
  local part="${2:-2}"
  local devpart="/dev/${dev}${part}"

  [ -b "$devpart" ] || { echo "no partition ${part} -- not a Raspberry Pi OS card"; return; }

  local mnt desc
  mnt=$(mktemp -d "/tmp/ident-${dev}-XXXX") || { echo "(could not create mount point)"; return; }
  desc="Raspberry Pi OS card, no SAVIOUR install found"

  if sudo mount -o ro "$devpart" "$mnt" 2>/dev/null; then
    local hn role type ver
    hn=$(sudo cat "$mnt/etc/hostname" 2>/dev/null | tr -d '[:space:]')
    if [ -f "$mnt/etc/saviour/config" ]; then
      role=$(sudo grep '^ROLE=' "$mnt/etc/saviour/config" 2>/dev/null | cut -d= -f2)
      type=$(sudo grep '^TYPE=' "$mnt/etc/saviour/config" 2>/dev/null | cut -d= -f2)
    fi
    ver=$(sudo grep -oP '(?<=__version__ = ")[^"]+' "$mnt/usr/local/src/saviour/src/__version__.py" 2>/dev/null)

    if [ -n "$hn" ] && [ "$hn" != "saviour-unconfigured" ] && [ "$hn" != "localhost" ] && [[ "$hn" != saviour-unprov-* ]]; then
      desc="$hn"
      if [ -n "$role" ] && [ "$role" != "none" ]; then
        desc+=" (${type:-?} ${role}"
        [ -n "$ver" ] && desc+=", $ver"
        desc+=")"
      elif [ -n "$ver" ]; then
        desc+=" ($ver)"
      fi
    elif [ -n "$ver" ]; then
      desc="unconfigured SAVIOUR install ($ver)"
    fi
    sudo umount "$mnt"
  else
    desc="not a Raspberry Pi OS card (partition ${part} won't mount)"
  fi
  rmdir "$mnt" 2>/dev/null || true
  echo "$desc"
}
