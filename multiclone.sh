#!/bin/bash
set -euo pipefail

IMAGE="$1"
shift
DEVICES=("$@")

if [ -z "$IMAGE" ] || [ ${#DEVICES[@]} -eq 0 ]; then
  echo "Usage: $0 <image.img> <device1> [device2] [device3] ..."
  echo "Example: $0 /mnt/export/saviour-image.img sda sdb sdc sdd"
  exit 1
fi

echo "=== Target devices: ${DEVICES[*]} ==="
echo "=== Source image: $IMAGE ==="
lsblk
echo
read -p "Confirm these are correct, blank, intended target devices? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
  echo "Aborted."
  exit 1
fi

# Safety: refuse to touch the running root device
for d in "${DEVICES[@]}"; do
  if [[ "$d" == mmcblk0* ]]; then
    echo "ERROR: refusing to write to $d (looks like the running system disk)"
    exit 1
  fi
done

# Build dcfldd of= arguments
OF_ARGS=()
for d in "${DEVICES[@]}"; do
  OF_ARGS+=("of=/dev/$d")
done

echo "=== Writing image to all targets simultaneously ==="
sudo dcfldd if="$IMAGE" bs=4M "${OF_ARGS[@]}"
sync

echo "=== Write complete. Re-reading partition tables ==="
for d in "${DEVICES[@]}"; do
  sudo partprobe "/dev/$d"
done
sleep 2   # let udev settle

# Per-device identity correction
for dev in "${DEVICES[@]}"; do
  echo "=== Fixing identity on /dev/$dev ==="

  sudo mkdir -p /mnt/card-check
  sudo mount "/dev/${dev}1" /mnt/card-check
  oldpartuuid=$(grep -oP 'root=PARTUUID=\K[a-f0-9]{8}-[a-f0-9]{2}' /mnt/card-check/cmdline.txt || true)
  if [ -z "$oldpartuuid" ]; then
    echo "ERROR: could not find PARTUUID in cmdline.txt on /dev/${dev}1, skipping $dev"
    sudo umount /mnt/card-check
    continue
  fi
  echo "Found old PARTUUID: $oldpartuuid"
  sudo umount /mnt/card-check

  newid_hex=$(openssl rand -hex 4)
  newid_dec=$((16#$newid_hex))
  sudo sfdisk --disk-id "/dev/$dev" "$newid_dec"
  sudo partprobe "/dev/$dev"
  sleep 1

  sudo mount "/dev/${dev}2" /mnt/card-check
  sudo tune2fs -U random "/dev/${dev}2"
  sudo sed -i -E "s/PARTUUID=[A-Za-z0-9]{8}-01/PARTUUID=${newid_hex}-01/" /mnt/card-check/etc/fstab
  sudo sed -i -E "s/PARTUUID=[A-Za-z0-9]{8}-02/PARTUUID=${newid_hex}-02/" /mnt/card-check/etc/fstab
  sudo truncate -s 0 /mnt/card-check/etc/machine-id
  sudo rm -f /mnt/card-check/etc/ssh/ssh_host_*
  sudo rm -f /mnt/card-check/var/lib/dhcpcd/duid
  sudo umount /mnt/card-check

  sudo mount "/dev/${dev}1" /mnt/card-check
  sudo sed -i -E "s/PARTUUID=[A-Za-z0-9]{8}-02/PARTUUID=${newid_hex}-02/" /mnt/card-check/cmdline.txt
  sudo umount /mnt/card-check

  echo "=== /dev/$dev done: new PARTUUID ${newid_hex}-01 / ${newid_hex}-02 ==="
done

echo "=== All devices processed. Verifying ==="
for dev in "${DEVICES[@]}"; do
  echo "--- /dev/$dev ---"
  sudo blkid "/dev/${dev}1" "/dev/${dev}2"
done

echo "=== Done. Boot-test at least one card before deploying the rest. ==="