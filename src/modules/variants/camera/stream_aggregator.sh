#!/usr/bin/env bash
#
# Takes an MJPEG stream, copies it into a 4x4 grid, then starts an HLS stream in /tmp/hls_test
# To run, navigate to /tmp/hls_test and run python3 -m http.server 8090
set -euo pipefail

# URL of your MJPEG input
STREAM_URL="http://192.168.1.197:8080/video_feed"

# Output dir for HLS
OUTPUT_DIR="/tmp/hls_test"
mkdir -p "$OUTPUT_DIR"

# Tile size (downscaled for Pi 5)
TILE=250
GRID_COLS=4
GRID_ROWS=4

# Calculate final resolution
FINAL_WIDTH=$((TILE * GRID_COLS))
FINAL_HEIGHT=$((TILE * GRID_ROWS))

# Create layout string for xstack
layout=""
for r in $(seq 0 $((GRID_ROWS-1))); do
    for c in $(seq 0 $((GRID_COLS-1))); do
        x=$((c * TILE))
        y=$((r * TILE))
        if [ -z "$layout" ]; then
            layout="${x}_${y}"
        else
            layout="${layout}|${x}_${y}"
        fi
    done
done

echo "Grid resolution: ${FINAL_WIDTH}x${FINAL_HEIGHT}"
echo "Layout: $layout"

# Build filter_complex
filter_complex="scale=${TILE}:${TILE},split=16[v0][v1][v2][v3][v4][v5][v6][v7][v8][v9][v10][v11][v12][v13][v14][v15]; \
[v0][v1][v2][v3][v4][v5][v6][v7][v8][v9][v10][v11][v12][v13][v14][v15]xstack=inputs=16:layout=${layout}"

# Run FFmpeg (MJPEG live stream)
ffmpeg -re -i "$STREAM_URL" \
    -filter_complex "$filter_complex" \
    -c:v libx264 -preset ultrafast -crf 23 \
    -f hls \
    -hls_time 1 \
    -hls_list_size 5 \
    -hls_flags delete_segments \
    "$OUTPUT_DIR/stream.m3u8