"""MSI P13 HID control (33c3:0e02 interface 1).

Protocol reverse engineered by capturing the device's USB HID traffic.
The HID channel speaks HTTP-like POST messages wrapped in framed packets.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import hid

from .framing import ProtocolError, decode, encode

_LOGGER = logging.getLogger(__name__)

VENDOR_ID = 0x33C3
PRODUCT_ID = 0x0E02
HID_INTERFACE = 1

# The device expects 1025-byte output reports (report ID + 1024 data bytes).
REPORT_SIZE = 1025
DATA_SIZE = 1024
REPORT_ID = 0x00

class HidError(RuntimeError):
    pass

@dataclass
class DeviceInfo:
    manufacturer: str = ""
    model: str = ""
    serial: str = ""
    brightness: int = 0
    degree: int = 0
    app_version: str = ""
    firmware_version: str = ""
    hardware_version: str = ""
    raw: dict[str, Any] | None = None

def _build_post(command: str, body: str = "", seq: int = 100) -> str:
    headers = (
        f"POST {command}\r\n"
        f"SeqNumber={seq}\r\n"
        f"ContentType=json\r\n"
    )
    if body:
        headers += f"ContentLength={len(body)}\r\n\r\n{body}"
    return headers

def _parse_post_response(text: str) -> tuple[dict[str, str], str]:
    header_part, _, body = text.partition("\r\n\r\n")
    headers: dict[str, str] = {}
    lines = header_part.split("\r\n")
    if lines:
        headers["command"] = lines[0]
    for line in lines[1:]:
        if "=" in line:
            key, value = line.split("=", 1)
            headers[key.strip()] = value.strip()
    return headers, body

class P13HidController:
    def __init__(self, device: Optional[hid.device] = None):
        self._dev = device
        self._owned = device is None

    @classmethod
    def find_path(cls) -> Optional[str]:
        for info in hid.enumerate(VENDOR_ID, PRODUCT_ID):
            if info.get("interface_number") == HID_INTERFACE:
                return info["path"]
        return None

    def connect(self) -> None:
        if self._dev is not None:
            return
        path = self.find_path()
        if path is None:
            raise HidError("P13 HID interface not found (33c3:0e02 MI_01)")
        self._dev = hid.device()
        self._dev.open_path(path)
        self._owned = True
        try:
            self._dev.set_nonblocking(False)
        except Exception:
            pass
        _LOGGER.info("HID connected: %s", path)

    def close(self) -> None:
        if self._dev and self._owned:
            self._dev.close()
        self._dev = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()

    def _write_message(self, message: str) -> None:
        if self._dev is None:
            raise HidError("not connected")
        frame = encode(message.encode("utf-8"))
        if len(frame) > DATA_SIZE:
            raise HidError(f"framed message too large ({len(frame)} > {DATA_SIZE})")
        report = bytearray(REPORT_SIZE)
        report[0] = REPORT_ID
        report[1 : 1 + len(frame)] = frame
        written = self._dev.write(bytes(report))
        if written <= 0:
            raise HidError("HID write failed")
        time.sleep(0.01)

    def _read_message(self, timeout_ms: int = 1000) -> str:
        if self._dev is None:
            raise HidError("not connected")
        raw = self._dev.read(REPORT_SIZE, timeout_ms)
        if not raw:
            raise HidError("HID read timeout")
        data = bytes(raw[1:]) if raw[0] == REPORT_ID else bytes(raw)
        end = data.find(0)
        if end != -1:
            data = data[:end]
        while data and data[-1] == 0:
            data = data[:-1]
        try:
            payload = decode(data)
        except ProtocolError as exc:
            raise HidError(f"protocol decode failed: {exc}") from exc
        return payload.decode("utf-8", errors="replace").rstrip("\x00")

    def _request(self, message: str, read_response: bool = False) -> Optional[str]:
        self._write_message(message)
        if not read_response:
            return None
        return self._read_message()

    def connect_session(self) -> DeviceInfo:
        """POST conn — returns device metadata JSON from firmware."""
        response = self._request(_build_post("conn 1"), read_response=True)
        if not response:
            raise HidError("empty conn response")
        _headers, body = _parse_post_response(response)
        body = re.sub(r"[^\x20-\x7E]", "", body)
        cl_match = re.search(r"ContentLength=(\d+)", response.split("\r\n\r\n", 1)[0])
        if cl_match:
            length = int(cl_match.group(1))
            body = body[:length]
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HidError(f"invalid conn JSON: {body!r}") from exc
        version = data.get("Version") or {}
        info = DeviceInfo(
            manufacturer=data.get("Manufacturer", ""),
            model=data.get("Model", ""),
            serial=data.get("SN", ""),
            brightness=int(data.get("Brightness", 0)),
            degree=int(data.get("Degree", 0)),
            app_version=version.get("App", ""),
            firmware_version=version.get("Firmware", ""),
            hardware_version=version.get("Hardware", ""),
            raw=data,
        )
        _LOGGER.info("connected to %s (%s)", info.model, info.serial)
        return info

    def set_brightness(self, percent: int) -> None:
        value = max(0, min(100, int(percent)))
        body = json.dumps({"value": value}, separators=(",", ":"))
        msg = _build_post("brightness 1", body)
        # Brightness is sent twice (observed on the wire).
        self._write_message(msg)
        self._write_message(msg)
        _LOGGER.info("brightness -> %s", value)

    def set_rotate(self, degrees: int) -> None:
        if degrees not in (0, 90, 180, 270):
            raise ValueError("rotation must be 0, 90, 180, or 270")
        body = json.dumps({"degree": degrees}, separators=(",", ":"))
        msg = _build_post("rotate 1", body)
        self._write_message(msg)
        self._write_message(msg)
        _LOGGER.info("rotate -> %s", degrees)

    def sniff(self, duration_s: float = 5.0) -> list[str]:
        end = time.time() + duration_s
        packets: list[str] = []
        while time.time() < end:
            try:
                packets.append(self._read_message(timeout_ms=200))
            except HidError:
                continue
        return packets
