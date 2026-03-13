# klipper-ttc-fix

A Klipper extra that prevents MCU TTC/scheduling errors by applying system-level timing fixes at startup.

## The Problem

Several host-level system behaviors cause Klipper MCU timing errors ("Timer too close", TTC scheduling errors). The MCU with the least timing slack — typically the busiest one, like a CAN-connected toolhead — errors out first.

**Known causes this extension fixes:**

| Cause | Why it breaks timing |
|---|---|
| **NTP clock slewing** | `systemd-timesyncd` makes periodic kernel clock frequency adjustments that cause all MCU `adj` values to drift simultaneously |
| **CPU frequency scaling** | Governor switching between power-save frequencies causes timing jitter |
| **USB autosuspend** | Linux power management momentarily suspends USB devices, causing communication gaps with USB-connected MCUs |
| **Wi-Fi power save** | Power save mode causes latency spikes that starve Klipper's timing loop |

This is especially common on:
- Raspberry Pi 5 (aggressive NTP sync, power management)
- CAN bus toolheads (STM32G0B1 at 64MHz — busy, minimal slack)
- Kalico/Klipper setups with multiple MCUs

## The Fix

All problematic settings are disabled when Klipper starts and restored when it shuts down. No timing window, no manual intervention needed.

## Features

- **NTP control**: Disables `timesyncd` clock slewing, re-enables on shutdown
- **CPU governor**: Forces `performance` mode on all cores, reverts on shutdown
- **USB autosuspend**: Disables autosuspend on all USB devices, reverts on shutdown
- **Wi-Fi power save**: Disables power save on the Wi-Fi interface, reverts on shutdown
- **Per-fix toggles**: Enable/disable each fix independently
- **Manual NTP control**: `NTP_ENABLE`, `NTP_DISABLE` gcode commands
- **Status reporting**: `TTC_STATUS` command and `printer.ttc_fix.*` for macros/Moonraker
- **Mainsail/Fluidd macros**: Buttons in the macro panel for manual control
- **Clean revert**: All changes are undone when Klipper shuts down

## Installation

### Quick Install

```bash
cd klipper-ttc-fix
bash install.sh
```

The installer will:
1. Copy `ttc_fix.py` to `~/klipper/klippy/extras/`
2. Copy config files to `~/printer_data/config/`
3. Install a sudoers rule for passwordless `timedatectl` and `iw`

### Manual Install

1. Copy the Klipper extra:
   ```bash
   cp ttc_fix.py ~/klipper/klippy/extras/
   ```

2. Copy config files:
   ```bash
   cp ttc_fix.cfg ttc_macros.cfg ~/printer_data/config/
   ```

3. Set up permissions:
   ```bash
   # Edit the file to replace 'klipper' with your actual user if different
   sudo cp ttc-fix-sudoers /etc/sudoers.d/ttc-fix
   sudo chmod 0440 /etc/sudoers.d/ttc-fix
   sudo visudo -cf /etc/sudoers.d/ttc-fix  # validate syntax
   ```

4. Add to `printer.cfg`:
   ```ini
   [include ttc_fix.cfg]
   [include ttc_macros.cfg]
   ```

5. Restart Klipper:
   ```bash
   sudo systemctl restart klipper
   ```

### Upgrading from ntp_control

If you were using the standalone `ntp_control` module:
1. Replace `[include ntp_control.cfg]` with `[include ttc_fix.cfg]` in `printer.cfg`
2. Replace `[include ntp_macros.cfg]` with `[include ttc_macros.cfg]`
3. The installer handles the rest (sudoers migration, etc.)

## Configuration

In `ttc_fix.cfg`:

```ini
[ttc_fix]
# --- NTP ---
manage_ntp: True          # Disable NTP clock slewing (default: True)
use_sudo: True            # Use sudo for timedatectl/iw (default: True)

# --- CPU Governor ---
manage_cpu_governor: True  # Force 'performance' governor (default: True)

# --- USB Autosuspend ---
manage_usb_autosuspend: True  # Disable USB autosuspend (default: True)

# --- Wi-Fi Power Save ---
manage_wifi_power_save: True  # Disable Wi-Fi power save (default: True)
wifi_interface: wlan0         # Wi-Fi interface name (default: wlan0)
```

Each fix can be individually disabled by setting it to `False`.

## Gcode Commands

| Command | Description |
|---|---|
| `TTC_STATUS` | Report status of all timing fixes |
| `NTP_ENABLE` | Enable NTP synchronization |
| `NTP_DISABLE` | Disable NTP synchronization |

## Mainsail/Fluidd Usage

The macros in `ttc_macros.cfg` appear as buttons in the Mainsail macro panel:

- **TTC_STATUS** — report all fix statuses
- **NTP_ON** / **NTP_OFF** — manual NTP enable/disable
- **NTP_TOGGLE** — flip NTP state

> **Note:** With default settings, all fixes are applied automatically at startup. These buttons are only needed for manual override.

## How It Works

1. **Klipper starts** (`klippy:ready`) — applies all enabled fixes:
   - Disables NTP via `timedatectl set-ntp false`
   - Sets CPU governor to `performance` via sysfs
   - Sets USB autosuspend to `-1` (disabled) via sysfs
   - Disables Wi-Fi power save via `iw dev wlan0 set power_save off`
2. **Klipper shuts down** (`klippy:disconnect`) — reverts all changes to original values
3. Status is exposed via `get_status()` for Moonraker/Mainsail/Fluidd

### Permissions

- **CPU governor** and **USB autosuspend** write to sysfs files. Klipper typically runs as a user that has write access to these, or you may need to add udev rules.
- **NTP** and **Wi-Fi** use external commands (`timedatectl`, `iw`) that need sudo. The sudoers file whitelists only the specific commands required.

## Verifying It Works

Run `TTC_STATUS` in the Klipper console, or check `klippy.log` for:
```
ttc_fix: Applying timing safety fixes
ttc_fix: Disabling NTP
ttc_fix: CPU governor /sys/.../scaling_governor: ondemand -> performance
ttc_fix: Disabled USB autosuspend on 3 device(s)
ttc_fix: Disabled Wi-Fi power save on wlan0
ttc_fix: All applicable fixes applied
```

## Standalone NTP-Only Module

If you only need NTP control (e.g., you've already handled the other fixes), the original `ntp_control.py` is still included. Use `[include ntp_control.cfg]` instead of `[include ttc_fix.cfg]`. Do not use both.

## Background

Originally identified on a RatRig printer running Kalico v2026.03.00 on a Pi 5. The root cause was `systemd-timesyncd` making periodic kernel clock frequency corrections — visible as simultaneous drift of all MCU `adj` values in `klippy.stats`. The NHK toolhead (64MHz STM32G0B1, busiest MCU, least slack) errored out first. Disabling NTP resolved it, and this extension automates that fix along with other known timing hazards.

## License

GPL-2.0-or-later (matching Klipper's license)
