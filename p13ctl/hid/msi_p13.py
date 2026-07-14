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
    boot_finish: int = 0
    realtime_display: int = 0
    extended_display: int = 0
    raw: dict[str, Any] | None = None

# SeqNumber values seen in captured traffic.
SEQ_CONN = 100
SEQ_EXTENDED_DISPLAY = 110
SEQ_REALTIME_DISPLAY = 112

# Firmware JPEG path applies Degree itself. Combined with a prior software
# rotate this looked like (2*logical + 180) on panel — e.g. CLI 0→180, 90→0.
# Use firmware Degree alone and compensate the panel's native 180° orientation.
_DEGREE_WIRE_OFFSET = 180

def to_wire_degree(logical: int) -> int:
    """Map user-facing degrees (0/90/180/270) to firmware Degree."""
    return (int(logical) + _DEGREE_WIRE_OFFSET) % 360

def from_wire_degree(wire: int) -> int:
    """Map firmware Degree back to user-facing degrees."""
    return (int(wire) - _DEGREE_WIRE_OFFSET) % 360

def _build_post(command: str, body: str = "", seq: int = 100) -> str:
    headers = (
        f"POST {command}\r\n"
        f"SeqNumber={seq}\r\n"
        f"ContentType=json\r\n"
    )
    if body:
        headers += f"ContentLength={len(body)}\r\n\r\n{body}"
    else:
        # The conn request ends without a blank body line when ContentLength is omitted.
        pass
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
        last_error: Exception | None = None
        for attempt in range(10):
            try:
                self._dev = hid.device()
                self._dev.open_path(path)
                break
            except OSError as exc:
                last_error = exc
                self._dev = None
                # Another p13ctl path (display host-enable, GUI rotate, etc.) may hold
                # the exclusive hidraw node briefly.
                time.sleep(0.05 * (attempt + 1))
        else:
            raise HidError(
                f"HID open failed (device busy?): {last_error}"
            ) from last_error
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
        # Report ID at offset 0; remaining bytes may be zero-padded to 1024.
        # Do not truncate at the first 0x00 — length high-byte is often zero.
        data = bytes(raw[1:]) if raw and raw[0] == REPORT_ID else bytes(raw)
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

    def _field(self, data: dict[str, Any], *names: str, default: Any = None) -> Any:
        """Read a JSON field with PascalCase or camelCase keys."""
        lower = {str(key).lower(): value for key, value in data.items()}
        for name in names:
            if name.lower() in lower:
                return lower[name.lower()]
        return default

    def _parse_json_body(self, response: str) -> dict[str, Any]:
        _headers, body = _parse_post_response(response)
        body = re.sub(r"[^\x20-\x7E]", "", body)
        cl_match = re.search(r"ContentLength=(\d+)", response.split("\r\n\r\n", 1)[0])
        if cl_match:
            length = int(cl_match.group(1))
            body = body[:length]
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HidError(f"invalid JSON body: {body!r}") from exc
        if not isinstance(data, dict):
            raise HidError(f"unexpected JSON payload: {body!r}")
        return data

    def _read_until(
        self,
        *,
        timeout_ms: int,
        predicate,
    ) -> str:
        deadline = time.time() + timeout_ms / 1000.0
        last_error: Exception | None = None
        while time.time() < deadline:
            remaining = max(50, int((deadline - time.time()) * 1000))
            try:
                response = self._read_message(timeout_ms=min(500, remaining))
            except HidError as exc:
                last_error = exc
                continue
            if predicate(response):
                return response
        if last_error:
            raise HidError(f"HID read timeout: {last_error}")
        raise HidError("HID read timeout")

    def _command_with_ack(self, command: str, body: str, seq: int, *, retries: int = 2) -> bool:
        """Write a POST and wait for a SeqNumber+1 ack."""
        msg = _build_post(command, body, seq=seq)
        for attempt in range(retries):
            try:
                self._write_message(msg)
                response = self._read_until(
                    timeout_ms=2000,
                    predicate=lambda text: (
                        str(seq + 1) in text
                        or "httpStatusCode" in text
                        or "200" in text
                        or "400" in text
                    ),
                )
                if "400" in response.split("\r\n", 1)[0]:
                    _LOGGER.debug("nack attempt %s: %r", attempt + 1, response[:120])
                    continue
                if str(seq + 1) in response or "200" in response:
                    return True
                _LOGGER.debug("ack mismatch attempt %s: %r", attempt + 1, response[:120])
            except HidError as exc:
                _LOGGER.debug("command %s attempt %s failed: %s", command, attempt + 1, exc)
                time.sleep(0.5)
        # Fall back to fire-and-forget like brightness (some firmwares skip ack).
        try:
            self._write_message(msg)
            self._write_message(msg)
            return True
        except HidError:
            return False

    def connect_session(self) -> DeviceInfo:
        """POST conn — returns device metadata JSON from firmware.

        The host writes conn twice before reading. Firmware may first
        emit a short ack, then the JSON body — wait until ContentLength arrives.
        """
        msg = _build_post("conn 1", seq=SEQ_CONN)
        self._write_message(msg)
        self._write_message(msg)
        response = self._read_until(
            timeout_ms=5000,
            predicate=lambda text: "ContentLength" in text and "{" in text,
        )
        data = self._parse_json_body(response)
        version = self._field(data, "Version", "version") or {}
        if not isinstance(version, dict):
            version = {}
        info = DeviceInfo(
            manufacturer=str(self._field(data, "Manufacturer", "manufacturer", default="") or ""),
            model=str(self._field(data, "Model", "model", default="") or ""),
            serial=str(self._field(data, "SN", "Sn", "sn", default="") or ""),
            brightness=int(self._field(data, "Brightness", "brightness", default=0) or 0),
            degree=from_wire_degree(
                int(self._field(data, "Degree", "degree", default=0) or 0)
            ),
            app_version=str(self._field(version, "App", "app", default="") or ""),
            firmware_version=str(self._field(version, "Firmware", "firmware", default="") or ""),
            hardware_version=str(self._field(version, "Hardware", "hardware", default="") or ""),
            boot_finish=int(self._field(data, "BootFinish", "bootFinish", default=0) or 0),
            realtime_display=int(
                self._field(data, "RealtimeDisplay", "realtimeDisplay", default=0) or 0
            ),
            extended_display=int(
                self._field(data, "ExtendedDisplay", "extendedDisplay", default=0) or 0
            ),
            raw=data,
        )
        _LOGGER.info(
            "connected to %s (%s) ExtendedDisplay=%s RealtimeDisplay=%s Degree=%s",
            info.model,
            info.serial,
            info.extended_display,
            info.realtime_display,
            info.degree,
        )
        return info

    def _command_fire_and_forget(self, command: str, body: str, seq: int) -> None:
        """Write a POST twice without waiting for an ack (brightness-style)."""
        msg = _build_post(command, body, seq=seq)
        self._write_message(msg)
        self._write_message(msg)

    def set_extended_display(self, enable: bool = True, *, wait_ack: bool = False) -> bool:
        """Leave firmware splash / onboard animation and accept host framebuffer.

        Matches the captured ``POST extendedDisplay`` (SeqNumber=110).
        Default is fire-and-forget so display connect does not hold hidraw for seconds.
        """
        body = json.dumps({"enable": bool(enable)}, separators=(",", ":"))
        if wait_ack:
            if self._command_with_ack("extendedDisplay 1", body, SEQ_EXTENDED_DISPLAY):
                _LOGGER.info("extendedDisplay -> %s", enable)
                return True
            raise HidError("failed to set extendedDisplay")
        self._command_fire_and_forget("extendedDisplay 1", body, SEQ_EXTENDED_DISPLAY)
        _LOGGER.info("extendedDisplay -> %s", enable)
        return True

    def set_realtime_display(self, enable: bool = True, *, wait_ack: bool = False) -> bool:
        """Enable firmware realtime host-content path (SeqNumber=112)."""
        body = json.dumps({"enable": bool(enable)}, separators=(",", ":"))
        if wait_ack:
            if self._command_with_ack("realtimeDisplay 1", body, SEQ_REALTIME_DISPLAY):
                _LOGGER.info("realtimeDisplay -> %s", enable)
                return True
            _LOGGER.warning("realtimeDisplay set failed (non-fatal)")
            return False
        self._command_fire_and_forget("realtimeDisplay 1", body, SEQ_REALTIME_DISPLAY)
        _LOGGER.info("realtimeDisplay -> %s", enable)
        return True

    def enable_host_display(self) -> DeviceInfo:
        """Cold-boot handoff: assert extendedDisplay(+realtime) for host JPEG.

        Skips the slow ``conn`` query — that held hidraw for multiple seconds and
        caused concurrent GUI/CLI HID opens to fail with "open failed".
        """
        self.set_extended_display(True)
        self.set_realtime_display(True)
        return DeviceInfo()

    def set_brightness(self, percent: int) -> None:
        value = max(0, min(100, int(percent)))
        body = json.dumps({"value": value}, separators=(",", ":"))
        msg = _build_post("brightness 1", body)
        # Brightness is sent twice (observed on the wire).
        self._write_message(msg)
        self._write_message(msg)
        _LOGGER.info("brightness -> %s", value)

    def set_rotate(self, degrees: int) -> None:
        """Set panel orientation (user-facing 0/90/180/270).

        Fire-and-forget like brightness — firmware acks are often multi-second
        and Degree still takes effect. Wire Degree is offset by 180°.
        """
        if degrees not in (0, 90, 180, 270):
            raise ValueError("rotation must be 0, 90, 180, or 270")
        wire = to_wire_degree(degrees)
        body = json.dumps({"degree": wire}, separators=(",", ":"))
        msg = _build_post("rotate 1", body)
        self._write_message(msg)
        self._write_message(msg)
        _LOGGER.info("rotate -> %s (wire Degree=%s)", degrees, wire)

    def sniff(self, duration_s: float = 5.0) -> list[str]:
        end = time.time() + duration_s
        packets: list[str] = []
        while time.time() < end:
            try:
                packets.append(self._read_message(timeout_ms=200))
            except HidError:
                continue
        return packets

def enable_host_display() -> DeviceInfo:
    """Module helper used by display paths before USB JPEG streaming."""
    with P13HidController() as hid_dev:
        return hid_dev.enable_host_display()
