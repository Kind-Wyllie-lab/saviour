#!/usr/bin/env bash
# Convert a mono .wav file to stereo

if [ $# -eq 0 ]; then
  echo "Incorrect number of arguments provided";
  echo "Correct usage: ./mono_to_stereo input.wav output.wav";
  kill -INT $$;
fi

input_file=$1;
output_file=$2;

if [ -z $output_file ]; then
  $output_file={$1: -4} + "_formatted.wav";
fi

check_wav() {
  # Checks a file is a wav file
  file=$1;
  filetype=${file: -3};
  if [ $filetype != "wav" ]
  then
    echo "Bad filetype: $file";
    kill -INT $$;
  fi
}

convert_file() {
  ffmpeg -i $1 -ac 2 -ar 48000 $2;
}

echo input_file;
echo output_file;

check_wav $input_file;
check_wav $output_file;
convert_file $input_file $output_file;