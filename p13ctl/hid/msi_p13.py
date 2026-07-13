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

    def _command_with_ack(self, command: str, body: str, seq: int, *, retries: int = 2) -> bool:
        """Write a POST and wait for a SeqNumber+1 ack."""
        msg = _build_post(command, body, seq=seq)
        for attempt in range(retries):
            try:
                self._write_message(msg)
                response = self._read_message(timeout_ms=2000)
                if str(seq + 1) in response or "httpStatusCode" in response or "200" in response:
                    return True
                _LOGGER.debug("ack mismatch attempt %s: %r", attempt + 1, response[:120])
            except HidError as exc:
                _LOGGER.debug("command %s attempt %s failed: %s", command, attempt + 1, exc)
                time.sleep(1.0)
        # Fall back to fire-and-forget like brightness (some firmwares skip ack).
        try:
            self._write_message(msg)
            self._write_message(msg)
            return True
        except HidError:
            return False

    def connect_session(self) -> DeviceInfo:
        """POST conn — returns device metadata JSON from firmware.

        The host writes conn twice before reading.
        """
        msg = _build_post("conn 1", seq=SEQ_CONN)
        self._write_message(msg)
        self._write_message(msg)
        response = self._read_message(timeout_ms=2000)
        if not response:
            raise HidError("empty conn response")
        data = self._parse_json_body(response)
        version = data.get("Version") or {}
        if not isinstance(version, dict):
            version = {}
        info = DeviceInfo(
            manufacturer=str(data.get("Manufacturer", "")),
            model=str(data.get("Model", "")),
            serial=str(data.get("SN") or data.get("Sn") or ""),
            brightness=int(data.get("Brightness", 0) or 0),
            degree=int(data.get("Degree", 0) or 0),
            app_version=str(version.get("App", "")),
            firmware_version=str(version.get("Firmware", "")),
            hardware_version=str(version.get("Hardware", "")),
            boot_finish=int(data.get("BootFinish", 0) or 0),
            realtime_display=int(data.get("RealtimeDisplay", 0) or 0),
            extended_display=int(data.get("ExtendedDisplay", 0) or 0),
            raw=data,
        )
        _LOGGER.info(
            "connected to %s (%s) ExtendedDisplay=%s RealtimeDisplay=%s",
            info.model,
            info.serial,
            info.extended_display,
            info.realtime_display,
        )
        return info

    def set_extended_display(self, enable: bool = True, *, retries: int = 3) -> bool:
        """Leave firmware splash / onboard animation and accept host framebuffer.

        Matches the captured ``POST extendedDisplay`` (SeqNumber=110).
        """
        body = json.dumps({"enable": bool(enable)}, separators=(",", ":"))
        for attempt in range(retries):
            if self._command_with_ack("extendedDisplay 1", body, SEQ_EXTENDED_DISPLAY):
                _LOGGER.info("extendedDisplay -> %s", enable)
                return True
            time.sleep(1.0)
            _LOGGER.debug("extendedDisplay retry %s/%s", attempt + 1, retries)
        raise HidError("failed to set extendedDisplay")

    def set_realtime_display(self, enable: bool = True, *, retries: int = 2) -> bool:
        """Enable firmware realtime host-content path (SeqNumber=112)."""
        body = json.dumps({"enable": bool(enable)}, separators=(",", ":"))
        for attempt in range(retries):
            if self._command_with_ack("realtimeDisplay 1", body, SEQ_REALTIME_DISPLAY):
                _LOGGER.info("realtimeDisplay -> %s", enable)
                return True
            time.sleep(0.5)
        _LOGGER.warning("realtimeDisplay set failed (non-fatal)")
        return False

    def enable_host_display(self) -> DeviceInfo:
        """Cold-boot handoff: conn + extendedDisplay(+realtime), matching the observed cold-boot sequence.

        Without this, firmware keeps showing its default boot image even if JPEG
        frames are later streamed over USB interface 0.
        """
        info = self.connect_session()
        # Always assert: after cold boot, splash can remain until this POST succeeds.
        self.set_extended_display(True)
        self.set_realtime_display(True)
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

def enable_host_display() -> DeviceInfo:
    """Module helper used by display paths before USB JPEG streaming."""
    with P13HidController() as hid_dev:
        return hid_dev.enable_host_display()
