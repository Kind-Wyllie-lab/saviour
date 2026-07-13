#!/bin/bash
# SAVIOUR SSH/Hostname Repair Script
#
# Repairs SD cards flashed with a pre-fix multiclone.sh: missing SSH host
# keys (sshd exits on boot -> connection refused) and an empty/missing
# hostname (shows as "*" in DHCP leases instead of a real name). Safe to
# run without reflashing -- mounts partition 2 of each device, regenerates
# keys and sets a temporary hostname if needed, unmounts. Only touches
# /etc/ssh and /etc/hostname/hosts; no data loss.
#
# Usage: sudo ./fix_ssh_and_hostname.sh <device1> [device2] ...
# Example: sudo ./fix_ssh_and_hostname.sh sda sdb sdc sdd

set -euo pipefail

DEVICES=("$@")
if [ ${#DEVICES[@]} -eq 0 ]; then
  echo "Usage: $0 <device1> [device2] ..."
  echo "Example: $0 sda sdb sdc sdd"
  exit 1
fi

# Safety: refuse to touch the running root device
for d in "${DEVICES[@]}"; do
  if [[ "$d" == mmcblk0* ]]; then
    echo "ERROR: refusing to touch $d (looks like the running system disk)"
    exit 1
  fi
done

fix_one() {
  local dev="$1"
  local mnt="/mnt/card-fix-$dev"
  echo "=== /dev/$dev ==="

  sudo mkdir -p "$mnt"
  sudo mount "/dev/${dev}2" "$mnt"

  echo "Regenerating SSH host keys..."
  sudo rm -f "$mnt"/etc/ssh/ssh_host_*
  sudo ssh-keygen -A -f "$mnt"

  local current_hn
  current_hn=$(sudo cat "$mnt/etc/hostname" 2>/dev/null | tr -d '[:space:]' || true)
  if [ -z "$current_hn" ] || [ "$current_hn" = "localhost" ] || [ "$current_hn" = "raspberrypi" ]; then
    local suffix new_hn
    suffix=$(openssl rand -hex 2)
    new_hn="saviour-unprov-${suffix}"
    echo "Setting hostname: $new_hn (was: '${current_hn:-empty}')"
    echo "$new_hn" | sudo tee "$mnt/etc/hostname" > /dev/null
    if sudo grep -q "^127\.0\.1\.1" "$mnt/etc/hosts"; then
      sudo sed -i -E "s/^127\.0\.1\.1.*/127.0.1.1\t${new_hn}/" "$mnt/etc/hosts"
    else
      echo -e "127.0.1.1\t${new_hn}" | sudo tee -a "$mnt/etc/hosts" > /dev/null
    fi
  else
    echo "Hostname already set to '$current_hn', leaving as-is"
  fi

  local key_count
  key_count=$(sudo find "$mnt/etc/ssh" -maxdepth 1 -name 'ssh_host_*_key' 2>/dev/null | wc -l)
  echo "SSH host keys: $key_count"
  if [ "$key_count" -eq 0 ]; then
    echo "WARNING: no SSH host keys after regeneration on /dev/$dev!" >&2
  fi

  sudo umount "$mnt"
  echo "=== /dev/$dev done ==="
}

for dev in "${DEVICES[@]}"; do
  fix_one "$dev"
done

echo "=== All devices fixed. Safe to boot. ==="
