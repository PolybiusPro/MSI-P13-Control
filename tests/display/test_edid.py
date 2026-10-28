"""The EDID advertised by the virtual monitor."""

from __future__ import annotations

import pytest

from p13ctl.display.edid import generate_edid


def test_edid_is_a_valid_base_block() -> None:
    edid = generate_edid()
    assert len(edid) == 128
    assert edid[:8] == b"\x00\xff\xff\xff\xff\xff\xff\x00"
    assert sum(edid) % 256 == 0


def test_edid_identifies_as_artinchip_aic010e() -> None:
    edid = generate_edid()
    mfg = int.from_bytes(edid[8:10], "big")
    letters = "".join(chr(ord("A") - 1 + ((mfg >> shift) & 0x1F)) for shift in (10, 5, 0))
    assert letters == "AIC"
    assert int.from_bytes(edid[10:12], "little") == 0x010E


def test_edid_preferred_timing_is_480x480_at_60hz() -> None:
    edid = generate_edid()
    dtd = edid[54:72]
    h_active = dtd[2] | ((dtd[4] >> 4) << 8)
    h_blank = dtd[3] | ((dtd[4] & 0xF) << 8)
    v_active = dtd[5] | ((dtd[7] >> 4) << 8)
    v_blank = dtd[6] | ((dtd[7] & 0xF) << 8)
    clock_hz = int.from_bytes(dtd[0:2], "little") * 10_000
    assert (h_active, v_active) == (480, 480)
    refresh = clock_hz / ((h_active + h_blank) * (v_active + v_blank))
    assert refresh == pytest.approx(60, abs=0.5)
