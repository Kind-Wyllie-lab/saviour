#!/usr/bin/bash
echo "Target time for TTL pulse: " $1
echo "Output pin: " $2

# Set pin to output
pinctrl set $2 op
pinctrl set $2 dl
echo "Preliminary state: $(pinctrl get $2)"

echo "Waiting" $(($1 - $(date +%s))) "s"

while [ "$(date +%s)" -lt "$1" ]; do
        :
	#echo "Not ready yet"
        #sleep 1
done

echo "FIRING at $(date +%s)"
pinctrl set $2 dh
echo "Final state: $(pinctrl get $2)"
