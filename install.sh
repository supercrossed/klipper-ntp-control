#!/usr/bin/env bash
# Installer for klipper-ntp-control
# Run as the user that manages Klipper (typically 'pi' or your login user).
# Usage: bash install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Paths — adjust if your Klipper install is non-standard
KLIPPER_EXTRAS="${KLIPPER_EXTRAS:-$HOME/klipper/klippy/extras}"
PRINTER_CONFIG="${PRINTER_CONFIG:-$HOME/printer_data/config}"
SUDOERS_DIR="/etc/sudoers.d"
KLIPPER_USER="${KLIPPER_USER:-$(whoami)}"

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$1"; }
error() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$1" >&2; exit 1; }

# --- Preflight checks ---

[[ -d "$KLIPPER_EXTRAS" ]] || error "Klipper extras dir not found at $KLIPPER_EXTRAS"
[[ -d "$PRINTER_CONFIG" ]] || error "Printer config dir not found at $PRINTER_CONFIG"

# --- Install Klipper extra ---

info "Installing ntp_control.py to $KLIPPER_EXTRAS/"
cp "$SCRIPT_DIR/ntp_control.py" "$KLIPPER_EXTRAS/ntp_control.py"

# --- Install config files ---

for cfg in ntp_control.cfg ntp_macros.cfg; do
    if [[ -f "$PRINTER_CONFIG/$cfg" ]]; then
        warn "$cfg already exists in $PRINTER_CONFIG — skipping (check for updates manually)"
    else
        info "Installing $cfg to $PRINTER_CONFIG/"
        cp "$SCRIPT_DIR/$cfg" "$PRINTER_CONFIG/$cfg"
    fi
done

# --- Install sudoers rule ---

info "Installing sudoers rule for passwordless timedatectl"
# Generate sudoers file with the correct username
SUDOERS_CONTENT="# Allow $KLIPPER_USER to control NTP via timedatectl without a password.
$KLIPPER_USER ALL=(ALL) NOPASSWD: /usr/bin/timedatectl set-ntp true
$KLIPPER_USER ALL=(ALL) NOPASSWD: /usr/bin/timedatectl set-ntp false
$KLIPPER_USER ALL=(ALL) NOPASSWD: /usr/bin/timedatectl show --property=NTP --value"

TMPFILE="$(mktemp)"
echo "$SUDOERS_CONTENT" > "$TMPFILE"
chmod 0440 "$TMPFILE"

# Validate before installing
if sudo visudo -cf "$TMPFILE"; then
    sudo cp "$TMPFILE" "$SUDOERS_DIR/ntp-control"
    sudo chmod 0440 "$SUDOERS_DIR/ntp-control"
    info "Sudoers rule installed at $SUDOERS_DIR/ntp-control"
else
    error "Sudoers syntax check failed — not installing"
fi
rm -f "$TMPFILE"

# --- Remind user to include configs ---

echo ""
info "Installation complete!"
echo ""
echo "  Add these lines to your printer.cfg if not already present:"
echo ""
echo "    [include ntp_control.cfg]"
echo "    [include ntp_macros.cfg]"
echo ""
echo "  Then restart Klipper:  sudo systemctl restart klipper"
echo ""
