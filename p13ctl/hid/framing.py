"""Frame encoder/decoder for the P13 HID control channel.

Frame layout observed in captured USB HID traffic.

Wire layout:
    5A | escape(len_hi, len_lo, payload...) | checksum | 5A

    logical_len = len(payload) + 5
    checksum    = (sum(payload) + len_hi + len_lo) & 0xFF

Escape (applied to length bytes and payload only):
    0x5A -> 0x5B 0x01
    0x5B -> 0x5B 0x02
"""

from __future__ import annotations

FRAME_START = 0x5A
FRAME_END = 0x5A
ESCAPE = 0x5B
ESCAPE_5A = 0x01
ESCAPE_5B = 0x02
FRAME_SIZE_MAX = 1024

class ProtocolError(ValueError):
    pass

def checksum(payload: bytes, length_hi: int, length_lo: int) -> int:
    return (sum(payload) + length_hi + length_lo) & 0xFF

def _escape_byte(byte: int, out: bytearray) -> None:
    if byte == FRAME_START:
        out.append(ESCAPE)
        out.append(ESCAPE_5A)
    elif byte == ESCAPE:
        out.append(ESCAPE)
        out.append(ESCAPE_5B)
    else:
        out.append(byte)

def encode(payload: bytes) -> bytes:
    if len(payload) > FRAME_SIZE_MAX - 5:
        raise ProtocolError(f"payload exceeds {FRAME_SIZE_MAX - 5} bytes")
    logical_len = len(payload) + 5
    length_hi = (logical_len >> 8) & 0xFF
    length_lo = logical_len & 0xFF
    body = bytearray()
    _escape_byte(length_hi, body)
    _escape_byte(length_lo, body)
    for byte in payload:
        _escape_byte(byte, body)
    return (
        bytes([FRAME_START])
        + body
        + bytes([checksum(payload, length_hi, length_lo), FRAME_END])
    )

def decode(frame: bytes) -> bytes:
    """Decode a frame.

    ``frame`` may include trailing HID padding; only the logical frame is used.
    """
    if not frame or frame[0] != FRAME_START:
        raise ProtocolError("missing frame start marker")

    raw = bytearray()
    i = 1
    logical_len: int | None = None
    needed_raw = 2

    while len(raw) < needed_raw or logical_len is None:
        if i >= len(frame):
            raise ProtocolError("truncated frame while unescaping")
        if frame[i] == ESCAPE:
            if i + 1 >= len(frame):
                raise ProtocolError("truncated escape sequence")
            code = frame[i + 1]
            if code == ESCAPE_5A:
                raw.append(FRAME_START)
            elif code == ESCAPE_5B:
                raw.append(ESCAPE)
            else:
                raise ProtocolError(f"invalid escape code 0x{code:02x}")
            i += 2
        else:
            if frame[i] == FRAME_START:
                raise ProtocolError("unescaped 0x5A in frame body")
            raw.append(frame[i])
            i += 1

        if logical_len is None and len(raw) >= 2:
            logical_len = (raw[0] << 8) | raw[1]
            if logical_len < 5 or logical_len > FRAME_SIZE_MAX:
                raise ProtocolError(f"invalid logical length {logical_len}")
            needed_raw = logical_len - 3  # length_hi, length_lo, payload

    if i + 1 >= len(frame):
        raise ProtocolError("truncated checksum/end marker")
    received_ck = frame[i]
    if frame[i + 1] != FRAME_END:
        raise ProtocolError("invalid frame end marker")

    length_hi, length_lo = raw[0], raw[1]
    payload = bytes(raw[2:])
    if len(payload) + 5 != logical_len:
        raise ProtocolError("payload length mismatch")
    expected_ck = checksum(payload, length_hi, length_lo)
    if received_ck != expected_ck:
        raise ProtocolError(
            f"checksum mismatch: expected={expected_ck:#02x} actual={received_ck:#02x}"
        )
    return payload
