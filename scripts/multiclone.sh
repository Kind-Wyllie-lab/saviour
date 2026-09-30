#!/bin/bash
# SAVIOUR Multi-Clone Script
#
# Usage: sudo scripts/multiclone.sh                                (interactive TUI)
#        sudo scripts/multiclone.sh <image.img> <device1> [device2] ...   (scriptable)
# Example: sudo scripts/multiclone.sh /mnt/export/saviour-image.img sda sdb sdc sdd

set -euo pipefail

ROOT_DEV=$(findmnt -n -o SOURCE / | sed -E 's/p?[0-9]+$//')
ROOT_DISK=$(basename "$(readlink -f "$ROOT_DEV")")

if [ "$#" -gt 0 ]; then
  # ── Scriptable path ────────────────────────────────────────────────────────
  IMAGE="$1"
  shift
  DEVICES=("$@")

  if [ -z "$IMAGE" ] || [ ${#DEVICES[@]} -eq 0 ]; then
    echo "Usage: $0 [<image.img> <device1> [device2] [device3] ...]"
    echo "Example: $0 /mnt/export/saviour-image.img sda sdb sdc sdd"
    echo "(or run with no arguments for an interactive device picker)"
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

else
  # ── Interactive path: whiptail image + target picker ───────────────────────
  if [ ! -t 0 ]; then
    echo "ERROR: no arguments given and this isn't an interactive terminal."
    echo "Usage: $0 <image.img> <device1> [device2] [device3] ..."
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

  # Offer a pick-list of *.img files found in common spots, but always let
  # the user type/edit a path -- inputbox pre-filled with the newest match.
  found_img=$(find /mnt /home /root -maxdepth 3 -name '*.img' -newer /etc/hostname 2>/dev/null | head -1 || true)
  [ -z "$found_img" ] && found_img=$(find /mnt /home /root -maxdepth 3 -name '*.img' 2>/dev/null | sort | tail -1 || true)

  IMAGE=$(wt --title "Source Image" --inputbox \
    "\nPath to the master image to flash (from capture_master_image.sh):\n" \
    10 $W "${found_img:-/mnt/export/saviour-image.img}") || { echo "Aborted."; exit 1; }

  if [ -z "$IMAGE" ] || [ ! -f "$IMAGE" ]; then
    whiptail --title "Image Not Found" --msgbox "\n$IMAGE does not exist." 8 $W
    exit 1
  fi

  echo "Identifying connected cards (mounting each briefly, read-only)..."
  candidates=()
  while IFS= read -r line; do
    NAME="" SIZE="" MODEL="" TRAN="" TYPE=""
    eval "$line"
    [ "$TYPE" = "disk" ] || continue
    [ "$NAME" = "$ROOT_DISK" ] && continue
    [[ "$NAME" == mmcblk0* ]] && continue
    id=$(identify_disk "$NAME")
    echo "  /dev/$NAME: $id"
    candidates+=("$NAME" "${SIZE:-?} -- ${id}" "OFF")
  done < <(sudo lsblk -dn -P -o NAME,SIZE,MODEL,TRAN,TYPE)

  if [ ${#candidates[@]} -eq 0 ]; then
    whiptail --title "No Devices Found" \
      --msgbox "\nNo candidate block devices found (other than the running system disk).\n\nCheck the SD card readers are plugged in, then re-run." 12 $W
    exit 1
  fi

  tgt_raw=$(wt --title "Select Target Cards" --checklist \
    "\nWhich devices should be OVERWRITTEN with $IMAGE?\nUse space to select, enter to confirm.\n" \
    $H $W $((${#candidates[@]} / 3)) \
    "${candidates[@]}") || { echo "Aborted."; exit 1; }

  if [ -z "$tgt_raw" ]; then
    whiptail --title "No Selection" --msgbox "\nNo target devices selected." 8 $W
    exit 1
  fi
  DEVICES=()
  eval "DEVICES=($tgt_raw)"

  summary="Image: $IMAGE\n\nTargets (WILL BE OVERWRITTEN):\n"
  for d in "${DEVICES[@]}"; do
    summary+="  /dev/$d\n"
  done
  summary+="\nThis cannot be undone. Proceed?"

  if ! whiptail --title "Confirm Flash" --yesno "\n$summary" $((10 + ${#DEVICES[@]})) $W \
    --yes-button "Flash" --no-button "Cancel"; then
    echo "Aborted."
    exit 1
  fi

  clear
  echo "=== Target devices: ${DEVICES[*]} ==="
  echo "=== Source image: $IMAGE ==="
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

IMAGE_BYTES=$(stat -c%s "$IMAGE" 2>/dev/null || echo 0)
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

# Each dd's progress goes to its own log file (see above), so without this
# nothing shows on screen for the whole write. Poll and redraw a status
# line per device until every job finishes.
source "$(dirname "$(readlink -f "$0")")/lib/dd_progress.sh"
live_progress_dashboard "$LOGDIR" "$IMAGE_BYTES" "${DEVICES[@]}" &
MONITOR_PID=$!

fail=0
for i in "${!pids[@]}"; do
  if ! wait "${pids[$i]}"; then
    echo "ERROR: write to /dev/${DEVICES[$i]} failed -- see $LOGDIR/${DEVICES[$i]}.log"
    fail=1
  fi
done
kill "$MONITOR_PID" 2>/dev/null || true
wait "$MONITOR_PID" 2>/dev/null || true
echo
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
  if [ "$ec" -ne 0 ]; then
    # exit 1/2 means e2fsck found AND fixed errors -- below the >=4 threshold
    # that aborts the run, so without this the device sails through with no
    # distinct signal. A clean write of a filesystem that was itself already
    # checked (capture_master_image.sh's own shrink step runs e2fsck -f -y
    # too) shouldn't need repairing -- if it did, suspect this specific card,
    # reader, cable or USB port, not the source image.
    echo "WARNING: e2fsck found and auto-repaired filesystem errors on /dev/${dev}2 (exit $ec) -- see the Pass 1-5 output above for what was recovered into lost+found."
    touch "${LOGDIR}/${dev}.fsck_dirty"
  fi
  sudo resize2fs "/dev/${dev}2"

  sudo mount "/dev/${dev}2" "$mnt"
  sudo tune2fs -U random "/dev/${dev}2"
  sudo sed -i -E "s/PARTUUID=[A-Za-z0-9]{8}-01/PARTUUID=${newid_hex}-01/" "$mnt/etc/fstab"
  sudo sed -i -E "s/PARTUUID=[A-Za-z0-9]{8}-02/PARTUUID=${newid_hex}-02/" "$mnt/etc/fstab"
  sudo truncate -s 0 "$mnt/etc/machine-id"

  # Regenerate unique host keys now, offline. Raspberry Pi OS only
  # auto-regenerates these via regenerate_ssh_host_keys.service, which is a
  # one-shot that disables itself after firing -- since the master image is
  # captured from a template that already booted once (to install SAVIOUR),
  # that service is already spent in the image. Deleting the keys without
  # this step leaves sshd with none to load, so it exits on boot and every
  # clone refuses SSH connections instead of merely being slow to answer.
  echo "Regenerating SSH host keys..."
  sudo rm -f "$mnt"/etc/ssh/ssh_host_*
  sudo ssh-keygen -A -f "$mnt"

  # A temporary, per-device hostname so each card is identifiable in DHCP
  # leases before SAVIOUR's own provisioning assigns a real one. dhcpcd/
  # NetworkManager both omit DHCP option 12 (hostname) when the hostname is
  # empty/unset/"localhost", so an unset hostname shows up as "*" in the
  # lease table -- indistinguishable from any other device on the network.
  # Suffixed with the same per-device id already used for PARTUUID so
  # multiple cards booting simultaneously out of the same image don't all
  # claim the same hostname at once.
  echo "Setting temporary hostname..."
  TEMP_HOSTNAME="saviour-unprov-${newid_hex}"
  echo "$TEMP_HOSTNAME" | sudo tee "$mnt/etc/hostname" > /dev/null
  if sudo grep -q "^127\.0\.1\.1" "$mnt/etc/hosts"; then
    sudo sed -i -E "s/^127\.0\.1\.1.*/127.0.1.1\t${TEMP_HOSTNAME}/" "$mnt/etc/hosts"
  else
    echo -e "127.0.1.1\t${TEMP_HOSTNAME}" | sudo tee -a "$mnt/etc/hosts" > /dev/null
  fi

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
verify_fail=0
for dev in "${DEVICES[@]}"; do
  echo "--- /dev/$dev ---"
  sudo blkid "/dev/${dev}1" "/dev/${dev}2"

  vmnt="/mnt/card-verify-$dev"
  sudo mkdir -p "$vmnt"
  sudo mount "/dev/${dev}2" "$vmnt"
  key_count=$(sudo find "$vmnt/etc/ssh" -maxdepth 1 -name 'ssh_host_*_key' 2>/dev/null | wc -l)
  hostname_val=$(sudo cat "$vmnt/etc/hostname" 2>/dev/null || echo "MISSING")
  sudo umount "$vmnt"
  echo "  SSH host keys: $key_count"
  echo "  Hostname: $hostname_val"
  if [ "$key_count" -eq 0 ]; then
    echo "  WARNING: no SSH host keys on /dev/$dev -- sshd will refuse to start on boot!" >&2
    verify_fail=1
  fi
  if [ -f "${LOGDIR}/${dev}.fsck_dirty" ]; then
    echo "  WARNING: filesystem corruption was found and auto-repaired on this device" >&2
    verify_fail=1
  fi
done

if [ "$verify_fail" -ne 0 ]; then
  echo "=== WARNING: one or more devices need attention -- see above -- before deploying ==="
fi
dirty_devices=()
for dev in "${DEVICES[@]}"; do
  [ -f "${LOGDIR}/${dev}.fsck_dirty" ] && dirty_devices+=("$dev")
done
if [ ${#dirty_devices[@]} -gt 0 ]; then
  echo ""
  echo "=================================================================="
  echo " e2fsck found and auto-repaired filesystem corruption on: ${dirty_devices[*]}"
  echo " Other targets written from the same source image in this same run were"
  echo " clean, so the image itself is presumably fine -- this points at that"
  echo " specific card, reader, cable, or USB port. Do not deploy ${dirty_devices[*]}"
  echo " without re-flashing (ideally on a different port) and re-checking."
  echo "=================================================================="
fi
echo "=== Done. Boot-test at least one card before deploying the rest. ==="