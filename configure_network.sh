#!/usr/bin/env bash
# Configure/reconfigure network

check_for_gateway() {
    # If the local network has a device at 192.168.x.2 or 10.0.x.2, SAVIOUR will assume this is gateway and configure it's own IP to match.
    GATEWAY=""
    MAX=4
    for ((i=1; i<=MAX; i++)); do
        PERCENT=$(echo "scale=2 ; $i / $MAX * 100" | bc);
        echo -ne "Checking 10.0.$i.2                            $PERCENT% Complete\r"
        if ping -c 1 -w 1 10.0.$i.2 >/dev/null 2>&1 ; then
            echo -ne "Gateway found: 10.0.$i.2                                    \r" 
            GATEWAY="10.0.$i.2"
            break 
        fi
        echo -ne "Checking 192.168.$i.2                         $PERCENT% Complete\r"
        if ping -c 1 -w 1 192.168.$i.2 >/dev/null 2>&1; then 
            echo -ne "Gateway found: 192.168.$i.2                                 \r"
            GATEWAY="192.168.$i.2"
            break
        fi
        echo -ne "No gateway found.                                               \r"
    done
}

set_own_ip() {
    if [ -n "$GATEWAY" ]; then
        # Extract subnet from gateway IP
        IFS='.' read -r a b c d <<< "$GATEWAY"
    else
        a="10"
        b="0"
        c="0"
    fi
    DEVICE_IP="$a.$b.$c.1/16" # Set controller IP
    echo "Will configure IP as: $DEVICE_IP on $INTERFACE, default gateway: $GATEWAY"
    read -p "Override IP settings? (y/n): " choice

    if [ "$choice" = "y" ] || [ "$choice" = "Y" ]; then
        read -p "Enter the desired IP address (e.g., 10.0.3.1/16): " DEVICE_IP
        read -p "Enter the desired gateway (e.g., 10.0.3.2): " GATEWAY

        IFS='.' read -r a b c d <<< "$DEVICE_IP"
    fi

    if [ -n "$GATEWAY" ]; then
        sudo nmcli connection modify "$INTERFACE" ipv4.addresses $DEVICE_IP ipv4.gateway $GATEWAY ipv4.dns "8.8.8.8,1.1.1.1" ipv4.method manual 
    else
        sudo nmcli connection modify "$INTERFACE" ipv4.addresses $DEVICE_IP ipv4.dns "8.8.8.8,1.1.1.1" ipv4.method manual 
    fi

    echo "IP set."
    sudo nmcli conn show "$INTERFACE" | grep IP4
}

detect_interface_name() {
    INTERFACE=$(nmcli -t -f GENERAL.CONNECTION device show eth0 | cut -d: -f2-)
}


detect_interface_name
echo "eth0 is named $INTERFACE"


read -p "Attempt to autoconfigure IP based on gateway? (y/n): " choice

    if [ "$choice" = "y" ] || [ "$choice" = "Y" ]; then
        check_for_gateway
    else
        gateway=""
    fi

set_own_ip