#!/usr/bin/env bash
#
# Take 16 videos numbered A1.mp4...D4.mp4 and aggregate them into a 4x4 tiled video


ROWS=(A B C D)
COLS=(1 2 3 4)
TILE=1080

inputs=()
layout=()

# Iterate through rows and columns
row_idx=0
for r in "${ROWS[@]}"; do
    col_idx=0
    for c in "${COLS[@]}"; do
        file="${r}${c}.mp4"
        inputs+=(-i "$file")

        x=$((col_idx * TILE))
        y=$((row_idx * TILE))
        layout+=("${x}_${y}")


        ((col_idx++))
     done
    ((row_idx++))
done

layout_str=$(IFS='|'; echo "${layout[*]}")

ffmpeg \
    "${inputs[@]}" \
    -filter_complex "xstack=inputs=16:layout=${layout_str}" \
    -c:v libx264 -preset fast -crf 18 \
    output_4x4.mp4
