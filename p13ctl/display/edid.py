"""Generate a valid 128-byte EDID for the MSI P13 480x480 panel."""

from __future__ import annotations

import struct

def generate_edid() -> bytes:
    """Build a minimal valid EDID 1.3 block for 480x480 @ 60Hz."""
    edid = bytearray(128)

    edid[0:8] = b"\x00\xFF\xFF\xFF\xFF\xFF\xFF\x00"

    # Manufacturer ID "AIC" (Artinchip, matches Windows AIC010E EDID)
    mfg = (1 << 10) | (9 << 5) | 3
    struct.pack_into(">H", edid, 8, mfg)

    struct.pack_into("<H", edid, 10, 0x010E)  # product code (AIC010E)
    struct.pack_into("<I", edid, 12, 1)  # serial
    edid[16] = 1  # week
    edid[17] = 2026 - 1990  # year

    edid[18] = 1  # EDID version
    edid[19] = 3
    edid[20] = 0x80  # digital input
    edid[21] = 5  # width cm (~48mm)
    edid[22] = 5  # height cm
    edid[23] = 120  # gamma 2.2
    edid[24] = 0x0A  # RGB, preferred timing in DTD1
    edid[25:35] = bytes([0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54])
    edid[35] = edid[36] = edid[37] = 0x00

    for i in range(38, 54, 2):
        edid[i] = 0x01
        edid[i + 1] = 0x01

    # DTD #1: 480x480 @ 60Hz
    # H: 480 + 160 blank = 640, V: 480 + 20 blank = 500
    # Pixel clock = 640 * 500 * 60 = 19.2 MHz
    pixel_clock = 1920
    h_active, h_blank = 480, 160
    v_active, v_blank = 480, 20
    h_front, h_sync = 48, 32
    v_front, v_sync = 3, 5

    struct.pack_into("<H", edid, 54, pixel_clock)
    edid[56] = h_active & 0xFF
    edid[57] = h_blank & 0xFF
    edid[58] = ((h_active >> 8) & 0xF) << 4 | ((h_blank >> 8) & 0xF)
    edid[59] = v_active & 0xFF
    edid[60] = v_blank & 0xFF
    edid[61] = ((v_active >> 8) & 0xF) << 4 | ((v_blank >> 8) & 0xF)
    edid[62] = h_front & 0xFF
    edid[63] = h_sync & 0xFF
    edid[64] = ((v_front & 0xF) << 4) | (v_sync & 0xF)
    edid[65] = (
        (((h_front >> 8) & 0x3) << 6)
        | (((h_sync >> 8) & 0x3) << 4)
        | (((v_front >> 4) & 0x3) << 2)
        | ((v_sync >> 4) & 0x3)
    )
    edid[66] = 48 & 0xFF
    edid[67] = 48 & 0xFF
    edid[68] = 0
    edid[69] = edid[70] = 0
    edid[71] = 0x18

    # Monitor name
    edid[72:75] = b"\x00\x00\x00"
    edid[75] = 0xFC
    edid[76] = 0x00
    name_str = b"MSI P13"
    edid[77:90] = name_str + b"\n" + b" " * (13 - len(name_str) - 1)

    # Range limits
    edid[90:93] = b"\x00\x00\x00"
    edid[93] = 0xFD
    edid[94] = 0x00
    edid[95] = 56
    edid[96] = 76
    edid[97] = 30
    edid[98] = 81
    edid[99] = 2  # max pixel clock / 10 MHz (ceil(19.2/10) = 2)
    edid[100:108] = b"\x00" * 8

    edid[108:126] = b"\x00" * 18
    edid[126] = 0
    edid[127] = (256 - (sum(edid[:127]) % 256)) % 256

    return bytes(edid)
