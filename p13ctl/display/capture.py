"""Desktop screen capture for mirroring to the P13 LCD.

Supports X11 (mss / Pillow) and Wayland (grim, spectacle, gnome-screenshot,
xdg-desktop-portal).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from typing import Literal
from urllib.parse import unquote, urlparse

from PIL import Image

from .artinchip import DisplayError

_LOGGER = logging.getLogger(__name__)

CropMode = Literal["center", "stretch"]

def _session_type() -> str | None:
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return None

def _crop_to_square(image: Image.Image, mode: CropMode) -> Image.Image:
    if mode == "stretch":
        return image
    w, h = image.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return image.crop((left, top, left + side, top + side))

def _load_png(path: str) -> Image.Image:
    with Image.open(path) as img:
        return img.convert("RGB")

def _grab_mss(monitor: int) -> Image.Image:
    try:
        import mss
    except ImportError as exc:
        raise DisplayError(
            "desktop capture on X11 requires: pip install p13ctl[desktop]"
        ) from exc

    with mss.mss() as sct:
        monitors = sct.monitors
        idx = monitor + 1
        if idx >= len(monitors):
            raise DisplayError(
                f"monitor index {monitor} out of range ({len(monitors) - 1} available)"
            )
        shot = sct.grab(monitors[idx])
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

def _grab_pillow() -> Image.Image:
    from PIL import ImageGrab

    try:
        return ImageGrab.grab()
    except OSError as exc:
        raise DisplayError(f"Pillow screen grab failed: {exc}") from exc

def _grab_file_tool(
    name: str,
    args: list[str],
    *,
    timeout: float = 10,
) -> Image.Image | None:
    if not args:
        return None
    tool = shutil.which(args[0])
    if tool is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        result = subprocess.run(
            [tool, *args[1:], path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            _LOGGER.debug("%s failed: %s", name, detail)
            return None
        return _load_png(path)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _LOGGER.debug("%s failed: %s", name, exc)
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

def _grab_grim() -> Image.Image | None:
    return _grab_file_tool("grim", ["grim"])

def _grab_spectacle() -> Image.Image | None:
    return _grab_file_tool("spectacle", ["spectacle", "-b", "-n", "-o"])

def _grab_gnome_screenshot() -> Image.Image | None:
    return _grab_file_tool("gnome-screenshot", ["gnome-screenshot", "-f"])

def _uri_to_path(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return unquote(parsed.path)
    raise DisplayError(f"unsupported screenshot URI scheme: {parsed.scheme}")

def _grab_portal() -> Image.Image | None:
    try:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
    except ImportError:
        _LOGGER.debug("portal capture needs python3-dbus and PyGObject; skipping")
        return None

    result: dict[str, object] = {}

    def on_response(code, results):
        result["code"] = int(code)
        result["results"] = results
        loop.quit()

    try:
        DBusGMainLoop(set_as_default=True)
        loop = GLib.MainLoop()
        bus = dbus.SessionBus()
        portal = bus.get_object(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
        )
        screenshot = dbus.Interface(portal, "org.freedesktop.portal.Screenshot")
        request_path = screenshot.Screenshot("", {})
        request = bus.get_object("org.freedesktop.portal.Desktop", request_path)
        request.connect_to_signal(
            "Response",
            on_response,
            dbus_interface="org.freedesktop.portal.Request",
        )
        loop.run()
    except Exception as exc:  # noqa: BLE001 — portal stacks vary by compositor
        _LOGGER.debug("portal screenshot failed: %s", exc)
        return None

    if result.get("code") != 0:
        _LOGGER.debug("portal screenshot denied (code %s)", result.get("code"))
        return None
    results = result.get("results")
    if not isinstance(results, dict) or "uri" not in results:
        _LOGGER.debug("portal screenshot returned no URI")
        return None
    path = _uri_to_path(str(results["uri"]))
    try:
        return _load_png(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

def _wayland_backend_order() -> list[str]:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    if "KDE" in desktop:
        # grim needs wlr-screencopy; KDE uses spectacle instead
        return ["spectacle", "xdg-desktop-portal", "gnome-screenshot", "grim"]
    if "GNOME" in desktop:
        return ["gnome-screenshot", "xdg-desktop-portal", "spectacle", "grim"]
    # wlroots and others
    return ["grim", "spectacle", "gnome-screenshot", "xdg-desktop-portal"]

def _grab_wayland() -> Image.Image:
    backends: dict[str, Callable[[], Image.Image | None]] = {
        "grim": _grab_grim,
        "spectacle": _grab_spectacle,
        "gnome-screenshot": _grab_gnome_screenshot,
        "xdg-desktop-portal": _grab_portal,
    }
    tried: list[str] = []
    for name in _wayland_backend_order():
        grab = backends[name]
        try:
            image = grab()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("%s raised: %s", name, exc)
            image = None
        tried.append(name)
        if image is not None:
            _LOGGER.debug("captured desktop via %s", name)
            return image

    raise DisplayError(
        "Wayland screen capture failed (--capture fallback).\n"
        "Prefer the default virtual monitor: p13ctl display desktop\n"
        "For capture mode install spectacle (KDE), gnome-screenshot (GNOME), or grim (wlroots).\n"
        f"Tried: {', '.join(tried)}"
    )

def grab_desktop(
    monitor: int = 0,
    crop: CropMode = "center",
) -> Image.Image:
    """Capture the desktop and optionally center-crop to a square."""
    session = _session_type()
    if session is None:
        raise DisplayError(
            "no display session found (set DISPLAY for X11 or WAYLAND_DISPLAY for Wayland)"
        )

    if session == "wayland":
        if monitor != 0:
            _LOGGER.warning("--monitor is ignored on Wayland; capturing full desktop")
        image = _grab_wayland()
    else:
        try:
            image = _grab_mss(monitor)
        except DisplayError:
            _LOGGER.debug("mss unavailable, falling back to Pillow ImageGrab")
            image = _grab_pillow()

    return _crop_to_square(image, crop)
