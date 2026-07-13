#!/bin/bash
# SAVIOUR Master Image Capture Script
#
# Captures a template SD card (Raspberry Pi OS + SAVIOUR installed via
# install.sh, role left unset) into a .img file, then shrinks the root
# filesystem and partition to actual used size. A shrunk image is the
# single biggest lever on multiclone.sh's flash time -- a full 64GB card
# is usually only a few GB actually used.
#
# Usage: sudo ./capture_master_image.sh <source_device> <output.img>
# Example: sudo ./capture_master_image.sh /dev/mmcblk0 /home/pi/saviour-master.img
#
# The source device must be a real Raspberry Pi OS card: partition 1 =
# vfat (boot), partition 2 = ext4 (root). Run this against a template
# card that has been booted, had install.sh run on it, and been shut
# down cleanly -- NOT the card currently running this script.

set -euo pipefail

SRC_DEV="$1"
OUT_IMG="$2"

if [ -z "$SRC_DEV" ] || [ -z "$OUT_IMG" ]; then
  echo "Usage: $0 <source_device> <output.img>"
  echo "Example: $0 /dev/mmcblk0 /home/pi/saviour-master.img"
  exit 1
fi

if [ ! -b "$SRC_DEV" ]; then
  echo "ERROR: $SRC_DEV is not a block device"
  exit 1
fi

# Safety: refuse to capture the disk this script is currently running from
ROOT_DEV=$(findmnt -n -o SOURCE / | sed -E 's/p?[0-9]+$//')
if [ "$(readlink -f "$SRC_DEV")" = "$(readlink -f "$ROOT_DEV")" ]; then
  echo "ERROR: refusing to capture $SRC_DEV -- it looks like the running system disk"
  exit 1
fi

echo "=== Source device: $SRC_DEV ==="
lsblk "$SRC_DEV"
echo
echo "=== Output image: $OUT_IMG ==="
read -p "Confirm this is the template card, booted and shut down cleanly? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
  echo "Aborted."
  exit 1
fi

echo "=== Capturing $SRC_DEV -> $OUT_IMG ==="
sudo dd if="$SRC_DEV" of="$OUT_IMG" bs=4M status=progress conv=fsync
sync

echo "=== Validating captured image ==="
LOOPDEV=$(sudo losetup -fP --show "$OUT_IMG")
cleanup() { sudo losetup -d "$LOOPDEV" 2>/dev/null || true; }
trap cleanup EXIT

BOOT_FSTYPE=$(sudo blkid -s TYPE -o value "${LOOPDEV}p1" 2>/dev/null || true)
ROOT_FSTYPE=$(sudo blkid -s TYPE -o value "${LOOPDEV}p2" 2>/dev/null || true)
if [ "$BOOT_FSTYPE" != "vfat" ] || [ "$ROOT_FSTYPE" != "ext4" ]; then
  echo "ERROR: captured image does not look like a Raspberry Pi OS image."
  echo "  Expected: partition 1 = vfat (boot), partition 2 = ext4 (root)"
  echo "  Found:    partition 1 = ${BOOT_FSTYPE:-<none>}, partition 2 = ${ROOT_FSTYPE:-<none>}"
  echo "  $SRC_DEV is probably not the template card -- check you pointed this"
  echo "  at the right device."
  exit 1
fi
echo "OK -- partition 1 = vfat (boot), partition 2 = ext4 (root)"

echo "=== Shrinking root filesystem to minimum ==="
ec=0
sudo e2fsck -f -y "${LOOPDEV}p2" || ec=$?
if [ "$ec" -ge 4 ]; then
  echo "ERROR: e2fsck found unrecoverable errors on the captured image's root filesystem (exit $ec)"
  echo "  Re-capture from the source card; do not trust this image."
  exit 1
fi
sudo resize2fs -M "${LOOPDEV}p2"

BLOCK_COUNT=$(sudo dumpe2fs -h "${LOOPDEV}p2" 2>/dev/null | grep -i '^Block count:' | awk '{print $3}')
BLOCK_SIZE=$(sudo dumpe2fs -h "${LOOPDEV}p2" 2>/dev/null | grep -i '^Block size:' | awk '{print $3}')
MIN_FS_BYTES=$((BLOCK_COUNT * BLOCK_SIZE))
MARGIN_BYTES=$((500 * 1024 * 1024))   # headroom so the shrunk fs isn't bone dry
TARGET_FS_BYTES=$((MIN_FS_BYTES + MARGIN_BYTES))

PART2_START_SECTOR=$(sudo parted -s "$OUT_IMG" unit s print | awk '$1 == "2" {gsub("s","",$2); print $2}')
NEW_END_SECTOR=$((PART2_START_SECTOR + (TARGET_FS_BYTES / 512) + 2048))

echo "=== Shrinking partition 2 to match ==="
sudo losetup -d "$LOOPDEV"
trap - EXIT
# parted's "shrinking a partition can cause data loss" confirmation refuses
# outright under -s/--script regardless of what's piped to stdin -- it's
# not a normal prompt, it's a hard no. sfdisk has no such gate and resizes
# a single partition's size field directly.
NEW_SIZE_SECTORS=$((NEW_END_SECTOR - PART2_START_SECTOR + 1))
echo ",${NEW_SIZE_SECTORS}" | sudo sfdisk --no-reread -N 2 "$OUT_IMG"

echo "=== Truncating image file to new size ==="
NEW_TOTAL_BYTES=$(((NEW_END_SECTOR + 1) * 512))
sudo truncate -s "$NEW_TOTAL_BYTES" "$OUT_IMG"

echo "=== Re-validating shrunk image ==="
LOOPDEV=$(sudo losetup -fP --show "$OUT_IMG")
BOOT_FSTYPE=$(sudo blkid -s TYPE -o value "${LOOPDEV}p1" 2>/dev/null || true)
ROOT_FSTYPE=$(sudo blkid -s TYPE -o value "${LOOPDEV}p2" 2>/dev/null || true)
sudo losetup -d "$LOOPDEV"
if [ "$BOOT_FSTYPE" != "vfat" ] || [ "$ROOT_FSTYPE" != "ext4" ]; then
  echo "ERROR: shrunk image failed validation (partition 1 = ${BOOT_FSTYPE:-<none>}, partition 2 = ${ROOT_FSTYPE:-<none>})"
  echo "  The pre-shrink image is still whatever dd wrote before the shrink step ran; re-run from a fresh capture."
  exit 1
fi

echo
echo "=== Done ==="
echo "Master image: $OUT_IMG"
ls -lh "$OUT_IMG"
echo "Use with: sudo ./multiclone.sh $OUT_IMG <device1> [device2] ..."
