"""Set up and run an EVDI virtual monitor for the P13 panel."""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time

from .artinchip import DisplayError
from .evdi_bridge import EvdiBridge, PANEL_HEIGHT, PANEL_WIDTH
from .layout import (
    LAYOUT_PATH,
    aligned_virtual_position,
    apply_layout,
    apply_panel_config,
    find_connected_virtual_output,
    load_display_config,
    load_layout,
    reset_saved_layout,
    save_display_config,
)
from .session import register_bridge, stop_active_sessions, unregister_bridge

_LOGGER = logging.getLogger(__name__)

_PHYSICAL_PREFIXES = ("eDP", "HDMI", "DP-", "Virtual")

def reapply_virtual_layout(evdi_output: str) -> bool:
    layout = load_layout()
    if layout is None:
        return False
    return apply_layout(layout, evdi_output=evdi_output)

def ensure_evdi_loaded() -> None:
    try:
        result = subprocess.run(["lsmod"], capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DisplayError(f"cannot check kernel modules: {exc}") from exc

    if "evdi" in result.stdout:
        return

    modprobe = shutil.which("modprobe")
    if modprobe is None:
        raise DisplayError(
            "EVDI kernel module is not loaded.\n"
            "Install it with:\n"
            "  ./install.sh --evdi-only"
        )

    try:
        subprocess.run(
            [modprobe, "evdi", "initial_device_count=1"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "modprobe failed").strip()
        raise DisplayError(
            "failed to load EVDI kernel module.\n"
            f"  {detail}\n"
            "Install EVDI with:\n"
            "  ./install.sh --evdi-only\n"
            "Secure Boot requires enrolling the DKMS MOK key after install:\n"
            "  sudo mokutil --import /var/lib/dkms/mok.pub  # then reboot"
        ) from exc
    time.sleep(2)

def _xrandr_outputs() -> list[tuple[str, bool, str]]:
    xrandr = shutil.which("xrandr")
    if xrandr is None:
        return []
    try:
        out = subprocess.check_output([xrandr], timeout=10, stderr=subprocess.DEVNULL).decode()
    except (OSError, subprocess.SubprocessError):
        return []

    outputs: list[tuple[str, bool, str]] = []
    for line in out.splitlines():
        if " connected" not in line:
            continue
        name = line.split()[0]
        connected = True
        primary = " primary" in line
        outputs.append((name, primary, line))
    return outputs

def find_evdi_output() -> str | None:
    """Return the EVDI virtual output name, if present."""
    name = find_connected_virtual_output()
    if name is not None:
        return name
    for out_name, _primary, _line in _xrandr_outputs():
        if not any(prefix in out_name for prefix in _PHYSICAL_PREFIXES):
            return out_name
    return None

def _rightmost_output() -> tuple[str | None, int, int]:
    """Return ``(name, x+width, y)`` of the rightmost physical output via xrandr."""
    right_edge = 0
    right_y = 0
    right_name = None
    for name, _is_primary, line in _xrandr_outputs():
        if any(prefix in name for prefix in _PHYSICAL_PREFIXES):
            match = re.search(r"(\d+)x(\d+)\+(\d+)\+(\d+)", line)
            if match:
                width = int(match.group(1))
                x = int(match.group(3))
                y = int(match.group(4))
                if right_name is None or x + width > right_edge:
                    right_name = name
                    right_edge = x + width
                    right_y = y
    return right_name, right_edge, right_y

def configure_virtual_output(
    output: str | None = None,
    *,
    wait_s: float = 3.0,
    save: bool = True,
    reset_layout: bool = False,
) -> tuple[str, bool]:
    """Position only the EVDI virtual output; other monitors are left unchanged."""
    if reset_layout:
        reset_saved_layout()

    deadline = time.monotonic() + wait_s
    evdi_output = output
    while evdi_output is None and time.monotonic() < deadline:
        evdi_output = find_evdi_output()
        if evdi_output is None:
            time.sleep(0.5)

    if evdi_output is None:
        raise DisplayError(
            "EVDI output not found in xrandr/kscreen.\n"
            "Wait a few seconds after starting the bridge, then configure manually in System Settings."
        )

    saved = None if reset_layout else load_layout()
    if saved and apply_layout(saved, evdi_output=evdi_output):
        return evdi_output, True

    placement = aligned_virtual_position(panel_w=PANEL_WIDTH, panel_h=PANEL_HEIGHT)
    position = placement["position"]
    mode = f"{PANEL_WIDTH}x{PANEL_HEIGHT}"

    def _persist() -> None:
        if save:
            try:
                time.sleep(0.5)
                save_display_config(virtual_output=evdi_output)
            except DisplayError as exc:
                _LOGGER.warning("could not save virtual monitor layout: %s", exc)

    use_kscreen = (
        shutil.which("kscreen-doctor") is not None
        and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    )

    if use_kscreen:
        cmd = [
            "kscreen-doctor",
            f"output.{evdi_output}.enable",
            f"output.{evdi_output}.mode.{mode}@60",
            f"output.{evdi_output}.position.{position}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            _LOGGER.info(
                "configured %s via kscreen-doctor at %s (aligned to %s)",
                evdi_output,
                position,
                placement.get("anchor"),
            )
            _persist()
            return evdi_output, False
        _LOGGER.debug("kscreen-doctor configure failed: %s", (result.stderr or result.stdout).strip())

    xrandr = shutil.which("xrandr")
    if xrandr is not None:
        rightmost, right_edge, right_y = _rightmost_output()
        cmd = [xrandr, "--output", evdi_output, "--mode", mode, "--rate", "60"]
        # Prefer absolute aligned coords when we got them from KScreen; else right of the rightmost.
        if placement.get("anchor"):
            x_str, y_str = position.split(",", 1)
            cmd += ["--pos", f"{x_str}x{y_str}"]
        elif rightmost:
            cmd += ["--right-of", rightmost]
        else:
            cmd += ["--pos", f"{right_edge}x{right_y}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            _LOGGER.info(
                "configured %s at 480x480@60 aligned (%s)",
                evdi_output,
                position,
            )
            _persist()
            return evdi_output, False
        _LOGGER.debug("xrandr configure failed: %s", (result.stderr or result.stdout).strip())

    if shutil.which("kscreen-doctor"):
        cmd = [
            "kscreen-doctor",
            f"output.{evdi_output}.enable",
            f"output.{evdi_output}.mode.{mode}@60",
            f"output.{evdi_output}.position.{position}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            _LOGGER.info("configured %s via kscreen-doctor at %s", evdi_output, position)
            _persist()
            return evdi_output, False
        detail = (result.stderr or result.stdout or "kscreen-doctor failed").strip()
        raise DisplayError(f"failed to configure virtual output {evdi_output}: {detail}")

    raise DisplayError(
        f"found EVDI output {evdi_output} but could not configure it (need xrandr or kscreen-doctor)"
    )

def run_virtual_monitor(
    *,
    fps: int = 60,
    quality: int = 75,
    rotate: int = 0,
    configure: bool = True,
    save_layout: bool = True,
    reset_layout: bool = False,
) -> None:
    """Create an EVDI virtual monitor and stream it to the P13 panel."""
    ensure_evdi_loaded()

    saved_config = None if reset_layout else load_display_config()
    panel_restored = False
    if saved_config and saved_config.get("panel"):
        panel_restored = apply_panel_config(saved_config["panel"])

    output_name: str | None = None

    def _reapply_saved_layout() -> None:
        if output_name:
            reapply_virtual_layout(output_name)

    bridge = EvdiBridge(
        fps=fps,
        quality=quality,
        rotate=rotate,
        on_mode_changed_hook=_reapply_saved_layout,
    )

    def _request_stop(signum: int, _frame) -> None:
        _LOGGER.info("received signal %s, stopping", signum)
        # Stop the stream loop only — save layout in finally before EVDI disconnect.
        bridge.running = False

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)

    try:
        bridge.setup_evdi()
        if configure:
            time.sleep(2)
            output_name, restored = configure_virtual_output(
                save=False,
                reset_layout=reset_layout,
            )
            msg = (
                f"Virtual monitor {output_name} enabled ({PANEL_WIDTH}x{PANEL_HEIGHT} @ 60Hz).\n"
                "Drag windows onto it to show them on the P13 panel.\n"
            )
            if restored:
                msg += "Restored saved P13 desktop layout.\n"
            elif save_layout:
                msg += "Adjust placement in System Settings; stop to save all settings.\n"
            if panel_restored:
                msg += "Restored saved brightness and panel rotation.\n"
            msg += "Press Ctrl+C to stop."
            print(msg)
        else:
            print("EVDI virtual monitor active (Ctrl+C to stop).")
        register_bridge(bridge)
        bridge.run()
    finally:
        if save_layout and output_name:
            try:
                # panel is omitted on purpose: brightness/rotation persist when set,
                # and the live value may be a transient blank (sleep/off = 0).
                save_display_config(
                    virtual_output=output_name,
                    stream={"fps": fps, "quality": quality, "rotate": rotate},
                )
                print(f"Saved P13 settings to {LAYOUT_PATH}")
            except DisplayError as exc:
                _LOGGER.warning("could not save display config: %s", exc)
        unregister_bridge(bridge)
        bridge.shutdown()
