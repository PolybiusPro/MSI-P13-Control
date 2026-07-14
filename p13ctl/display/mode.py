"""Saved Display Mode for login autostart and the GUI."""

from __future__ import annotations

import logging
import signal
import threading
from pathlib import Path
from typing import Any

from PIL import Image

from .artinchip import ArtinchipDisplay, DisplayError
from .layout import (
    blank_panel_off,
    load_display_config,
    resolve_stream_settings,
    restore_saved_brightness,
    save_display_config,
)

_LOGGER = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_EXTENDED = "extended"
MODE_TEST = "test"
MODE_IMAGE = "image"
MODE_SYSMON = "sysmon"

VALID_MODES = frozenset(
    {MODE_OFF, MODE_EXTENDED, MODE_TEST, MODE_IMAGE, MODE_SYSMON}
)
PANEL_SIZE = (480, 480)
DEFAULT_MODE = MODE_EXTENDED

def get_saved_mode() -> dict[str, Any]:
    """Return ``{"name": str, "image": str | None}`` from saved config."""
    config = load_display_config() or {}
    raw = config.get("mode") if isinstance(config.get("mode"), dict) else {}
    name = str(raw.get("name") or DEFAULT_MODE)
    if name not in VALID_MODES:
        name = DEFAULT_MODE
    image = raw.get("image")
    image_path = str(image) if image else None
    if image_path and not Path(image_path).is_file():
        _LOGGER.warning("saved image missing: %s", image_path)
        image_path = None
    return {"name": name, "image": image_path}

def update_saved_mode(*, name: str, image: str | None = None) -> Path:
    """Persist the Display Mode selection (and optional image path)."""
    if name not in VALID_MODES:
        raise DisplayError(f"unknown display mode: {name}")
    mode: dict[str, Any] = {"name": name}
    if name == MODE_IMAGE:
        if image:
            mode["image"] = image
        else:
            existing = get_saved_mode().get("image")
            if existing:
                mode["image"] = existing
    return save_display_config(mode=mode)

def _stream() -> dict[str, int]:
    return resolve_stream_settings(
        load_display_config(),
        fps=None,
        quality=None,
        rotate=None,
    )

def run_saved_display_mode() -> int:
    """Apply the saved Display Mode (used at login by p13-display.service).

    Long-running modes (extended, sysmon) block until stopped.
    One-shot modes (off, test, image) apply a frame and return.
    """
    saved = get_saved_mode()
    name = saved["name"]
    stream = _stream()
    _LOGGER.info("starting saved display mode: %s", name)

    if name == MODE_OFF:
        try:
            blank_panel_off()
            print("Display off (brightness 0).")
        except DisplayError as exc:
            print(f"Display error: {exc}", flush=True)
            return 1
        return 0

    restore_saved_brightness()

    if name == MODE_TEST:
        try:
            with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
                disp.show_test_pattern()
            print("Test pattern sent.")
        except DisplayError as exc:
            print(f"Display error: {exc}", flush=True)
            return 1
        return 0

    if name == MODE_IMAGE:
        try:
            with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
                path = saved.get("image")
                if path:
                    disp.show_static_file(path)
                    print(f"Image sent: {path}")
                else:
                    disp.send_image(Image.new("RGB", PANEL_SIZE, (0, 0, 0)))
                    print("Solid black (no image selected yet).")
        except DisplayError as exc:
            print(f"Display error: {exc}", flush=True)
            return 1
        return 0

    if name == MODE_SYSMON:
        try:
            with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
                if threading.current_thread() is threading.main_thread():
                    signal.signal(signal.SIGTERM, lambda *_: disp.request_stop())
                    signal.signal(signal.SIGINT, lambda *_: disp.request_stop())
                print("System monitor running (Ctrl+C to stop)...")
                disp.run_sysmon()
        except DisplayError as exc:
            print(f"Display error: {exc}", flush=True)
            return 1
        return 0

    # MODE_EXTENDED
    from .virtual_monitor import run_virtual_monitor

    try:
        run_virtual_monitor(
            fps=stream["fps"],
            quality=stream["quality"],
            rotate=stream["rotate"],
            configure=True,
            save_layout=True,
            reset_layout=False,
        )
    except DisplayError as exc:
        print(f"Display error: {exc}", flush=True)
        return 1
    return 0
