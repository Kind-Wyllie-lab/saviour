#!/bin/bash
# uninstall.sh
# Uninstall Saviour system and clean up all configurations
# Usage: bash uninstall.sh

set -e # If any function throws an error (doesn't return 0), exit immediately.

# Setup logging
LOG_FILE="saviour_uninstall.log"
SUMMARY_FILE="saviour_uninstall_summary.txt"

# Function to log messages
log_message() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $1" | tee -a "$LOG_FILE"
}

# Function to log section headers
log_section() {
    log_message "=== $1 ==="
}

# Function to save summary
save_summary() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    cat > "$SUMMARY_FILE" <<EOF
Saviour Uninstall Summary
Generated: $timestamp

$1

EOF
    log_message "Summary saved to: $SUMMARY_FILE"
}

# Function to auto-detect device role and module type
detect_device_configuration() {
    log_section "Auto-Detecting Device Configuration"
    
    # Check for controller service
    if systemctl list-unit-files | grep -q "saviour-controller-service"; then
        DEVICE_ROLE="controller"
        log_message "Detected CONTROLLER device (found saviour-controller-service)"
        echo "Detected CONTROLLER device (found saviour-controller-service)"
    # Check for module services
    elif systemctl list-unit-files | grep -q "saviour-camera-module"; then
        DEVICE_ROLE="module"
        MODULE_TYPE="camera"
        log_message "Detected CAMERA MODULE device (found saviour-camera-module)"
        echo "Detected CAMERA MODULE device (found saviour-camera-module)"
    elif systemctl list-unit-files | grep -q "saviour-microphone-module"; then
        DEVICE_ROLE="module"
        MODULE_TYPE="microphone"
        log_message "Detected MICROPHONE MODULE device (found saviour-microphone-module)"
        echo "Detected MICROPHONE MODULE device (found saviour-microphone-module)"
    elif systemctl list-unit-files | grep -q "saviour-arduino-module"; then
        DEVICE_ROLE="module"
        MODULE_TYPE="arduino"
        log_message "Detected ARDUINO MODULE device (found saviour-arduino-module)"
        echo "Detected ARDUINO MODULE device (found saviour-arduino-module)"
    else
        # Fallback to manual selection if no services found
        log_message "No Saviour services detected, falling back to manual selection"
        echo "No Saviour services detected. Please specify the role manually:"
        echo "1) Controller - Master device that coordinates other modules"
        echo "2) Module - Slave device that connects to a controller"
        echo ""
        
        while true; do
            read -p "Enter your choice (1 or 2): " choice
            case $choice in
                1)
                    DEVICE_ROLE="controller"
                    log_message "User specified CONTROLLER"
                    echo "Device configured as CONTROLLER"
                    break
                    ;;
                2)
                    DEVICE_ROLE="module"
                    log_message "User specified MODULE"
                    echo "Device configured as MODULE"
                    break
                    ;;
                *)
                    echo "Invalid choice. Please enter 1 or 2."
                    ;;
            esac
        done
    fi
}

# Function to ask user about module type (fallback only)
ask_module_type() {
    if [ "$DEVICE_ROLE" = "module" ] && [ -z "$MODULE_TYPE" ]; then
        log_section "Module Type Configuration"
        echo "Please specify the type of module that was configured:"
        echo "1) Camera - Video recording and streaming module"
        echo "2) Microphone - Audio recording module"
        echo "3) Arduino - Hardware control module"
        echo ""
        
        while true; do
            read -p "Enter your choice (1-3): " choice
            case $choice in
                1)
                    MODULE_TYPE="camera"
                    log_message "Module type was configured as CAMERA"
                    echo "Module type was configured as CAMERA"
                    break
                    ;;
                2)
                    MODULE_TYPE="microphone"
                    log_message "Module type was configured as MICROPHONE"
                    echo "Module type was configured as MICROPHONE"
                    break
                    ;;
                3)
                    MODULE_TYPE="arduino"
                    log_message "Module type was configured as ARDUINO"
                    echo "Module type was configured as ARDUINO"
                    break
                    ;;
                *)
                    echo "Invalid choice. Please enter 1-3."
                    ;;
            esac
        done
    fi
}

# Function to stop and disable Saviour systemd services
uninstall_saviour_services() {
    log_section "Stopping and Disabling Saviour Services"
    
    # Stop and disable controller service
    if systemctl is-active --quiet saviour-controller-service 2>/dev/null; then
        log_message "Stopping saviour-controller-service..."
        sudo systemctl stop saviour-controller-service
        echo "Stopped saviour-controller-service"
    fi
    
    if systemctl is-enabled --quiet saviour-controller-service 2>/dev/null; then
        log_message "Disabling saviour-controller-service..."
        sudo systemctl disable saviour-controller-service
        echo "Disabled saviour-controller-service"
    fi
    
    # Stop and disable module services
    if [ "$DEVICE_ROLE" = "module" ]; then
        if systemctl is-active --quiet saviour-${MODULE_TYPE}-module 2>/dev/null; then
            log_message "Stopping saviour-${MODULE_TYPE}-module..."
            sudo systemctl stop saviour-${MODULE_TYPE}-module
            echo "Stopped saviour-${MODULE_TYPE}-module"
        fi
        
        if systemctl is-enabled --quiet saviour-${MODULE_TYPE}-module 2>/dev/null; then
            log_message "Disabling saviour-${MODULE_TYPE}-module..."
            sudo systemctl disable saviour-${MODULE_TYPE}-module
            echo "Disabled saviour-${MODULE_TYPE}-module"
        fi
    fi
    
    # Remove service files
    if [ -f /etc/systemd/system/saviour-controller-service.service ]; then
        log_message "Removing saviour-controller-service.service..."
        sudo rm /etc/systemd/system/saviour-controller-service.service
        echo "Removed saviour-controller-service.service"
    fi
    
    if [ "$DEVICE_ROLE" = "module" ] && [ -f /etc/systemd/system/saviour-${MODULE_TYPE}-module.service ]; then
        log_message "Removing saviour-${MODULE_TYPE}-module.service..."
        sudo rm /etc/systemd/system/saviour-${MODULE_TYPE}-module.service
        echo "Removed saviour-${MODULE_TYPE}-module.service"
    fi
    
    # Reload systemd
    sudo systemctl daemon-reload
    log_message "Saviour services uninstalled"
}

# Function to uninstall PTP services
uninstall_ptp_services() {
    log_section "Uninstalling PTP Services"
    
    # Stop PTP services
    if systemctl is-active --quiet ptp4l 2>/dev/null; then
        log_message "Stopping ptp4l..."
        sudo systemctl stop ptp4l
        echo "Stopped ptp4l"
    fi
    
    if systemctl is-active --quiet phc2sys 2>/dev/null; then
        log_message "Stopping phc2sys..."
        sudo systemctl stop phc2sys
        echo "Stopped phc2sys"
    fi
    
    # Disable PTP services
    if systemctl is-enabled --quiet ptp4l 2>/dev/null; then
        log_message "Disabling ptp4l..."
        sudo systemctl disable ptp4l
        echo "Disabled ptp4l"
    fi
    
    if systemctl is-enabled --quiet phc2sys 2>/dev/null; then
        log_message "Disabling phc2sys..."
        sudo systemctl disable phc2sys
        echo "Disabled phc2sys"
    fi
    
    # Remove PTP service files
    if [ -f /etc/systemd/system/ptp4l.service ]; then
        log_message "Removing ptp4l.service..."
        sudo rm /etc/systemd/system/ptp4l.service
        echo "Removed ptp4l.service"
    fi
    
    if [ -f /etc/systemd/system/phc2sys.service ]; then
        log_message "Removing phc2sys.service..."
        sudo rm /etc/systemd/system/phc2sys.service
        echo "Removed phc2sys.service"
    fi
    
    # Reload systemd
    sudo systemctl daemon-reload
    log_message "PTP services uninstalled"
}

# Function to restore NTP configuration
restore_ntp_configuration() {
    log_section "Restoring NTP Configuration"
    
    # Stop timesyncd
    if systemctl is-active --quiet systemd-timesyncd 2>/dev/null; then
        log_message "Stopping systemd-timesyncd..."
        sudo systemctl stop systemd-timesyncd
        echo "Stopped systemd-timesyncd"
    fi
    
    # Restore original timesyncd config if backup exists
    if [ -f /etc/systemd/timesyncd.conf.backup ]; then
        log_message "Restoring original timesyncd.conf..."
        sudo cp /etc/systemd/timesyncd.conf.backup /etc/systemd/timesyncd.conf
        echo "Restored original timesyncd.conf"
    else
        log_message "No timesyncd.conf.backup found, creating default configuration..."
        sudo tee /etc/systemd/timesyncd.conf > /dev/null <<EOF
[Time]
NTP=
FallbackNTP=time.nist.gov time.google.com pool.ntp.org
EOF
        echo "Created default timesyncd.conf"
    fi
    
    # Restart timesyncd
    sudo systemctl restart systemd-timesyncd
    log_message "NTP configuration restored"
}

# Function to uninstall Samba share
uninstall_samba_share() {
    if [ "$DEVICE_ROLE" = "controller" ]; then
        log_section "Uninstalling Samba Share"
        
        # Stop Samba services
        if systemctl is-active --quiet smbd 2>/dev/null; then
            log_message "Stopping smbd..."
            sudo systemctl stop smbd
            echo "Stopped smbd"
        fi
        
        if systemctl is-active --quiet nmbd 2>/dev/null; then
            log_message "Stopping nmbd..."
            sudo systemctl stop nmbd
            echo "Stopped nmbd"
        fi
        
        # Disable Samba services
        if systemctl is-enabled --quiet smbd 2>/dev/null; then
            log_message "Disabling smbd..."
            sudo systemctl disable smbd
            echo "Disabled smbd"
        fi
        
        if systemctl is-enabled --quiet nmbd 2>/dev/null; then
            log_message "Disabling nmbd..."
            sudo systemctl disable nmbd
            echo "Disabled nmbd"
        fi
        
        # Restore original samba config if backup exists
        if [ -f /etc/samba/smb.conf.backup ]; then
            log_message "Restoring original smb.conf..."
            sudo cp /etc/samba/smb.conf.backup /etc/samba/smb.conf
            echo "Restored original smb.conf"
        else
            log_message "No smb.conf.backup found, creating default configuration..."
            sudo tee /etc/samba/smb.conf > /dev/null <<EOF
[global]
   workgroup = WORKGROUP
   server string = %h server (Samba, Ubuntu)
   server role = standalone server
   map to guest = bad user
   dns proxy = no
   log level = 1
   log file = /var/log/samba/%m.log
   max log size = 50
EOF
            echo "Created default smb.conf"
        fi
        
        # Remove controller share directory
        if [ -d /home/pi/controller_share ]; then
            log_message "Removing controller_share directory..."
            sudo rm -rf /home/pi/controller_share
            echo "Removed controller_share directory"
        fi
        
        log_message "Samba share uninstalled"
    fi
}

# Function to uninstall DHCP server
uninstall_dhcp_server() {
    if [ "$DEVICE_ROLE" = "controller" ]; then
        log_section "Uninstalling DHCP Server"
        
        # Stop dnsmasq
        if systemctl is-active --quiet dnsmasq 2>/dev/null; then
            log_message "Stopping dnsmasq..."
            sudo systemctl stop dnsmasq
            echo "Stopped dnsmasq"
        fi
        
        # Disable dnsmasq
        if systemctl is-enabled --quiet dnsmasq 2>/dev/null; then
            log_message "Disabling dnsmasq..."
            sudo systemctl disable dnsmasq
            echo "Disabled dnsmasq"
        fi
        
        # Restore original dnsmasq config if backup exists
        if [ -f /etc/dnsmasq.conf.backup ]; then
            log_message "Restoring original dnsmasq.conf..."
            sudo cp /etc/dnsmasq.conf.backup /etc/dnsmasq.conf
            echo "Restored original dnsmasq.conf"
        else
            log_message "No dnsmasq.conf.backup found, creating default configuration..."
            sudo tee /etc/dnsmasq.conf > /dev/null <<EOF
# Configuration file for dnsmasq.
# The format is one option per line, legal options are the same
# as the long options legal on the command line. See
# "/usr/sbin/dnsmasq --help" or "/usr/sbin/dnsmasq -H" for
# legal options.

# This file gets rewritten by resolvconf when DNS servers are configured
# via DHCP, so it should not be edited manually.
EOF
            echo "Created default dnsmasq.conf"
        fi
        
        # Restore original dnsmasq service if backup exists
        if [ -f /lib/systemd/system/dnsmasq.service.backup ]; then
            log_message "Restoring original dnsmasq.service..."
            sudo cp /lib/systemd/system/dnsmasq.service.backup /lib/systemd/system/dnsmasq.service
            echo "Restored original dnsmasq.service"
        fi
        
        # Remove service override
        if [ -d /etc/systemd/system/dnsmasq.service.d ]; then
            log_message "Removing dnsmasq service override..."
            sudo rm -rf /etc/systemd/system/dnsmasq.service.d
            echo "Removed dnsmasq service override"
        fi
        
        # Reload systemd
        sudo systemctl daemon-reload
        log_message "DHCP server uninstalled"
    fi
}

# Function to remove system packages (optional)
remove_system_packages() {
    log_section "Removing System Packages"
    
    echo "Do you want to remove the system packages that were installed? (y/n)"
    echo "This will remove: linuxptp, ffmpeg, libavcodec-extra, python3-picamera2, etc."
    read -p "Remove system packages? (y/n): " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log_message "User chose to remove system packages"
        
        # List of packages to remove
        PACKAGES_TO_REMOVE=(
            linuxptp
            ffmpeg
            samba
            samba-common-bin
            cifs-utils
            dnsmasq
        )
        
        echo "Removing system packages..."
        for pkg in "${PACKAGES_TO_REMOVE[@]}"; do
            if dpkg -s "$pkg" &> /dev/null; then
                log_message "Removing $pkg..."
                sudo apt-get remove --purge -y "$pkg"
                echo "Removed $pkg"
            else
                log_message "[SKIP] $pkg is not installed"
                echo "[SKIP] $pkg is not installed"
            fi
        done
        
        # Clean up any remaining dependencies
        sudo apt-get autoremove -y
        sudo apt-get autoclean
        
        log_message "System packages removed"
    else
        log_message "User chose to keep system packages"
        echo "System packages will be kept"
    fi
}

# Function to remove virtual environment
remove_virtual_environment() {
    log_section "Removing Virtual Environment"
    
    if [ -d "env" ]; then
        log_message "Removing virtual environment..."
        sudo rm -rf env
        echo "Removed virtual environment"
    else
        log_message "No virtual environment found"
        echo "No virtual environment found"
    fi
}

# Function to remove camera configuration
remove_camera_configuration() {
    log_section "Removing Camera Configuration"
    
    # Remove camera_auto_detect=1 from /boot/config.txt if it exists
    if grep -q "camera_auto_detect=1" /boot/config.txt; then
        log_message "Removing camera_auto_detect=1 from /boot/config.txt..."
        sudo sed -i '/camera_auto_detect=1/d' /boot/config.txt
        echo "Removed camera_auto_detect=1 from /boot/config.txt"
    else
        log_message "No camera_auto_detect=1 found in /boot/config.txt"
        echo "No camera_auto_detect=1 found in /boot/config.txt"
    fi
    
    log_message "Camera configuration removed"
}

# Function to clean up log files
cleanup_log_files() {
    log_section "Cleaning Up Log Files"
    
    # Remove Saviour-related log files
    SAVIOUR_LOG_FILES=(
        "saviour_setup.log"
        "saviour_setup_summary.txt"
        "saviour_uninstall.log"
        "saviour_uninstall_summary.txt"
    )
    
    for log_file in "${SAVIOUR_LOG_FILES[@]}"; do
        if [ -f "$log_file" ]; then
            log_message "Removing $log_file..."
            rm "$log_file"
            echo "Removed $log_file"
        fi
    done
    
    log_message "Log files cleaned up"
}

# Initialize logging
log_section "Saviour Uninstall Started"
log_message "Uninstall script version: $(date '+%Y-%m-%d %H:%M:%S')"
log_message "System: $(uname -a)"
log_message "User: $(whoami)"

# Auto-detect device configuration
detect_device_configuration

# Ask user about module type if this is a module and type wasn't auto-detected
ask_module_type

# Stop and disable Saviour systemd services
uninstall_saviour_services

# Uninstall PTP services
uninstall_ptp_services

# Restore NTP configuration
restore_ntp_configuration

# Uninstall Samba share (controllers only)
uninstall_samba_share

# Uninstall DHCP server (controllers only)
uninstall_dhcp_server

# Remove camera configuration
remove_camera_configuration

# Remove virtual environment
remove_virtual_environment

# Remove system packages (optional)
remove_system_packages

# Clean up log files
cleanup_log_files

# Generate summary content
SUMMARY_CONTENT=""

if [ "$DEVICE_ROLE" = "controller" ]; then
    SUMMARY_CONTENT="Device Role: CONTROLLER (was configured)

=== Services Removed ===
- saviour-controller-service: Stopped, disabled, and removed
- ptp4l: Stopped, disabled, and removed
- phc2sys: Stopped, disabled, and removed
- smbd/nmbd: Stopped, disabled, and restored to defaults
- dnsmasq: Stopped, disabled, and restored to defaults

=== Configuration Restored ===
- NTP: Restored to default configuration
- Samba: Restored to default configuration
- DHCP: Restored to default configuration
- Camera: Removed camera_auto_detect=1 from /boot/config.txt

=== Files Removed ===
- /etc/systemd/system/saviour-controller-service.service
- /etc/systemd/system/ptp4l.service
- /etc/systemd/system/phc2sys.service
- /home/pi/controller_share (directory)
- Virtual environment (env/)
- Log files (saviour_setup.log, etc.)"
else
    SUMMARY_CONTENT="Device Role: MODULE (was configured)
Module Type: ${MODULE_TYPE^^}

=== Services Removed ===
- saviour-${MODULE_TYPE}-module: Stopped, disabled, and removed
- ptp4l: Stopped, disabled, and removed
- phc2sys: Stopped, disabled, and removed

=== Configuration Restored ===
- NTP: Restored to default configuration
- Camera: Removed camera_auto_detect=1 from /boot/config.txt

=== Files Removed ===
- /etc/systemd/system/saviour-${MODULE_TYPE}-module.service
- /etc/systemd/system/ptp4l.service
- /etc/systemd/system/phc2sys.service
- Virtual environment (env/)
- Log files (saviour_setup.log, etc.)"
fi

SUMMARY_CONTENT="$SUMMARY_CONTENT

=== Next Steps ===
1. The system is now clean and ready for APA system installation
2. Run: bash setup.sh to install APA system
3. Reboot may be required for some changes to take effect

=== Manual Cleanup (if needed) ===
- Check for any remaining Saviour-related files in /usr/local/src/saviour/
- Remove any remaining log files or configuration backups
- Check /var/log/ for any Saviour-related log files"

# Save summary to file
save_summary "$SUMMARY_CONTENT"

log_section "Uninstall Complete"
log_message "Saviour system uninstalled successfully"

# Display summary
echo "=== Uninstall Complete! ==="
echo "Saviour system has been successfully uninstalled."
echo ""
echo "Uninstall log saved to: $LOG_FILE"
echo "Summary saved to: $SUMMARY_FILE"
echo ""

if [ "$DEVICE_ROLE" = "controller" ]; then
    echo "=== Controller Uninstall Summary ==="
    echo "Device Role: CONTROLLER (was configured)"
    echo ""
    echo "=== Services Removed ==="
    echo "- saviour-controller-service: Stopped, disabled, and removed"
    echo "- ptp4l: Stopped, disabled, and removed"
    echo "- phc2sys: Stopped, disabled, and removed"
    echo "- smbd/nmbd: Stopped, disabled, and restored to defaults"
    echo "- dnsmasq: Stopped, disabled, and restored to defaults"
    echo ""
    echo "=== Configuration Restored ==="
    echo "- NTP: Restored to default configuration"
    echo "- Samba: Restored to default configuration"
    echo "- DHCP: Restored to default configuration"
    echo "- Camera: Removed camera_auto_detect=1 from /boot/config.txt"
    echo ""
    echo "=== Files Removed ==="
    echo "- /etc/systemd/system/saviour-controller-service.service"
    echo "- /etc/systemd/system/ptp4l.service"
    echo "- /etc/systemd/system/phc2sys.service"
    echo "- /home/pi/controller_share (directory)"
    echo "- Virtual environment (env/)"
    echo "- Log files (saviour_setup.log, etc.)"
else
    echo "=== Module Uninstall Summary ==="
    echo "Device Role: MODULE (was configured)"
    echo "Module Type: ${MODULE_TYPE^^}"
    echo ""
    echo "=== Services Removed ==="
    echo "- saviour-${MODULE_TYPE}-module: Stopped, disabled, and removed"
    echo "- ptp4l: Stopped, disabled, and removed"
    echo "- phc2sys: Stopped, disabled, and removed"
    echo ""
    echo "=== Configuration Restored ==="
    echo "- NTP: Restored to default configuration"
    echo "- Camera: Removed camera_auto_detect=1 from /boot/config.txt"
    echo ""
    echo "=== Files Removed ==="
    echo "- /etc/systemd/system/saviour-${MODULE_TYPE}-module.service"
    echo "- /etc/systemd/system/ptp4l.service"
    echo "- /etc/systemd/system/phc2sys.service"
    echo "- Virtual environment (env/)"
    echo "- Log files (saviour_setup.log, etc.)"
fi

echo ""
echo "=== Next Steps ==="
echo "1. The system is now clean and ready for APA system installation"
echo "2. Run: bash setup.sh to install APA system"
echo "3. Reboot may be required for some changes to take effect"
echo ""
echo "=== Manual Cleanup (if needed) ==="
echo "- Check for any remaining Saviour-related files in /usr/local/src/saviour/"
echo "- Remove any remaining log files or configuration backups"
echo "- Check /var/log/ for any Saviour-related log files"

log_section "Uninstall Script Completed"
log_message "Uninstall script finished successfully"

read -p "Would you like to reboot now (recommended)? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]
then
    log_section "Rebooting"
    log_message "User chose to reboot - rebooting now."
    sudo reboot
fi 