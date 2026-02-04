#!/usr/bin/env bash
# Play a wav file using aplay and the hifiberry hat

set -e

DEFAULT_DUR=1;
DEFAULT_VOL=1;


if [ $# -eq 0 ]; then
  echo "Usage: $0 <file.wav> <seconds> <volume> (0.0-1.0)";
  exit 1;
fi


FILE="$1";
DUR="${2:-$DEFAULT_DUR}";
VOL="${3:-$DEFAULT_VOL}";

# TODO: Add support for specifying channel
# CHANNEL="$4";

echo "Playing $FILE for ${DUR}s at $(awk "BEGIN {printf \"%.0f\", $VOL*100}")% volume";

ffmpeg -hide_banner -loglevel error -t "$DUR" -i $FILE -filter:a "volume=$VOL" -f wav - | aplay -D plughw:2,0;

