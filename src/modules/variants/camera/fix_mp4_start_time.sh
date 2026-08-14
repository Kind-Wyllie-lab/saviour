#!/usr/bin/env bash
if [ $# -eq 0 ]
    then
        cat << EOF
No arguments provided to script!

usage: $ ./fix_mp4_start_time.sh input_video.mp4 output_video.mp4
EOF
        exit 1
fi
echo "Outputting $1 to $2"
ffmpeg -i $1 -map 0 -c copy -reset_timestamps 1 $2