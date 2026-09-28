#!/bin/bash
# Shared helper: live_progress_dashboard <logdir> <device1> [device2] ...
#
# clone_direct.sh and multiclone.sh both run one `dd status=progress` per
# target device in the background, each redirected to its own log file --
# necessary because N dd processes all writing "\r"-updated progress lines
# straight to the shared terminal at once would just mangle each other. But
# that means nothing shows on screen until a job finishes or fails, which
# is a long silent wait for a multi-GB image.
#
# This polls each device's log file every couple of seconds and redraws a
# one-line-per-device status board in place (using tput sc/rc so it doesn't
# scroll). dd separates interim progress updates with '\r' and terminates
# its final summary with '\n', so `tr '\r' '\n' | tail -1` reliably grabs
# the most recent line either way.
#
# Usage: source scripts/lib/dd_progress.sh
#        live_progress_dashboard "$LOGDIR" "${DEVICES[@]}" &
#        MONITOR_PID=$!
#        ...wait on the dd PIDs...
#        kill "$MONITOR_PID" 2>/dev/null || true
#        wait "$MONITOR_PID" 2>/dev/null || true

live_progress_dashboard() {
  local logdir="$1"
  shift
  local devices=("$@")

  tput sc 2>/dev/null || true
  while true; do
    tput rc 2>/dev/null || true
    tput ed 2>/dev/null || true
    echo "--- write progress (refreshes every 2s) ---"
    for d in "${devices[@]}"; do
      local last=""
      if [ -f "$logdir/$d.log" ]; then
        last=$(tr '\r' '\n' < "$logdir/$d.log" 2>/dev/null | sed '/^\s*$/d' | tail -1)
      fi
      printf "  %-8s %s\n" "$d" "${last:-starting...}"
    done
    sleep 2
  done
}
