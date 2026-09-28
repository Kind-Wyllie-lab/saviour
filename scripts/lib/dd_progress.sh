#!/bin/bash
# Shared helper: live_progress_dashboard <logdir> <total_bytes> <device1> [device2] ...
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
# scroll): percent complete, rate, and ETA. dd's own status=progress output
# has no percent/ETA (it doesn't know the target's total size) -- but the
# caller does (the image file's size, or the source device's size), so this
# takes it as `total_bytes` and derives percent/rate/ETA itself from the
# raw byte-count dd does report, rather than parsing dd's human-formatted
# "307 MB/s" text (unit suffixes, decimal vs binary -- not worth it when
# the leading byte count is a plain, unambiguous integer).
#
# dd separates interim progress updates with '\r' and terminates its final
# summary with '\n' (the "N+0 records in/out" lines land *before* the final
# "X bytes ... copied" line, so it's still the last line either way), so
# `tr '\r' '\n' | tail -1` reliably grabs the most recent status line, and
# its leading integer is the cumulative byte count so far.
#
# Usage: source scripts/lib/dd_progress.sh
#        live_progress_dashboard "$LOGDIR" "$TOTAL_BYTES" "${DEVICES[@]}" &
#        MONITOR_PID=$!
#        ...wait on the dd PIDs...
#        kill "$MONITOR_PID" 2>/dev/null || true
#        wait "$MONITOR_PID" 2>/dev/null || true

live_progress_dashboard() {
  local logdir="$1"
  local total_bytes="${2:-0}"
  shift 2
  local devices=("$@")

  local -A prev_bytes
  local -A prev_time
  local d now0
  now0=$(date +%s)
  for d in "${devices[@]}"; do
    prev_bytes["$d"]=0
    prev_time["$d"]=$now0
  done

  tput sc 2>/dev/null || true
  while true; do
    tput rc 2>/dev/null || true
    tput ed 2>/dev/null || true
    echo "--- write progress (refreshes every 2s) ---"
    local now
    now=$(date +%s)
    for d in "${devices[@]}"; do
      local line="" bytes_now=""
      if [ -f "$logdir/$d.log" ]; then
        line=$(tr '\r' '\n' < "$logdir/$d.log" 2>/dev/null | sed '/^\s*$/d' | tail -1)
      fi
      bytes_now=$(printf '%s' "$line" | grep -oE '^[0-9]+' || true)

      if [ -z "$bytes_now" ]; then
        printf "  %-8s starting...\n" "$d"
        continue
      fi

      local dt=$((now - prev_time["$d"]))
      local db=$((bytes_now - prev_bytes["$d"]))
      local rate_bps=0
      if [ "$dt" -gt 0 ]; then
        rate_bps=$((db / dt))
      fi
      prev_bytes["$d"]=$bytes_now
      prev_time["$d"]=$now

      local pct_str="?"
      if [ "$total_bytes" -gt 0 ] 2>/dev/null; then
        local pct_x10=$((bytes_now * 1000 / total_bytes))
        [ "$pct_x10" -gt 1000 ] && pct_x10=1000
        pct_str="$((pct_x10 / 10)).$((pct_x10 % 10))"
      fi

      local eta_str="--:--:--"
      if [ "$rate_bps" -gt 0 ] && [ "$total_bytes" -gt 0 ] 2>/dev/null; then
        local remaining=$((total_bytes - bytes_now))
        [ "$remaining" -lt 0 ] && remaining=0
        local eta_s=$((remaining / rate_bps))
        eta_str=$(printf '%02d:%02d:%02d' $((eta_s / 3600)) $(((eta_s % 3600) / 60)) $((eta_s % 60)))
      fi

      local rate_str
      rate_str=$(numfmt --to=iec --suffix=B/s "$rate_bps" 2>/dev/null || echo "${rate_bps}B/s")

      printf "  %-8s %5s%%  %10s  ETA %s\n" "$d" "$pct_str" "$rate_str" "$eta_str"
    done
    sleep 2
  done
}
