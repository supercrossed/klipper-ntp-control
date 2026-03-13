# klipper-ntp-control

A Klipper extra that prevents MCU timing errors caused by NTP clock adjustments.

## The Problem

`systemd-timesyncd` (and other NTP clients) periodically adjust the kernel clock frequency. These adjustments cause **all MCU `adj` values to drift simultaneously** in Klipper's timing system. The MCU with the least timing slack — typically the busiest one, like a CAN-connected toolhead — errors out first with a "Timer too close" or TTC scheduling error.

This is a known but obscure issue, especially on:
- Raspberry Pi 5 (high-performance CPU, aggressive NTP sync)
- CAN bus toolheads (STM32G0B1 at 64MHz — busy, minimal slack)
- Kalico/Klipper setups with multiple MCUs

## The Fix

Disable NTP during prints. This extension automates that.

## Features

- **Auto-management** (default): NTP is disabled when a print starts and re-enabled when it ends/cancels/errors
- **Manual control**: `NTP_ENABLE`, `NTP_DISABLE`, `NTP_STATUS` gcode commands
- **Mainsail/Fluidd macros**: `NTP_ON`, `NTP_OFF`, `NTP_CHECK`, `NTP_TOGGLE` buttons in the macro panel
- **Status exposure**: `printer.ntp_control.ntp_active` available in macros and Moonraker API

## Installation

### Quick Install

```bash
cd klipper-ntp-control
bash install.sh
```

The installer will:
1. Copy `ntp_control.py` to `~/klipper/klippy/extras/`
2. Copy config files to `~/printer_data/config/`
3. Install a sudoers rule for passwordless `timedatectl`

### Manual Install

1. Copy the Klipper extra:
   ```bash
   cp ntp_control.py ~/klipper/klippy/extras/
   ```

2. Copy config files:
   ```bash
   cp ntp_control.cfg ntp_macros.cfg ~/printer_data/config/
   ```

3. Set up permissions (choose **one**):

   **Option A — sudoers (recommended):**
   ```bash
   # Edit the file to replace 'klipper' with your actual user if different
   sudo cp ntp-control-sudoers /etc/sudoers.d/ntp-control
   sudo chmod 0440 /etc/sudoers.d/ntp-control
   sudo visudo -cf /etc/sudoers.d/ntp-control  # validate syntax
   ```

   **Option B — polkit rule:**
   ```bash
   sudo cp ntp-control-polkit.rules /etc/polkit-1/rules.d/50-ntp-control.rules
   ```
   Then set `use_sudo: False` in your `[ntp_control]` config.

4. Add to `printer.cfg`:
   ```ini
   [include ntp_control.cfg]
   [include ntp_macros.cfg]
   ```

5. Restart Klipper:
   ```bash
   sudo systemctl restart klipper
   ```

## Configuration

In `ntp_control.cfg`:

```ini
[ntp_control]
# Auto-disable NTP on print start, re-enable on end (default: True)
auto_manage: True

# Use sudo for timedatectl commands (default: True)
# Set False if using the polkit rule instead of sudoers
use_sudo: True
```

## Gcode Commands

| Command | Description |
|---|---|
| `NTP_ENABLE` | Enable NTP synchronization |
| `NTP_DISABLE` | Disable NTP synchronization |
| `NTP_STATUS` | Print current NTP state to console |

## Mainsail/Fluidd Usage

The macros in `ntp_macros.cfg` appear as buttons in the Mainsail macro panel:

- **NTP_ON** / **NTP_OFF** — explicit enable/disable
- **NTP_CHECK** — query current status
- **NTP_TOGGLE** — flip the current state

> **Note:** Mainsail does not support custom toggle widgets without a fork. These macro buttons are the standard way to expose controls. With `auto_manage: True` (the default), most users won't need to touch the buttons at all — NTP is handled automatically around prints.

## How It Works

1. On Klipper startup, the extension queries `timedatectl` for current NTP state
2. When a print starts (`print_stats:printing` event), it runs `timedatectl set-ntp false`
3. When a print ends/cancels/errors, it runs `timedatectl set-ntp true`
4. Status is exposed via `get_status()` for Moonraker/Mainsail/Fluidd to query

## Verifying It Works

Check `klippy.log` for messages like:
```
ntp_control: NTP is currently active. It will be disabled automatically when a print starts.
ntp_control: Disabling NTP for print safety
ntp_control: NTP set to false
ntp_control: Re-enabling NTP after print
ntp_control: NTP set to true
```

In the Klipper console, run `NTP_STATUS` to see the current state.

## Background

For the full debugging story, see the related discussion. The root cause was identified by observing that **all MCU adj values drifted simultaneously** in `klippy.stats` whenever `systemd-timesyncd` made a clock correction — a signature that pointed to the host clock, not the MCUs themselves.

## License

GPL-2.0-or-later (matching Klipper's license)
