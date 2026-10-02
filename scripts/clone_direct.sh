#!/bin/bash
# SAVIOUR Direct SD Clone Script
#
# Clones a live source SD card straight to N target SD cards -- no
# intermediate .img file. Use this instead of
# capture_master_image.sh + multiclone.sh when you just need to clone one
# card to a handful of others right now and don't need a reusable image:
# it needs essentially no free disk space on the host (capture_master_image.sh
# needs free space >= the source card's FULL raw capacity, since it dd's the
# whole device before it shrinks anything -- that's almost certainly why a
# capture on another Pi's own small SD card ran out of space mid-write).
#
# Trade-off: each target gets a full raw copy of the source device's used
# capacity (not shrunk), so this is slower per-target than writing a
# pre-shrunk image, and it ties up the physical source card for the whole
# run. For a reusable master you clone from repeatedly, still use
# capture_master_image.sh + multiclone.sh (just make sure the host has free
# space >= the source card's full size, e.g. an external SSD, not another
# Pi's own SD card).
#
# Usage: sudo scripts/clone_direct.sh                                    (interactive TUI, pick devices from a list)
#        sudo scripts/clone_direct.sh <source_device> <target1> [target2] ...   (scriptable, no prompts beyond the final yes/no)
# Example: sudo scripts/clone_direct.sh sdb sdc sdd sde

set -euo pipefail

# Safety: refuse to touch the running root device, as source or target.
# Computed up front -- both the TUI (to exclude it from pickable lists) and
# the plain-args path (to reject it outright) need this.
ROOT_DEV=$(findmnt -n -o SOURCE / | sed -E 's/p?[0-9]+$//')
ROOT_DISK=$(basename "$(readlink -f "$ROOT_DEV")")

if [ "$#" -gt 0 ]; then
  # ── Scriptable path: source + targets given on the command line ──────────────
  SRC="$1"
  shift
  DEVICES=("$@")

  if [ ${#DEVICES[@]} -eq 0 ]; then
    echo "Usage: $0 [<source_device> <target_device1> [target_device2] ...]"
    echo "Example: $0 sdb sdc sdd sde"
    echo "(or run with no arguments for an interactive device picker)"
    exit 1
  fi

  if [ ! -b "/dev/$SRC" ]; then
    echo "ERROR: /dev/$SRC is not a block device"
    exit 1
  fi

  for d in "$SRC" "${DEVICES[@]}"; do
    if [ "$(readlink -f "/dev/$d")" = "$(readlink -f "$ROOT_DEV")" ]; then
      echo "ERROR: refusing to touch /dev/$d -- it looks like the running system disk"
      exit 1
    fi
    if [[ "$d" == mmcblk0* ]]; then
      echo "ERROR: refusing to touch /dev/$d (looks like the running system disk)"
      exit 1
    fi
  done
  for d in "${DEVICES[@]}"; do
    if [ "$d" = "$SRC" ]; then
      echo "ERROR: target /dev/$d is the same as the source device"
      exit 1
    fi
  done

  echo "=== Source device: /dev/$SRC ==="
  echo "=== Target devices: ${DEVICES[*]} ==="
  lsblk
  echo
  read -p "Confirm source is the correct module card, and targets are blank/intended? (yes/no): " confirm
  if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 1
  fi

else
  # ── Interactive path: whiptail device picker ──────────────────────────────────
  if [ ! -t 0 ]; then
    echo "ERROR: no device arguments given and this isn't an interactive terminal."
    echo "Usage: $0 <source_device> <target_device1> [target_device2] ..."
    exit 1
  fi

  if ! command -v whiptail &>/dev/null; then
    echo "whiptail not found -- installing..."
    sudo apt-get install -y whiptail
  fi

  # shellcheck source=lib/identify_disk.sh
  source "$(dirname "$(readlink -f "$0")")/lib/identify_disk.sh"

  W=78
  H=20
  wt() { whiptail "$@" 3>&1 1>&2 2>&3; }

  # lsblk -P emits shell-quoted KEY="value" pairs, one disk per line --
  # robust against spaces in MODEL, unlike parsing the plain table output.
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
    candidates+=("$NAME" "${SIZE:-?} -- ${id}")
  done < <(sudo lsblk -dn -P -o NAME,SIZE,MODEL,TRAN,TYPE)

  if [ ${#candidates[@]} -eq 0 ]; then
    whiptail --title "No Devices Found" \
      --msgbox "\nNo candidate block devices found (other than the running system disk).\n\nCheck the SD card readers are plugged in, then re-run." 12 $W
    exit 1
  fi

  # Radiolist: tag, description, status(ON/OFF) triples -- default all OFF.
  src_menu=()
  for ((i = 0; i < ${#candidates[@]}; i += 2)); do
    src_menu+=("${candidates[$i]}" "${candidates[$i+1]}" "OFF")
  done

  SRC=$(wt --title "Select Source Card" --radiolist \
    "\nWhich device is the source SD card to clone FROM?\n(the already-configured module -- this card is only read, never written)\n" \
    $H $W $((${#candidates[@]} / 2)) \
    "${src_menu[@]}") || { echo "Aborted."; exit 1; }

  if [ -z "$SRC" ]; then
    whiptail --title "No Selection" --msgbox "\nNo source device selected." 8 $W
    exit 1
  fi

  # Checklist for targets: same candidate list minus whichever was picked as source.
  tgt_menu=()
  for ((i = 0; i < ${#candidates[@]}; i += 2)); do
    [ "${candidates[$i]}" = "$SRC" ] && continue
    tgt_menu+=("${candidates[$i]}" "${candidates[$i+1]}" "OFF")
  done

  if [ ${#tgt_menu[@]} -eq 0 ]; then
    whiptail --title "No Targets" --msgbox "\nNo other candidate devices found to use as clone targets." 8 $W
    exit 1
  fi

  tgt_raw=$(wt --title "Select Target Cards" --checklist \
    "\nWhich devices should be OVERWRITTEN with a clone of /dev/$SRC?\nUse space to select, enter to confirm.\n" \
    $H $W $((${#tgt_menu[@]} / 3)) \
    "${tgt_menu[@]}") || { echo "Aborted."; exit 1; }

  if [ -z "$tgt_raw" ]; then
    whiptail --title "No Selection" --msgbox "\nNo target devices selected." 8 $W
    exit 1
  fi

  # whiptail --checklist returns space-separated, double-quoted tags.
  DEVICES=()
  eval "DEVICES=($tgt_raw)"

  summary="Source (read-only):\n  /dev/$SRC\n\nTargets (WILL BE OVERWRITTEN):\n"
  for d in "${DEVICES[@]}"; do
    summary+="  /dev/$d\n"
  done
  summary+="\nThis cannot be undone. Proceed?"

  if ! whiptail --title "Confirm Clone" --yesno "\n$summary" $((10 + ${#DEVICES[@]})) $W \
    --yes-button "Clone" --no-button "Cancel"; then
    echo "Aborted."
    exit 1
  fi

  clear
  echo "=== Source device: /dev/$SRC ==="
  echo "=== Target devices: ${DEVICES[*]} ==="
fi

# Safety: refuse a source that doesn't look like a Raspberry Pi OS card
# (vfat boot + ext4 root) -- catches pointing this at the wrong device
# before tying up every target card on a bad copy.
echo "=== Validating source device ==="
BOOT_FSTYPE=$(sudo blkid -s TYPE -o value "/dev/${SRC}1" 2>/dev/null || true)
ROOT_FSTYPE=$(sudo blkid -s TYPE -o value "/dev/${SRC}2" 2>/dev/null || true)
if [ "$BOOT_FSTYPE" != "vfat" ] || [ "$ROOT_FSTYPE" != "ext4" ]; then
  echo "ERROR: /dev/$SRC does not look like a Raspberry Pi OS card."
  echo "  Expected: partition 1 = vfat (boot), partition 2 = ext4 (root)"
  echo "  Found:    partition 1 = ${BOOT_FSTYPE:-<none>}, partition 2 = ${ROOT_FSTYPE:-<none>}"
  exit 1
fi
echo "OK -- partition 1 = vfat (boot), partition 2 = ext4 (root)"

# Pre-flight capacity check. This is a raw whole-device copy (no shrink),
# so every target must be able to hold the source's full size -- catch
# an undersized target now rather than mid-write.
SRC_BYTES=$(sudo blockdev --getsize64 "/dev/$SRC")
echo "=== Checking target capacity (source is $((SRC_BYTES / 1024 / 1024 / 1024)) GB) ==="
for d in "${DEVICES[@]}"; do
  tgt_bytes=$(sudo blockdev --getsize64 "/dev/$d")
  if [ "$tgt_bytes" -lt "$SRC_BYTES" ]; then
    echo "ERROR: /dev/$d is $((tgt_bytes / 1024 / 1024 / 1024)) GB, smaller than source ($((SRC_BYTES / 1024 / 1024 / 1024)) GB)"
    exit 1
  fi
done
echo "OK -- all targets are large enough"

# Unmount any pre-existing filesystems on target devices, same rationale
# as multiclone.sh: a stale mount blocks the kernel re-reading the new
# partition table after the write.
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

LOGDIR=$(mktemp -d /tmp/clonedirect.XXXXXX)
echo "=== Reading /dev/$SRC and writing to all targets in parallel (logs: $LOGDIR) ==="
# Each target gets its own independent dd reading straight from the source
# device -- deliberately NOT a single `dd | tee` pipeline. tee writes each
# block to every output in turn before reading the next one, so the whole
# copy runs at the pace of the slowest target (the same round-robin
# bottleneck multiclone.sh's own comments call out for dcfldd). Separate
# readers let the kernel keep every other card's read/write in flight
# while one card is momentarily slow, at the cost of re-reading the
# physical source card once per target instead of once total -- a fine
# trade since these are SD readers on a shared hub, not the bottleneck.
pids=()
for d in "${DEVICES[@]}"; do
  sudo dd if="/dev/$SRC" of="/dev/$d" bs=4M conv=fsync status=progress \
    > "$LOGDIR/$d.log" 2>&1 &
  pids+=("$!")
done

# Each dd's progress goes to its own log file (see above), so without this
# nothing shows on screen for the whole write. Poll and redraw a status
# line per device until every job finishes.
source "$(dirname "$(readlink -f "$0")")/lib/dd_progress.sh"
live_progress_dashboard "$LOGDIR" "$SRC_BYTES" "${DEVICES[@]}" &
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
  sudo partprobe "/dev/$d" || echo "WARNING: partprobe failed for /dev/$d -- will retry via identity-fix step"
done
sleep 2   # let udev settle

# Per-device identity correction -- same as multiclone.sh's fix_identity:
# new PARTUUID, grow partition 2 to fill any extra target capacity, fresh
# SSH host keys, cleared machine-id, temporary per-device hostname.
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

  # Grow the root partition/filesystem to fill any extra capacity on a
  # target card larger than the source (a no-op when they're the same size).
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
    # distinct signal. Each target independently re-reads /dev/$SRC, so if
    # other targets from this same run came out clean, suspect this specific
    # target's card, reader, cable or USB port rather than the source card.
    echo "WARNING: e2fsck found and auto-repaired filesystem errors on /dev/${dev}2 (exit $ec) -- see the Pass 1-5 output above for what was recovered into lost+found."
    touch "${LOGDIR}/${dev}.fsck_dirty"
  fi
  sudo resize2fs "/dev/${dev}2"

  sudo mount "/dev/${dev}2" "$mnt"
  sudo tune2fs -U random "/dev/${dev}2"
  sudo sed -i -E "s/PARTUUID=[A-Za-z0-9]{8}-01/PARTUUID=${newid_hex}-01/" "$mnt/etc/fstab"
  sudo sed -i -E "s/PARTUUID=[A-Za-z0-9]{8}-02/PARTUUID=${newid_hex}-02/" "$mnt/etc/fstab"
  sudo truncate -s 0 "$mnt/etc/machine-id"

  # Browser profile locks name the source machine (hostname-pid), so every
  # clone's Chromium/Firefox reported "profile in use by another computer"
  # (found 2026-10-02 on a controller cloned from a camera's image).
  sudo rm -f "$mnt"/home/*/.config/chromium/Singleton{Lock,Cookie,Socket} \
             "$mnt"/root/.config/chromium/Singleton{Lock,Cookie,Socket}
  sudo find "$mnt"/home/*/.mozilla "$mnt"/root/.mozilla -maxdepth 4 \
       \( -name lock -o -name .parentlock \) -delete 2>/dev/null || true

  echo "Regenerating SSH host keys..."
  sudo rm -f "$mnt"/etc/ssh/ssh_host_*
  sudo ssh-keygen -A -f "$mnt"

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
  echo " Other targets read independently from /dev/$SRC in this same run were"
  echo " clean, so the source card is presumably fine -- this points at that"
  echo " specific target card, reader, cable, or USB port. Do not deploy"
  echo " ${dirty_devices[*]} without re-flashing (ideally on a different port)"
  echo " and re-checking."
  echo "=================================================================="
fi
echo "=== Done. Boot-test at least one card before deploying the rest. ==="
echo "=== On first boot, run 'sudo saviour-config' on each -- it auto-detects the clone ==="
echo "=== and offers Refresh Identity (hostname, SSH keys, machine ID, active config). ==="
