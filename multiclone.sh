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

# Unmount any pre-existing filesystems on target devices. Factory-blank
# SDXC cards ship pre-formatted (usually exFAT) and get auto-mounted by
# udisks2 on insertion. Writing under a mounted partition still succeeds
# (dd writes the raw device), but the kernel then can't re-read the new
# partition table while the stale one is in use -- partprobe fails, and
# under set -e the whole run aborts before the identity-fix step, leaving
# every target device with identical PARTUUID/machine-id/ssh host keys.
echo "=== Unmounting any existing filesystems on target devices ==="
for d in "${DEVICES[@]}"; do
  for part in /dev/"$d"*; do
    [ -e "$part" ] || continue
    mnt=$(findmnt -n -o TARGET "$part" 2>/dev/null || true)
    if [ -n "$mnt" ]; then
      echo "  Unmounting $part (was mounted at $mnt)"
      if ! sudo umount "$part" 2>/dev/null && ! sudo umount -l "$part" 2>/dev/null; then
        echo "ERROR: could not unmount $part -- refusing to write over a mounted filesystem"
        exit 1
      fi
    fi
  done
done

# Safety: refuse a source image that doesn't look like a Raspberry Pi OS
# image (vfat boot + ext4 root). Catches the mistake of pointing this at
# a blank/factory-formatted card or some other unrelated .img -- cheaply,
# before burning hours writing it to every target device.
echo "=== Validating source image ==="
VALIDATE_LOOPDEV=$(sudo losetup -fP --show "$IMAGE")
BOOT_FSTYPE=$(sudo blkid -s TYPE -o value "${VALIDATE_LOOPDEV}p1" 2>/dev/null || true)
ROOT_FSTYPE=$(sudo blkid -s TYPE -o value "${VALIDATE_LOOPDEV}p2" 2>/dev/null || true)
sudo losetup -d "$VALIDATE_LOOPDEV"
if [ "$BOOT_FSTYPE" != "vfat" ] || [ "$ROOT_FSTYPE" != "ext4" ]; then
  echo "ERROR: $IMAGE does not look like a Raspberry Pi OS image."
  echo "  Expected: partition 1 = vfat (boot), partition 2 = ext4 (root)"
  echo "  Found:    partition 1 = ${BOOT_FSTYPE:-<none>}, partition 2 = ${ROOT_FSTYPE:-<none>}"
  exit 1
fi
echo "OK -- partition 1 = vfat (boot), partition 2 = ext4 (root)"

LOGDIR=$(mktemp -d /tmp/multiclone.XXXXXX)
echo "=== Writing image to all targets in parallel (logs: $LOGDIR) ==="
# dcfldd's multi-of= writes to each device sequentially per block (one
# write() at a time), so total time is the SUM of every card's write
# time rather than the max. Separate dd processes let the kernel keep
# other cards' writes in flight while one card is busy acknowledging a
# block internally -- still capped by the shared hub uplink, but it
# closes the dead-time gap dcfldd's blocking round-robin leaves behind.
pids=()
for d in "${DEVICES[@]}"; do
  sudo dd if="$IMAGE" of="/dev/$d" bs=4M conv=fsync status=progress \
    > "$LOGDIR/$d.log" 2>&1 &
  pids+=("$!")
done

fail=0
for i in "${!pids[@]}"; do
  if ! wait "${pids[$i]}"; then
    echo "ERROR: write to /dev/${DEVICES[$i]} failed -- see $LOGDIR/${DEVICES[$i]}.log"
    fail=1
  fi
done
if [ "$fail" -ne 0 ]; then
  exit 1
fi
sync

echo "=== Write complete. Re-reading partition tables ==="
for d in "${DEVICES[@]}"; do
  # Non-fatal: a single device's kernel failing to pick up the new
  # partition table (e.g. something else re-mounted it) shouldn't abort
  # the whole run and skip the identity-fix step for every other device.
  # fix_identity mounts each partition directly and will fail cleanly,
  # per-device, if the kernel's view is still stale.
  sudo partprobe "/dev/$d" || echo "WARNING: partprobe failed for /dev/$d -- will retry via identity-fix step"
done
sleep 2   # let udev settle

# Per-device identity correction. This step is metadata/latency bound
# (mount, sfdisk, small sed edits), not throughput bound, so it benefits
# from parallelism independently of the shared USB hub bandwidth cap on
# the imaging step above. Each device gets its own mount point so the
# parallel jobs don't collide.
fix_identity() {
  local dev="$1"
  local mnt="/mnt/card-check-$dev"
  echo "=== Fixing identity on /dev/$dev ==="

  sudo mkdir -p "$mnt"
  sudo mount "/dev/${dev}1" "$mnt"
  local oldpartuuid
  oldpartuuid=$(grep -oP 'root=PARTUUID=\K[a-f0-9]{8}-[a-f0-9]{2}' "$mnt/cmdline.txt" || true)
  if [ -z "$oldpartuuid" ]; then
    echo "ERROR: could not find PARTUUID in cmdline.txt on /dev/${dev}1, skipping $dev"
    sudo umount "$mnt"
    return 1
  fi
  echo "Found old PARTUUID: $oldpartuuid"
  sudo umount "$mnt"

  local newid_hex newid_dec
  newid_hex=$(openssl rand -hex 4)
  newid_dec=$((16#$newid_hex))
  sudo sfdisk --disk-id "/dev/$dev" "$newid_dec"
  sudo partprobe "/dev/$dev"
  sleep 1

  # The master image is shrunk to its actual used size, so on a full-size
  # card the root partition only covers a fraction of the disk. Grow the
  # partition table entry and the filesystem to fill the card now, offline,
  # so cards come out of the hub already at full capacity -- no first-boot
  # resize step needed.
  echo "Growing root partition on /dev/$dev to fill the card..."
  echo ",+" | sudo sfdisk --no-reread -N 2 "/dev/$dev"
  sudo partprobe "/dev/$dev"
  sleep 1
  local ec=0
  sudo e2fsck -f -y "/dev/${dev}2" || ec=$?
  if [ "$ec" -ge 4 ]; then
    echo "ERROR: e2fsck found unrecoverable errors on /dev/${dev}2 (exit $ec)"
    return 1
  fi
  sudo resize2fs "/dev/${dev}2"

  sudo mount "/dev/${dev}2" "$mnt"
  sudo tune2fs -U random "/dev/${dev}2"
  sudo sed -i -E "s/PARTUUID=[A-Za-z0-9]{8}-01/PARTUUID=${newid_hex}-01/" "$mnt/etc/fstab"
  sudo sed -i -E "s/PARTUUID=[A-Za-z0-9]{8}-02/PARTUUID=${newid_hex}-02/" "$mnt/etc/fstab"
  sudo truncate -s 0 "$mnt/etc/machine-id"
  sudo rm -f "$mnt"/etc/ssh/ssh_host_*
  sudo rm -f "$mnt/var/lib/dhcpcd/duid"
  sudo umount "$mnt"

  sudo mount "/dev/${dev}1" "$mnt"
  sudo sed -i -E "s/PARTUUID=[A-Za-z0-9]{8}-02/PARTUUID=${newid_hex}-02/" "$mnt/cmdline.txt"
  sudo umount "$mnt"

  echo "=== /dev/$dev done: new PARTUUID ${newid_hex}-01 / ${newid_hex}-02 ==="
}

echo "=== Fixing per-device identity in parallel (logs: $LOGDIR) ==="
pids=()
for dev in "${DEVICES[@]}"; do
  fix_identity "$dev" > "$LOGDIR/$dev-identity.log" 2>&1 &
  pids+=("$!")
done

fail=0
for i in "${!pids[@]}"; do
  dev="${DEVICES[$i]}"
  status=0
  wait "${pids[$i]}" || status=$?
  cat "$LOGDIR/$dev-identity.log"
  if [ "$status" -ne 0 ]; then
    echo "ERROR: identity fix failed on /dev/$dev -- see $LOGDIR/$dev-identity.log"
    fail=1
  fi
done
if [ "$fail" -ne 0 ]; then
  exit 1
fi

echo "=== All devices processed. Verifying ==="
for dev in "${DEVICES[@]}"; do
  echo "--- /dev/$dev ---"
  sudo blkid "/dev/${dev}1" "/dev/${dev}2"
done

echo "=== Done. Boot-test at least one card before deploying the rest. ==="