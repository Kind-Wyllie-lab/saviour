import subprocess

process = subprocess.Popen(["sudo", "tcpdump", "-l", "-i", "eth0"])

#process = subprocess.Popen(["sudo", "tcpdump", "-l", "-i", "eth0", "|", "grep", "-e", "sync", "-e", "announce", "-e", "follow up", "-e", "delay"])
