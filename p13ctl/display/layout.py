"""Persist P13 display settings — virtual monitor layout, panel HID, stream options."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .artinchip import DisplayError

_LOGGER = logging.getLogger(__name__)

CONFIG_VERSION = 3
LAYOUT_VERSION = CONFIG_VERSION  # backward compat alias
LAYOUT_PATH = Path.home() / ".config/p13ctl/display-layout.json"
_EVDI_PREFIXES = ("DVI-I", "DVI-D", "DVI")
_PHYSICAL_PREFIXES = ("eDP", "HDMI", "DP-", "Virtual")

# KScreen::Output::Rotation bit flags (see libkscreen output.h)
_KSCREEN_ROTATION_NAMES: dict[int, str | None] = {
    0: None,
    1: None,  # None = 1 << 0
    2: "left",  # Left = 1 << 1
    4: "inverted",  # Inverted = 1 << 2
    8: "right",  # Right = 1 << 3
    16: "flipped",
    32: "flipped90",
    64: "flipped180",
    128: "flipped270",
}

def layout_path() -> Path:
    return LAYOUT_PATH

def is_evdi_output(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in _EVDI_PREFIXES)

def is_physical_output(name: str) -> bool:
    return any(prefix in name for prefix in _PHYSICAL_PREFIXES)

def reset_saved_layout() -> None:
    try:
        LAYOUT_PATH.unlink(missing_ok=True)
    except OSError as exc:
        raise DisplayError(f"could not remove saved config: {exc}") from exc

def read_panel_state() -> dict[str, int]:
    """Read brightness and physical rotation from the P13 HID interface."""
    from p13ctl.hid.msi_p13 import HidError, P13HidController

    try:
        with P13HidController() as hid:
            info = hid.connect_session()
    except HidError as exc:
        raise DisplayError(f"could not read panel settings: {exc}") from exc
    return {"brightness": int(info.brightness), "rotation": int(info.degree)}

def apply_panel_config(panel: dict[str, Any]) -> bool:
    """Apply saved brightness and physical panel rotation via HID."""
    from p13ctl.hid.msi_p13 import HidError, P13HidController

    if not panel:
        return False
    try:
        with P13HidController() as hid:
            if "brightness" in panel:
                hid.set_brightness(int(panel["brightness"]))
            if "rotation" in panel:
                hid.set_rotate(int(panel["rotation"]))
    except HidError as exc:
        _LOGGER.warning("could not apply panel settings: %s", exc)
        return False
    _LOGGER.info(
        "restored panel settings (brightness=%s, rotation=%s)",
        panel.get("brightness"),
        panel.get("rotation"),
    )
    return True

def update_saved_panel(**values: int) -> Path:
    """Merge panel fields into the saved config (e.g. after hid brightness/rotate)."""
    existing = load_display_config() or {}
    panel = dict(existing.get("panel") or {})
    panel.update(values)
    return save_display_config(panel=panel)

def resolve_stream_settings(
    config: dict[str, Any] | None,
    *,
    fps: int | None,
    quality: int | None,
    rotate: int | None,
) -> dict[str, int]:
    stream = (config or {}).get("stream") or {}
    return {
        "fps": fps if fps is not None else int(stream.get("fps", 60)),
        "quality": quality if quality is not None else int(stream.get("quality", 75)),
        "rotate": rotate if rotate is not None else int(stream.get("rotate", 0)),
    }

def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)

def _parse_kscreen_json(stdout: str) -> dict[str, Any]:
    cleaned = _strip_ansi(stdout)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise DisplayError("kscreen-doctor returned no JSON layout")
    return json.loads(match.group())

def _fetch_kscreen_outputs() -> list[dict[str, Any]]:
    doctor = shutil.which("kscreen-doctor")
    if doctor is None:
        raise DisplayError("kscreen-doctor not found")

    result = subprocess.run(
        [doctor, "-j", "-o"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "kscreen-doctor failed").strip()
        raise DisplayError(f"failed to read display layout: {detail}")

    data = _parse_kscreen_json(result.stdout)
    outputs: list[dict[str, Any]] = []
    for item in data.get("outputs", []):
        if not item.get("connected"):
            continue
        name = item.get("name")
        if not name:
            continue
        pos = item.get("pos") or {}
        size = item.get("size") or {}
        outputs.append(
            {
                "name": name,
                "x": int(pos.get("x", 0)),
                "y": int(pos.get("y", 0)),
                "w": int(size.get("width", 0)),
                "h": int(size.get("height", 0)),
                "enabled": bool(item.get("enabled", True)),
                "mode": _mode_name(item),
                "rotation": _rotation_name(item.get("rotation")),
                "scale": item.get("scale", 1),
                "raw": item,
            }
        )
    return outputs

def _mode_name(output: dict[str, Any]) -> str | None:
    mode_id = output.get("currentModeId")
    if mode_id is None:
        return None
    for mode in output.get("modes", []):
        if str(mode.get("id")) == str(mode_id):
            return mode.get("name")
    return None

def _rotation_name(value: int | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        name = value.strip().lower()
        if name in ("", "none", "normal"):
            return None
        if name in _KSCREEN_ROTATION_NAMES.values():
            return name
        return None
    try:
        key = int(value)
    except (TypeError, ValueError):
        return None
    return _KSCREEN_ROTATION_NAMES.get(key)

def _find_anchor(virtual: dict[str, Any], outputs: list[dict[str, Any]]) -> tuple[str | None, dict[str, int]]:
    physical = [o for o in outputs if is_physical_output(o["name"])]
    if not physical:
        return None, {"x": virtual["x"], "y": virtual["y"]}

    best_name: str | None = None
    best_score: float | None = None
    for anchor in physical:
        below_gap = virtual["y"] - (anchor["y"] + anchor["h"])
        if below_gap < -100:
            continue
        score = abs(below_gap) + abs(virtual["x"] - anchor["x"]) * 0.25
        if best_score is None or score < best_score:
            best_score = score
            best_name = anchor["name"]

    if best_name is None:
        vx = virtual["x"] + virtual["w"] // 2
        vy = virtual["y"] + virtual["h"] // 2
        for anchor in physical:
            cx = anchor["x"] + anchor["w"] // 2
            cy = anchor["y"] + anchor["h"] // 2
            score = abs(vx - cx) + abs(vy - cy)
            if best_score is None or score < best_score:
                best_score = score
                best_name = anchor["name"]

    if best_name is None:
        return None, {"x": virtual["x"], "y": virtual["y"]}

    anchor = next(o for o in physical if o["name"] == best_name)
    return best_name, {"x": virtual["x"] - anchor["x"], "y": virtual["y"] - anchor["y"]}

def _resolve_position(
    entry: dict[str, Any],
    outputs: list[dict[str, Any]],
) -> str | None:
    anchor_name = entry.get("anchor")
    offset = entry.get("offset")
    if anchor_name and isinstance(offset, dict):
        anchor = next((o for o in outputs if o["name"] == anchor_name), None)
        if anchor is not None:
            x = anchor["x"] + int(offset.get("x", 0))
            y = anchor["y"] + int(offset.get("y", 0))
            return f"{x},{y}"

    position = entry.get("position")
    if position:
        return position
    return None

def capture_virtual_output(name: str | None = None) -> dict[str, Any]:
    """Read virtual monitor settings and anchor-relative placement."""
    outputs = _fetch_kscreen_outputs()
    virtual_raw: dict[str, Any] | None = None

    for item in outputs:
        if name is not None and item["name"] == name:
            virtual_raw = item
            break
        if name is None and is_evdi_output(item["name"]):
            virtual_raw = item
            break

    if virtual_raw is None:
        raise DisplayError("virtual monitor output not found in current display layout")

    anchor, offset = _find_anchor(virtual_raw, outputs)
    virtual = {
        "name": virtual_raw["name"],
        "enabled": virtual_raw["enabled"],
        "mode": virtual_raw["mode"],
        "position": f"{virtual_raw['x']},{virtual_raw['y']}",
        "rotation": virtual_raw["rotation"],
        "scale": virtual_raw["scale"],
        "anchor": anchor,
        "offset": offset,
    }
    return {"version": CONFIG_VERSION, "virtual": virtual}

def load_display_config() -> dict[str, Any] | None:
    if not LAYOUT_PATH.is_file():
        return None
    try:
        data = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOGGER.warning("ignoring invalid saved config: %s", exc)
        return None

    config: dict[str, Any] = {"version": CONFIG_VERSION}
    virtual = _load_virtual_entry(data)
    if virtual is not None:
        config["virtual"] = virtual
    if isinstance(data.get("panel"), dict):
        config["panel"] = dict(data["panel"])
    if isinstance(data.get("stream"), dict):
        config["stream"] = dict(data["stream"])
    if len(config) == 1:
        return None
    return config

def save_display_config(
    virtual_output: str | None = None,
    *,
    panel: dict[str, Any] | None = None,
    stream: dict[str, Any] | None = None,
) -> Path:
    """Save P13 settings, merging with any existing config on disk."""
    existing = load_display_config() or {}
    data: dict[str, Any] = {"version": CONFIG_VERSION}

    if virtual_output is not None:
        captured = capture_virtual_output(virtual_output)
        data["virtual"] = captured["virtual"]
    elif existing.get("virtual"):
        data["virtual"] = existing["virtual"]

    if panel is not None:
        data["panel"] = panel
    elif existing.get("panel"):
        data["panel"] = existing["panel"]

    if stream is not None:
        data["stream"] = stream
    elif existing.get("stream"):
        data["stream"] = existing["stream"]

    LAYOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAYOUT_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _LOGGER.info("saved display config to %s", LAYOUT_PATH)
    return LAYOUT_PATH

def save_layout(virtual_output: str | None = None) -> Path:
    """Save only the virtual monitor section (merges with existing config)."""
    return save_display_config(virtual_output=virtual_output)

def _load_virtual_entry(data: dict[str, Any]) -> dict[str, Any] | None:
    if data.get("version") in (CONFIG_VERSION, 2) and data.get("virtual"):
        return dict(data["virtual"])
    if data.get("version") == 1:
        for entry in data.get("outputs", []):
            if is_evdi_output(entry.get("name", "")):
                loaded = dict(entry)
                loaded.pop("priority", None)
                return loaded
    return None

def load_layout() -> dict[str, Any] | None:
    config = load_display_config()
    if config is None or not config.get("virtual"):
        return None
    return {"version": CONFIG_VERSION, "virtual": config["virtual"]}

def _kscreen_args_for_virtual(entry: dict[str, Any], position: str | None) -> list[str]:
    name = entry["name"]
    if not entry.get("enabled", True):
        return [f"output.{name}.disable"]

    args = [f"output.{name}.enable"]
    mode = entry.get("mode")
    if mode:
        args.append(f"output.{name}.mode.{mode}")
    if position:
        args.append(f"output.{name}.position.{position}")
    rotation = entry.get("rotation")
    if rotation:
        args.append(f"output.{name}.rotation.{rotation}")
    else:
        args.append(f"output.{name}.rotation.none")
    scale = entry.get("scale")
    if scale not in (None, 1, 1.0):
        args.append(f"output.{name}.scale.{scale}")
    return args

def apply_layout(layout: dict[str, Any], *, evdi_output: str) -> bool:
    """Apply saved settings to the virtual monitor only."""
    doctor = shutil.which("kscreen-doctor")
    if doctor is None:
        return False

    virtual = dict(layout.get("virtual") or {})
    if not virtual:
        return False

    try:
        outputs = _fetch_kscreen_outputs()
    except DisplayError as exc:
        _LOGGER.debug("could not read outputs for layout apply: %s", exc)
        outputs = []

    virtual["name"] = evdi_output
    virtual["enabled"] = True
    position = _resolve_position(virtual, outputs)
    cmd = [doctor, *_kscreen_args_for_virtual(virtual, position)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "kscreen-doctor failed").strip()
        _LOGGER.debug("apply virtual layout failed: %s", detail)
        return False
    _LOGGER.info(
        "restored virtual monitor %s at %s (anchor=%s)",
        evdi_output,
        position,
        virtual.get("anchor"),
    )
    return True
