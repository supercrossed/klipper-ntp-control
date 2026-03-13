# Klipper extra to prevent TTC/scheduling errors
#
# Applies system-level fixes at Klipper startup that eliminate common causes
# of MCU timing errors on SBC hosts (Raspberry Pi, etc.):
#   - NTP clock slewing (timesyncd frequency adjustments drift all MCU adj values)
#   - CPU frequency scaling (governor changes cause timing jitter)
#   - USB autosuspend (momentary device suspends cause communication gaps)
#   - Wi-Fi power save (latency spikes starve the timing loop)
#
# All fixes are applied on klippy:ready and reverted on klippy:disconnect
# so the system returns to normal when Klipper is not running.
#
# Copyright (C) 2026 Andrei
# SPDX-License-Identifier: GPL-2.0-or-later

import glob
import logging
import os
import subprocess
from typing import Any

TIMEDATECTL_PATH = "/usr/bin/timedatectl"
SUDO_PATH = "/usr/bin/sudo"
IW_PATH = "/usr/sbin/iw"

# sysfs paths for CPU governor
CPU_GOVERNOR_GLOB = "/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"

# sysfs paths for USB autosuspend (-1 = disabled, >0 = seconds before suspend)
USB_AUTOSUSPEND_GLOB = "/sys/bus/usb/devices/*/power/autosuspend"

logger = logging.getLogger(__name__)


class TTCFix:
    """Applies system-level fixes to prevent MCU TTC/scheduling errors."""

    def __init__(self, config: Any) -> None:
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")

        # Per-fix toggles (all enabled by default)
        self.manage_ntp: bool = config.getboolean("manage_ntp", True)
        self.manage_cpu_governor: bool = config.getboolean(
            "manage_cpu_governor", True
        )
        self.manage_usb_autosuspend: bool = config.getboolean(
            "manage_usb_autosuspend", True
        )
        self.manage_wifi_power_save: bool = config.getboolean(
            "manage_wifi_power_save", True
        )

        # NTP-specific config
        self.use_sudo: bool = config.getboolean("use_sudo", True)

        # Wi-Fi interface name
        self.wifi_interface: str = config.get("wifi_interface", "wlan0")

        # State tracking
        self._ntp_active: bool | None = None
        self._cpu_governor_original: dict[str, str] = {}
        self._usb_autosuspend_original: dict[str, str] = {}
        self._wifi_ps_original: bool | None = None

        # Register gcode commands
        self.gcode.register_command(
            "TTC_STATUS",
            self.cmd_TTC_STATUS,
            desc=self.cmd_TTC_STATUS_help,
        )
        self.gcode.register_command(
            "NTP_ENABLE",
            self.cmd_NTP_ENABLE,
            desc=self.cmd_NTP_ENABLE_help,
        )
        self.gcode.register_command(
            "NTP_DISABLE",
            self.cmd_NTP_DISABLE,
            desc=self.cmd_NTP_DISABLE_help,
        )

        # Always register lifecycle hooks — individual fixes check their own toggle
        self.printer.register_event_handler(
            "klippy:ready", self._handle_ready
        )
        self.printer.register_event_handler(
            "klippy:disconnect", self._handle_disconnect
        )

    # -- Gcode help strings --

    cmd_TTC_STATUS_help = "Report status of all TTC timing fixes"
    cmd_NTP_ENABLE_help = "Enable NTP time synchronization"
    cmd_NTP_DISABLE_help = "Disable NTP time synchronization"

    # -- Gcode command handlers --

    def cmd_TTC_STATUS(self, gcmd: Any) -> None:
        self._refresh_all_status()
        lines = ["TTC Fix Status:"]

        # NTP
        if self.manage_ntp:
            if self._ntp_active is None:
                lines.append("  NTP: unknown (query failed)")
            elif self._ntp_active:
                lines.append("  NTP: ACTIVE (risk)")
            else:
                lines.append("  NTP: disabled (safe)")
        else:
            lines.append("  NTP: not managed")

        # CPU governor
        if self.manage_cpu_governor:
            governors = self._read_cpu_governors()
            unique = set(governors.values())
            if not governors:
                lines.append("  CPU governor: not available")
            elif unique == {"performance"}:
                lines.append("  CPU governor: performance (safe)")
            else:
                lines.append(
                    "  CPU governor: %s (risk)" % ", ".join(sorted(unique))
                )
        else:
            lines.append("  CPU governor: not managed")

        # USB autosuspend
        if self.manage_usb_autosuspend:
            usb_values = self._read_usb_autosuspend()
            if not usb_values:
                lines.append("  USB autosuspend: no devices found")
            else:
                enabled = [
                    p for p, v in usb_values.items()
                    if v.strip() not in ("-1", "0")
                ]
                if enabled:
                    lines.append(
                        "  USB autosuspend: ACTIVE on %d device(s) (risk)"
                        % len(enabled)
                    )
                else:
                    lines.append("  USB autosuspend: disabled (safe)")
        else:
            lines.append("  USB autosuspend: not managed")

        # Wi-Fi power save
        if self.manage_wifi_power_save:
            ps_active = self._read_wifi_power_save()
            if ps_active is None:
                lines.append("  Wi-Fi power save: not available")
            elif ps_active:
                lines.append("  Wi-Fi power save: ACTIVE (risk)")
            else:
                lines.append("  Wi-Fi power save: disabled (safe)")
        else:
            lines.append("  Wi-Fi power save: not managed")

        gcmd.respond_info("\n".join(lines))

    def cmd_NTP_ENABLE(self, gcmd: Any) -> None:
        if self._set_ntp(True):
            gcmd.respond_info("NTP synchronization enabled")
        else:
            gcmd.respond_info("ERROR: Failed to enable NTP synchronization")

    def cmd_NTP_DISABLE(self, gcmd: Any) -> None:
        if self._set_ntp(False):
            gcmd.respond_info("NTP synchronization disabled")
        else:
            gcmd.respond_info("ERROR: Failed to disable NTP synchronization")

    # -- Status exposure for Mainsail/Fluidd/Moonraker --

    def get_status(self, eventtime: float | None = None) -> dict[str, Any]:
        return {
            "ntp_active": self._ntp_active,
            "manage_ntp": self.manage_ntp,
            "manage_cpu_governor": self.manage_cpu_governor,
            "manage_usb_autosuspend": self.manage_usb_autosuspend,
            "manage_wifi_power_save": self.manage_wifi_power_save,
        }

    # -- Lifecycle event handlers --

    def _handle_ready(self) -> None:
        logger.info("ttc_fix: Applying timing safety fixes")
        if self.manage_ntp:
            self._apply_ntp_fix()
        if self.manage_cpu_governor:
            self._apply_cpu_governor_fix()
        if self.manage_usb_autosuspend:
            self._apply_usb_autosuspend_fix()
        if self.manage_wifi_power_save:
            self._apply_wifi_power_save_fix()
        logger.info("ttc_fix: All applicable fixes applied")

    def _handle_disconnect(self) -> None:
        logger.info("ttc_fix: Reverting timing fixes on shutdown")
        if self.manage_ntp:
            self._revert_ntp()
        if self.manage_cpu_governor:
            self._revert_cpu_governor()
        if self.manage_usb_autosuspend:
            self._revert_usb_autosuspend()
        if self.manage_wifi_power_save:
            self._revert_wifi_power_save()

    # ========================================================================
    # NTP fix — disable timesyncd clock slewing
    # ========================================================================

    def _apply_ntp_fix(self) -> None:
        self._refresh_ntp_status()
        if self._ntp_active is not False:
            logger.info("ttc_fix: Disabling NTP")
            self._set_ntp(False)
        else:
            logger.info("ttc_fix: NTP already disabled")

    def _revert_ntp(self) -> None:
        logger.info("ttc_fix: Re-enabling NTP")
        self._set_ntp(True)

    def _build_timedatectl_cmd(self, *args: str) -> list[str]:
        cmd = [TIMEDATECTL_PATH, *args]
        if self.use_sudo:
            cmd = [SUDO_PATH, *cmd]
        return cmd

    def _set_ntp(self, enable: bool) -> bool:
        action = "true" if enable else "false"
        try:
            subprocess.run(
                self._build_timedatectl_cmd("set-ntp", action),
                check=True,
                capture_output=True,
                timeout=10,
            )
            self._ntp_active = enable
            return True
        except subprocess.CalledProcessError as exc:
            logger.error(
                "ttc_fix: timedatectl set-ntp %s failed: %s",
                action,
                exc.stderr.decode(errors="replace").strip(),
            )
            return False
        except FileNotFoundError:
            logger.error(
                "ttc_fix: %s not found — is systemd installed?",
                TIMEDATECTL_PATH,
            )
            return False
        except subprocess.TimeoutExpired:
            logger.error("ttc_fix: timedatectl timed out")
            return False

    def _refresh_ntp_status(self) -> None:
        try:
            result = subprocess.run(
                self._build_timedatectl_cmd(
                    "show", "--property=NTP", "--value"
                ),
                check=True,
                capture_output=True,
                timeout=10,
            )
            value = result.stdout.decode(errors="replace").strip().lower()
            self._ntp_active = value == "yes"
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired) as exc:
            logger.warning("ttc_fix: Failed to query NTP status: %s", exc)
            self._ntp_active = None

    def _refresh_all_status(self) -> None:
        if self.manage_ntp:
            self._refresh_ntp_status()

    # ========================================================================
    # CPU governor fix — set to 'performance' to eliminate frequency scaling
    # ========================================================================

    def _read_cpu_governors(self) -> dict[str, str]:
        """Read current governor for each CPU core. Returns {path: governor}."""
        result: dict[str, str] = {}
        for path in sorted(glob.glob(CPU_GOVERNOR_GLOB)):
            try:
                with open(path, "r") as fh:
                    result[path] = fh.read().strip()
            except OSError:
                pass
        return result

    def _apply_cpu_governor_fix(self) -> None:
        governors = self._read_cpu_governors()
        if not governors:
            logger.warning("ttc_fix: No CPU governor sysfs entries found")
            return

        for path, current in governors.items():
            if current != "performance":
                # Save original so we can revert on shutdown
                self._cpu_governor_original[path] = current
                try:
                    with open(path, "w") as fh:
                        fh.write("performance")
                    logger.info(
                        "ttc_fix: CPU governor %s: %s -> performance", path, current
                    )
                except OSError as exc:
                    logger.error(
                        "ttc_fix: Failed to set CPU governor at %s: %s",
                        path, exc,
                    )
            else:
                logger.info("ttc_fix: CPU governor %s already performance", path)

    def _revert_cpu_governor(self) -> None:
        for path, original in self._cpu_governor_original.items():
            try:
                with open(path, "w") as fh:
                    fh.write(original)
                logger.info(
                    "ttc_fix: CPU governor %s reverted to %s", path, original
                )
            except OSError as exc:
                logger.warning(
                    "ttc_fix: Failed to revert CPU governor at %s: %s",
                    path, exc,
                )
        self._cpu_governor_original.clear()

    # ========================================================================
    # USB autosuspend fix — disable to prevent momentary device disconnects
    # ========================================================================

    def _read_usb_autosuspend(self) -> dict[str, str]:
        """Read autosuspend value for each USB device. Returns {path: value}."""
        result: dict[str, str] = {}
        for path in sorted(glob.glob(USB_AUTOSUSPEND_GLOB)):
            try:
                with open(path, "r") as fh:
                    result[path] = fh.read().strip()
            except OSError:
                pass
        return result

    def _apply_usb_autosuspend_fix(self) -> None:
        usb_values = self._read_usb_autosuspend()
        if not usb_values:
            logger.info("ttc_fix: No USB autosuspend sysfs entries found")
            return

        changed = 0
        for path, current in usb_values.items():
            # -1 means autosuspend is already disabled
            if current != "-1":
                self._usb_autosuspend_original[path] = current
                try:
                    with open(path, "w") as fh:
                        fh.write("-1")
                    changed += 1
                except OSError as exc:
                    logger.error(
                        "ttc_fix: Failed to disable USB autosuspend at %s: %s",
                        path, exc,
                    )

        if changed:
            logger.info(
                "ttc_fix: Disabled USB autosuspend on %d device(s)", changed
            )
        else:
            logger.info("ttc_fix: USB autosuspend already disabled on all devices")

    def _revert_usb_autosuspend(self) -> None:
        for path, original in self._usb_autosuspend_original.items():
            try:
                with open(path, "w") as fh:
                    fh.write(original)
            except OSError as exc:
                logger.warning(
                    "ttc_fix: Failed to revert USB autosuspend at %s: %s",
                    path, exc,
                )
        reverted = len(self._usb_autosuspend_original)
        if reverted:
            logger.info(
                "ttc_fix: Reverted USB autosuspend on %d device(s)", reverted
            )
        self._usb_autosuspend_original.clear()

    # ========================================================================
    # Wi-Fi power save fix — disable to prevent latency spikes
    # ========================================================================

    def _read_wifi_power_save(self) -> bool | None:
        """Query Wi-Fi power save state. Returns True/False/None."""
        try:
            result = subprocess.run(
                [IW_PATH, "dev", self.wifi_interface, "get", "power_save"],
                check=True,
                capture_output=True,
                timeout=10,
            )
            output = result.stdout.decode(errors="replace").strip().lower()
            # Output is like "Power save: on" or "Power save: off"
            return "on" in output
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired):
            return None

    def _set_wifi_power_save(self, enable: bool) -> bool:
        state = "on" if enable else "off"
        try:
            cmd = [IW_PATH, "dev", self.wifi_interface, "set", "power_save", state]
            if self.use_sudo:
                cmd = [SUDO_PATH] + cmd
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=10,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired) as exc:
            logger.error(
                "ttc_fix: Failed to set Wi-Fi power_save %s: %s", state, exc
            )
            return False

    def _apply_wifi_power_save_fix(self) -> None:
        ps_active = self._read_wifi_power_save()
        if ps_active is None:
            # Interface doesn't exist or iw not installed — not an error on
            # wired-only setups
            logger.info(
                "ttc_fix: Wi-Fi interface %s not available, skipping",
                self.wifi_interface,
            )
            return

        self._wifi_ps_original = ps_active
        if ps_active:
            if self._set_wifi_power_save(False):
                logger.info("ttc_fix: Disabled Wi-Fi power save on %s",
                            self.wifi_interface)
            # Error already logged by _set_wifi_power_save
        else:
            logger.info("ttc_fix: Wi-Fi power save already off on %s",
                        self.wifi_interface)

    def _revert_wifi_power_save(self) -> None:
        if self._wifi_ps_original is True:
            if self._set_wifi_power_save(True):
                logger.info("ttc_fix: Reverted Wi-Fi power save on %s",
                            self.wifi_interface)
        self._wifi_ps_original = None


def load_config(config: Any) -> TTCFix:
    return TTCFix(config)
