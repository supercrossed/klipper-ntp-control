# Klipper extra to control NTP/timesyncd service
#
# systemd-timesyncd makes periodic kernel clock frequency adjustments that
# cause all MCU adj values to drift simultaneously. The busiest MCU with
# the least timing slack (often a toolhead on CAN) errors out first.
# Disabling NTP during prints eliminates this class of timing errors.
#
# Copyright (C) 2026 Andrei
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import subprocess
from typing import Any

TIMEDATECTL_PATH = "/usr/bin/timedatectl"
SUDO_PATH = "/usr/bin/sudo"

logger = logging.getLogger(__name__)


class NTPControl:
    """Manages system NTP synchronization to prevent MCU timing errors."""

    def __init__(self, config: Any) -> None:
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")

        # Whether to automatically disable NTP on print start / re-enable on end
        self.auto_manage: bool = config.getboolean("auto_manage", True)

        # Whether timedatectl needs sudo (most Klipper setups do)
        self.use_sudo: bool = config.getboolean("use_sudo", True)

        # Track current NTP state (None = unknown until first query)
        self._ntp_active: bool | None = None

        # Register gcode commands
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
        self.gcode.register_command(
            "NTP_STATUS",
            self.cmd_NTP_STATUS,
            desc=self.cmd_NTP_STATUS_help,
        )

        # Register print event handlers for auto-management
        if self.auto_manage:
            self.printer.register_event_handler(
                "klippy:ready", self._handle_ready
            )
            self.printer.register_event_handler(
                "print_stats:printing", self._handle_print_start
            )
            self.printer.register_event_handler(
                "print_stats:complete", self._handle_print_end
            )
            self.printer.register_event_handler(
                "print_stats:cancelled", self._handle_print_end
            )
            self.printer.register_event_handler(
                "print_stats:error", self._handle_print_end
            )

    # -- Gcode command help strings --

    cmd_NTP_ENABLE_help = "Enable NTP time synchronization"
    cmd_NTP_DISABLE_help = "Disable NTP time synchronization"
    cmd_NTP_STATUS_help = "Report current NTP synchronization status"

    # -- Gcode command handlers --

    def cmd_NTP_ENABLE(self, gcmd: Any) -> None:
        success = self._set_ntp(True)
        if success:
            gcmd.respond_info("NTP synchronization enabled")
        else:
            gcmd.respond_info("ERROR: Failed to enable NTP synchronization")

    def cmd_NTP_DISABLE(self, gcmd: Any) -> None:
        success = self._set_ntp(False)
        if success:
            gcmd.respond_info("NTP synchronization disabled")
        else:
            gcmd.respond_info("ERROR: Failed to disable NTP synchronization")

    def cmd_NTP_STATUS(self, gcmd: Any) -> None:
        self._refresh_ntp_status()
        if self._ntp_active is None:
            gcmd.respond_info("NTP status: unknown (query failed)")
        elif self._ntp_active:
            gcmd.respond_info("NTP status: active (may cause MCU timing errors)")
        else:
            gcmd.respond_info("NTP status: inactive (safe for printing)")

    # -- Status exposure for Mainsail/Fluidd/Moonraker --

    def get_status(self, eventtime: float | None = None) -> dict[str, Any]:
        return {
            "ntp_active": self._ntp_active,
            "auto_manage": self.auto_manage,
        }

    # -- Event handlers --

    def _handle_ready(self) -> None:
        self._refresh_ntp_status()
        if self._ntp_active:
            logger.info(
                "ntp_control: NTP is currently active. "
                "It will be disabled automatically when a print starts."
            )

    def _handle_print_start(self, *args: Any) -> None:
        if not self.auto_manage:
            return
        if self._ntp_active is not False:
            logger.info("ntp_control: Disabling NTP for print safety")
            self._set_ntp(False)

    def _handle_print_end(self, *args: Any) -> None:
        if not self.auto_manage:
            return
        logger.info("ntp_control: Re-enabling NTP after print")
        self._set_ntp(True)

    # -- Internal helpers --

    def _build_cmd(self, *args: str) -> list[str]:
        """Build command list, prepending sudo if configured."""
        cmd = [TIMEDATECTL_PATH, *args]
        if self.use_sudo:
            cmd = [SUDO_PATH, *cmd]
        return cmd

    def _set_ntp(self, enable: bool) -> bool:
        """Set NTP state via timedatectl. Returns True on success."""
        action = "true" if enable else "false"
        try:
            subprocess.run(
                self._build_cmd("set-ntp", action),
                check=True,
                capture_output=True,
                timeout=10,
            )
            self._ntp_active = enable
            logger.info("ntp_control: NTP set to %s", action)
            return True
        except subprocess.CalledProcessError as exc:
            logger.error(
                "ntp_control: timedatectl set-ntp %s failed: %s",
                action,
                exc.stderr.decode(errors="replace").strip(),
            )
            return False
        except FileNotFoundError:
            logger.error(
                "ntp_control: %s not found — is systemd installed?",
                TIMEDATECTL_PATH,
            )
            return False
        except subprocess.TimeoutExpired:
            logger.error("ntp_control: timedatectl timed out after 10s")
            return False

    def _refresh_ntp_status(self) -> None:
        """Query current NTP state from timedatectl."""
        try:
            result = subprocess.run(
                self._build_cmd("show", "--property=NTP", "--value"),
                check=True,
                capture_output=True,
                timeout=10,
            )
            value = result.stdout.decode(errors="replace").strip().lower()
            self._ntp_active = value == "yes"
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired) as exc:
            logger.warning("ntp_control: Failed to query NTP status: %s", exc)
            self._ntp_active = None


def load_config(config: Any) -> NTPControl:
    return NTPControl(config)
