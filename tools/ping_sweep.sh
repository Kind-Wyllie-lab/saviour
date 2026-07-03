#!/usr/bin/env bash
# Ping all known module IPs and report UP/DOWN
# Run from habitat-controller: bash tools/ping_sweep.sh

declare -A NAMES=(
  [192.168.1.196]="camera_d443  (A1)"
  [192.168.1.129]="camera_d540  (A2)"
  [192.168.1.198]="camera_d586  (A3)"
  [192.168.1.130]="camera_98b7  (A4)"
  [192.168.1.172]="camera_d62d  (B1)"
  [192.168.1.137]="camera_d549  (B2)"
  [192.168.1.146]="camera_9d0d  (B3)"
  [192.168.1.154]="camera_3533  (B4)"
  [192.168.1.169]="camera_d165  (C1)"
  [192.168.1.201]="camera_d589  (C2)"
  [192.168.1.170]="camera_d569  (C3)"
  [192.168.1.232]="camera_33ff  (C4)"
  [192.168.1.210]="camera_34aa  (D1)"
  [192.168.1.148]="camera_34ec  (D2)"
  [192.168.1.179]="camera_340b  (D3)"
  [192.168.1.133]="camera_a2d2  (D4)"
  [192.168.1.185]="microphone_9af1 (Col_1)"
  [192.168.1.141]="microphone_9ec9 (Col_2)"
  [192.168.1.167]="microphone_999e (Col_3)"
  [192.168.1.149]="microphone_9acd (Col_4)"
)

UP=0; DOWN=0
for ip in $(echo "${!NAMES[@]}" | tr ' ' '\n' | sort -t. -k4 -n); do
  if ping -c 1 -W 1 "$ip" &>/dev/null; then
    echo "  UP   $ip  ${NAMES[$ip]}"
    ((UP++))
  else
    echo "  DOWN $ip  ${NAMES[$ip]}"
    ((DOWN++))
  fi
done

echo ""
echo "  $UP up, $DOWN down (of $((UP+DOWN)) total)"
