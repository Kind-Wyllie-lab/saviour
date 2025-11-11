#!/usr/env/bin bash
DIR=`pwd`
#echo $DIR
LIST=`ls src/modules/examples/`

shopt -s nullglob
arr=(src/modules/examples/*)
for ((i=0; i<${#arr[@]}; i++)); do
  echo "${arr[$i]}"
done

