#!/usr/bin/env bash
# Installer for klipper-ttc-fix
# Run as the user that manages Klipper (typically 'pi' or your login user).
# Usage: bash install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Paths — adjust if your Klipper install is non-standard
KLIPPER_EXTRAS="${KLIPPER_EXTRAS:-$HOME/klipper/klippy/extras}"
PRINTER_CONFIG="${PRINTER_CONFIG:-$HOME/printer_data/config}"
SUDOERS_DIR="/etc/sudoers.d"
KLIPPER_USER="${KLIPPER_USER:-$(whoami)}"
WIFI_INTERFACE="${WIFI_INTERFACE:-wlan0}"

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$1"; }
error() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$1" >&2; exit 1; }

# --- Preflight checks ---

[[ -d "$KLIPPER_EXTRAS" ]] || error "Klipper extras dir not found at $KLIPPER_EXTRAS"
[[ -d "$PRINTER_CONFIG" ]] || error "Printer config dir not found at $PRINTER_CONFIG"

# --- Install Klipper extras ---

info "Installing ttc_fix.py to $KLIPPER_EXTRAS/"
cp "$SCRIPT_DIR/ttc_fix.py" "$KLIPPER_EXTRAS/ttc_fix.py"

# Also install the standalone ntp_control.py for users who only want NTP control
info "Installing ntp_control.py to $KLIPPER_EXTRAS/"
cp "$SCRIPT_DIR/ntp_control.py" "$KLIPPER_EXTRAS/ntp_control.py"

# --- Install config files ---

for cfg in ttc_fix.cfg ttc_macros.cfg; do
    if [[ -f "$PRINTER_CONFIG/$cfg" ]]; then
        warn "$cfg already exists in $PRINTER_CONFIG — skipping (check for updates manually)"
    else
        info "Installing $cfg to $PRINTER_CONFIG/"
        cp "$SCRIPT_DIR/$cfg" "$PRINTER_CONFIG/$cfg"
    fi
done

# --- Install sudoers rule ---

info "Installing sudoers rule for TTC fix commands"
# Generate sudoers file with the correct username and wifi interface
SUDOERS_CONTENT="# Allow $KLIPPER_USER to run TTC fix commands without a password.

# NTP control via timedatectl
$KLIPPER_USER ALL=(ALL) NOPASSWD: /usr/bin/timedatectl set-ntp true
$KLIPPER_USER ALL=(ALL) NOPASSWD: /usr/bin/timedatectl set-ntp false
$KLIPPER_USER ALL=(ALL) NOPASSWD: /usr/bin/timedatectl show --property=NTP --value

# Wi-Fi power save control via iw
$KLIPPER_USER ALL=(ALL) NOPASSWD: /usr/sbin/iw dev $WIFI_INTERFACE set power_save on
$KLIPPER_USER ALL=(ALL) NOPASSWD: /usr/sbin/iw dev $WIFI_INTERFACE set power_save off"

TMPFILE="$(mktemp)"
echo "$SUDOERS_CONTENT" > "$TMPFILE"
chmod 0440 "$TMPFILE"

# Validate before installing
if sudo visudo -cf "$TMPFILE"; then
    sudo cp "$TMPFILE" "$SUDOERS_DIR/ttc-fix"
    sudo chmod 0440 "$SUDOERS_DIR/ttc-fix"
    info "Sudoers rule installed at $SUDOERS_DIR/ttc-fix"
else
    error "Sudoers syntax check failed — not installing"
fi
rm -f "$TMPFILE"

# --- Clean up old ntp-control sudoers if present ---

if [[ -f "$SUDOERS_DIR/ntp-control" ]]; then
    info "Removing old ntp-control sudoers rule (superseded by ttc-fix)"
    sudo rm -f "$SUDOERS_DIR/ntp-control"
fi

# --- Remind user to include configs ---

echo ""
info "Installation complete!"
echo ""
echo "  Add these lines to your printer.cfg if not already present:"
echo ""
echo "    [include ttc_fix.cfg]"
echo "    [include ttc_macros.cfg]"
echo ""
echo "  If you were previously using ntp_control, replace:"
echo "    [include ntp_control.cfg]  ->  [include ttc_fix.cfg]"
echo "    [include ntp_macros.cfg]   ->  [include ttc_macros.cfg]"
echo ""
echo "  Then restart Klipper:  sudo systemctl restart klipper"
echo ""
