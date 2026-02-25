#!/usr/env/bin bash
# Script to regenerate an SSH key, typically after an image has been cloned.

sudo rm -r /etc/ssh/ssh*key sudo dpkg-reconfigure openssh-server
 
sudo rm -r /etc/ssh/ssh*key 
sudo dpkg-reconfigure openssh-server
 