# Remote Samba Share Setup

How to replicate the SAVIOUR controller share on a separate server (e.g. `10.0.0.2`) so modules write recordings there instead of to the controller Pi.

## On the remote server (10.0.0.2)

```bash
# Install Samba
sudo apt-get update && sudo apt-get install -y samba

# Create the saviour group and module user
sudo groupadd saviour
sudo useradd --system --no-create-home --shell /usr/sbin/nologin \
    --gid saviour saviour_module

# Add your admin account to the saviour group
sudo usermod -aG saviour $USER

# Create the share directory with the same permission model as the controller:
#   owner pi:saviour, mode 1775 (group-write + sticky bit)
sudo mkdir -p /srv/saviour/controller_share
sudo chown $USER:saviour /srv/saviour/controller_share
sudo chmod 1775 /srv/saviour/controller_share

# Generate a password for saviour_module (keep a copy — you'll need it for modules)
MODULE_PASS=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 20)
echo "saviour_module password: ${MODULE_PASS}"

# Write smb.conf
sudo tee /etc/samba/smb.conf > /dev/null <<EOF
[global]
   workgroup = WORKGROUP
   server string = SAVIOUR Remote Share
   server role = standalone server
   map to guest = bad user
   log level = 1

[controller_share]
   comment = SAVIOUR Controller Share
   path = /srv/saviour/controller_share
   browseable = yes
   guest ok = yes
   read only = yes
   write list = saviour_module, $USER
   admin users = $USER
   create mask = 0664
   directory mask = 0775
EOF

# Set Samba passwords
printf '%s\n%s\n' "${MODULE_PASS}" "${MODULE_PASS}" | sudo smbpasswd -s -a saviour_module
# Replace 'yourpassword' with your own admin account password
echo -e "yourpassword\nyourpassword" | sudo smbpasswd -s -a $USER

# Start Samba
sudo systemctl restart smbd nmbd
sudo systemctl enable smbd nmbd
```

## On each SAVIOUR module

Edit `/var/lib/saviour/active_config.json` (or set via the controller GUI) to point at the remote server:

```json
{
  "export": {
    "share_ip": "10.0.0.2",
    "share_path": "controller_share",
    "share_username": "saviour_module",
    "share_password": "<the MODULE_PASS from above>"
  }
}
```

Or update `src/modules/config/base_config.json` before flashing so the default is already correct:

```bash
python3 - <<'EOF'
import json
path = "src/modules/config/base_config.json"
with open(path) as f:
    cfg = json.load(f)
cfg["export"]["share_ip"] = "10.0.0.2"
cfg["export"]["share_username"] = "saviour_module"
cfg["export"]["share_password"] = "<MODULE_PASS>"
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
EOF
```

## Verify from a module

```bash
# Mount manually to test
sudo mkdir -p /mnt/test
sudo mount -t cifs //10.0.0.2/controller_share /mnt/test \
    -o username=saviour_module,password=<MODULE_PASS>,uid=saviour_module,gid=saviour,file_mode=0664,dir_mode=0775

# Check you can write but not delete another user's file
touch /mnt/test/testfile
# (should succeed)
rm /mnt/test/testfile
# (should fail with EACCES from sticky bit if the file was written by a different uid)

sudo umount /mnt/test
```
