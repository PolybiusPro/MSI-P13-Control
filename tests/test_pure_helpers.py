"""Pure helpers: EDID generation and the RSA public-key decrypt used for auth."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from p13ctl.display.artinchip import DisplayError, _rsa_public_decrypt
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


def _sign_raw(key: rsa.RSAPrivateKey, padded: bytes) -> bytes:
    numbers = key.private_numbers()
    n = numbers.public_numbers.n
    signed = pow(int.from_bytes(padded, "big"), numbers.d, n)
    return signed.to_bytes((n.bit_length() + 7) // 8, "big")


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_rsa_public_decrypt_recovers_payload(key) -> None:
    payload = b"challenge-bytes"
    padded = b"\x00\x01" + b"\xff" * (256 - 3 - len(payload)) + b"\x00" + payload
    assert _rsa_public_decrypt(key.public_key(), _sign_raw(key, padded)) == payload


@pytest.mark.parametrize(
    ("prefix", "message"),
    [(b"\x00\x02", "padding invalid"), (b"\x00\x01", "separator invalid")],
)
def test_rsa_public_decrypt_rejects_bad_padding(key, prefix: bytes, message: str) -> None:
    # Type-2 padding, or type-1 with no 0x00 separator after the 0xFF run.
    fill = b"\xaa" if prefix == b"\x00\x02" else b"\xff"
    padded = prefix + fill * (256 - len(prefix))
    with pytest.raises(DisplayError, match=message):
        _rsa_public_decrypt(key.public_key(), _sign_raw(key, padded))
