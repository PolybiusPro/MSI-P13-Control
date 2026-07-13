"""Frame encoder/decoder for the P13 HID control channel.

Frame layout observed in captured USB HID traffic.

Frame layout:
    0x5A | length_hi | length_lo | payload... | checksum | 0x5A

    length   = len(payload) + 5  (entire frame size, big-endian u16)
    checksum = sum(payload) & 0xFF
"""

from __future__ import annotations

FRAME_START = 0x5A
FRAME_END = 0x5A
FRAME_SIZE_MAX = 1024

class ProtocolError(ValueError):
    pass

def checksum(payload: bytes) -> int:
    return sum(payload) & 0xFF

def encode(payload: bytes) -> bytes:
    if len(payload) > FRAME_SIZE_MAX - 5:
        raise ProtocolError(f"payload exceeds {FRAME_SIZE_MAX - 5} bytes")
    total_len = len(payload) + 5
    return (
        bytes([FRAME_START, (total_len >> 8) & 0xFF, total_len & 0xFF])
        + payload
        + bytes([checksum(payload), FRAME_END])
    )

def decode(frame: bytes) -> bytes:
    if len(frame) < 5:
        raise ProtocolError("frame too short")
    if frame[0] != FRAME_START or frame[-1] != FRAME_END:
        raise ProtocolError("invalid frame delimiters")
    total_len = (frame[1] << 8) | frame[2]
    if total_len != len(frame):
        raise ProtocolError(f"length mismatch: header={total_len} actual={len(frame)}")
    payload = frame[3:-2]
    if checksum(payload) != frame[-2]:
        raise ProtocolError("checksum mismatch")
    return payload
