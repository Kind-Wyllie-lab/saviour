#!/bin/bash
# SAVIOUR Master Image Capture Script
#
# Captures a template SD card (Raspberry Pi OS + SAVIOUR installed via
# install.sh, role left unset) into a .img file, then shrinks the root
# filesystem and partition to actual used size. A shrunk image is the
# single biggest lever on multiclone.sh's flash time -- a full 64GB card
# is usually only a few GB actually used.
#
# IMPORTANT: the raw dd below needs free space >= the source card's FULL
# capacity (shrinking happens only after the whole device is copied) --
# run this on a host with real spare storage (a controller's NVMe, an
# external SSD), not another Pi's own small SD card, or it runs out of
# space mid-write.
#
# Usage: sudo scripts/capture_master_image.sh                                (interactive TUI)
#        sudo scripts/capture_master_image.sh <source_device> <output.img>   (scriptable)
# Example: sudo scripts/capture_master_image.sh /dev/mmcblk0 /home/pi/saviour-master.img
#
# The source device must be a real Raspberry Pi OS card: partition 1 =
# vfat (boot), partition 2 = ext4 (root). Run this against a card that has
# been booted, had install.sh run on it, and been shut down cleanly --
# NOT the card currently running this script.

set -euo pipefail

ROOT_DEV=$(findmnt -n -o SOURCE / | sed -E 's/p?[0-9]+$//')

if [ "$#" -gt 0 ]; then
  # ── Scriptable path ────────────────────────────────────────────────────────
  SRC_DEV="$1"
  OUT_IMG="$2"

  if [ -z "$SRC_DEV" ] || [ -z "$OUT_IMG" ]; then
    echo "Usage: $0 [<source_device> <output.img>]"
    echo "Example: $0 /dev/mmcblk0 /home/pi/saviour-master.img"
    echo "(or run with no arguments for an interactive device picker)"
    exit 1
  fi

  if [ ! -b "$SRC_DEV" ]; then
    echo "ERROR: $SRC_DEV is not a block device"
    exit 1
  fi

  if [ "$(readlink -f "$SRC_DEV")" = "$(readlink -f "$ROOT_DEV")" ]; then
    echo "ERROR: refusing to capture $SRC_DEV -- it looks like the running system disk"
    exit 1
  fi

  echo "=== Source device: $SRC_DEV ==="
  lsblk "$SRC_DEV"
  echo
  echo "=== Output image: $OUT_IMG ==="
  read -p "Confirm this is the correct card, booted and shut down cleanly? (yes/no): " confirm
  if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 1
  fi

else
  # ── Interactive path: whiptail device + output-path picker ─────────────────
  if [ ! -t 0 ]; then
    echo "ERROR: no arguments given and this isn't an interactive terminal."
    echo "Usage: $0 <source_device> <output.img>"
    exit 1
  fi

  if ! command -v whiptail &>/dev/null; then
    echo "whiptail not found -- installing..."
    sudo apt-get install -y whiptail
  fi

  source "$(dirname "$(readlink -f "$0")")/lib/identify_disk.sh"

  W=78
  H=20
  wt() { whiptail "$@" 3>&1 1>&2 2>&3; }
  ROOT_DISK=$(basename "$(readlink -f "$ROOT_DEV")")

  echo "Identifying connected cards (mounting each briefly, read-only)..."
  candidates=()
  while IFS= read -r line; do
    NAME="" SIZE="" MODEL="" TRAN="" TYPE=""
    eval "$line"
    [ "$TYPE" = "disk" ] || continue
    [ "$NAME" = "$ROOT_DISK" ] && continue
    id=$(identify_disk "$NAME")
    echo "  /dev/$NAME: $id"
    candidates+=("$NAME" "${SIZE:-?} -- ${id}" "OFF")
  done < <(sudo lsblk -dn -P -o NAME,SIZE,MODEL,TRAN,TYPE)

  if [ ${#candidates[@]} -eq 0 ]; then
    whiptail --title "No Devices Found" \
      --msgbox "\nNo candidate block devices found (other than the running system disk).\n\nCheck the SD card reader is plugged in, then re-run." 12 $W
    exit 1
  fi

  SRC_NAME=$(wt --title "Select Source Card" --radiolist \
    "\nWhich device should be captured into a master image?\n(read-only -- this card is never written to)\n" \
    $H $W $((${#candidates[@]} / 3)) \
    "${candidates[@]}") || { echo "Aborted."; exit 1; }

  if [ -z "$SRC_NAME" ]; then
    whiptail --title "No Selection" --msgbox "\nNo source device selected." 8 $W
    exit 1
  fi
  SRC_DEV="/dev/$SRC_NAME"

  SRC_BYTES=$(sudo blockdev --getsize64 "$SRC_DEV")
  SRC_GB=$((SRC_BYTES / 1024 / 1024 / 1024))

  DEFAULT_OUT="$HOME/saviour-${SRC_NAME}-$(date +%Y%m%d).img"
  OUT_IMG=$(wt --title "Output Image Path" --inputbox \
    "\nWhere should the captured image be written?\n\nSource is ${SRC_GB} GB -- the FULL raw size is needed as free space\nhere temporarily (shrinking happens after the copy). Pick a path\nwith real spare storage (controller NVMe, external SSD), not a\nsmall SD card's own root filesystem.\n" \
    16 $W "$DEFAULT_OUT") || { echo "Aborted."; exit 1; }

  if [ -z "$OUT_IMG" ]; then
    whiptail --title "No Path" --msgbox "\nNo output path given." 8 $W
    exit 1
  fi

  # Pre-flight free-space check against the FULL raw source size -- this is
  # exactly the check that would have caught "SD ran out of space mid-write"
  # before burning any time on the copy.
  OUT_DIR=$(dirname "$OUT_IMG")
  mkdir -p "$OUT_DIR" 2>/dev/null || true
  AVAIL_BYTES=$(df --output=avail -B1 "$OUT_DIR" 2>/dev/null | tail -1)
  if [ -z "$AVAIL_BYTES" ]; then
    whiptail --title "Path Error" --msgbox "\nCan't stat free space for $OUT_DIR -- check the path is valid." 9 $W
    exit 1
  fi
  AVAIL_GB=$((AVAIL_BYTES / 1024 / 1024 / 1024))
  if [ "$AVAIL_BYTES" -lt "$SRC_BYTES" ]; then
    whiptail --title "Not Enough Space" --msgbox "\n$OUT_DIR has ${AVAIL_GB} GB free, but the raw capture needs ${SRC_GB} GB (the source card's full capacity) before it can shrink.\n\nPick a different output path -- e.g. the controller's NVMe or an external SSD -- and try again." 14 $W
    exit 1
  fi

  if ! whiptail --title "Confirm Capture" --yesno \
    "\nSource: $SRC_DEV (${SRC_GB} GB)\nOutput: $OUT_IMG\nFree space at destination: ${AVAIL_GB} GB\n\nConfirm this is the correct card, booted and shut down cleanly?" \
    14 $W --yes-button "Capture" --no-button "Cancel"; then
    echo "Aborted."
    exit 1
  fi

  clear
  echo "=== Source device: $SRC_DEV ==="
  echo "=== Output image: $OUT_IMG ==="
fi

# Pre-flight free-space check against the FULL raw source size -- the dd
# below copies the whole device before any shrinking happens, so a host
# with less free space than the source card's total capacity (e.g.
# another Pi's own small SD card) will run out of space mid-write.
OUT_DIR=$(dirname "$OUT_IMG")
mkdir -p "$OUT_DIR" 2>/dev/null || true
SRC_BYTES=$(sudo blockdev --getsize64 "$SRC_DEV")
AVAIL_BYTES=$(df --output=avail -B1 "$OUT_DIR" 2>/dev/null | tail -1)
if [ -n "$AVAIL_BYTES" ] && [ "$AVAIL_BYTES" -lt "$SRC_BYTES" ]; then
  echo "ERROR: $OUT_DIR has $((AVAIL_BYTES / 1024 / 1024 / 1024)) GB free, but the raw"
  echo "  capture needs $((SRC_BYTES / 1024 / 1024 / 1024)) GB (source card's full capacity) before it can shrink."
  echo "  Pick a different output path with more free space and try again."
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
# exit 1/2 means e2fsck found AND fixed errors -- below the >=4 threshold
# that aborts the run. This checks the freshly-dd'd .img file, i.e. whether
# the raw copy from the source card came out clean -- a corrupted master
# image poisons every clone made from it, so flag it loudly rather than
# silently shrinking and shipping it.
FSCK_DIRTY=0
if [ "$ec" -ne 0 ]; then
  FSCK_DIRTY=1
  echo "WARNING: e2fsck found and auto-repaired filesystem errors on the captured image (exit $ec) -- see the Pass 1-5 output above for what was recovered into lost+found."
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
if [ "$FSCK_DIRTY" -ne 0 ]; then
  echo ""
  echo "=================================================================="
  echo " WARNING: e2fsck found and auto-repaired filesystem corruption while"
  echo " capturing this image (see above) -- the source card, reader, cable,"
  echo " or USB port may be unreliable. This image is structurally valid now,"
  echo " but some files may have been orphaned into lost+found rather than"
  echo " recovered under their real names. Consider re-capturing from the"
  echo " source card before cloning from this image."
  echo "=================================================================="
fi
echo "Use with: sudo scripts/multiclone.sh $OUT_IMG <device1> [device2] ..."
