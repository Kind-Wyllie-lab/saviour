#!/usr/bin/env bash
# Play a wav file using aplay and the hifiberry hat

if [ $# -eq 0 ]; then
  echo "Usage: $0 <file.wav> <seconds> <volume> (0.0-1.0)";
  kill -INT $$;
fi


FILE="$1";
DUR="$2";
VOL="$3";

echo "Playing $FILE for ${DUR}s at $(awk "BEGIN {printf \"%.0f\", $VOL*100}")% volume";

ffmpeg -hide_banner -loglevel error -t "$DUR" -i $FILE -filter:a "volume=$VOL" -f wav - | aplay -D plughw:2,0;