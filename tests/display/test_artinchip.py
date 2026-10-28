"""ArtinchipDisplay: reconnect after re-enumeration, connect failures, RSA auth."""

from __future__ import annotations

import errno

import pytest
import usb.core
import usb.util
from cryptography.hazmat.primitives.asymmetric import rsa

from p13ctl.display import artinchip
from p13ctl.display.artinchip import (
    DISPLAY_INTERFACE,
    ArtinchipDisplay,
    DisplayError,
    _rsa_public_decrypt,
)


def _display(first_error: int | None):
    display = ArtinchipDisplay()
    calls = {"send": 0, "reconnect": 0}

    def fake_send(jpeg_data: bytes, media_format: int) -> None:
        calls["send"] += 1
        if calls["send"] == 1 and first_error is not None:
            raise usb.core.USBError("stale handle", errno=first_error)

    def fake_reconnect(timeout: float = 10.0) -> None:
        calls["reconnect"] += 1

    display._send_jpeg = fake_send  # type: ignore[method-assign]
    display._reconnect = fake_reconnect  # type: ignore[method-assign]
    return display, calls


@pytest.mark.parametrize("code", [errno.ENODEV, errno.EIO])
def test_stale_handle_reconnects_and_retries(code: int) -> None:
    display, calls = _display(code)
    display.send_jpeg(b"jpeg")
    assert calls == {"send": 2, "reconnect": 1}


def test_other_usb_errors_propagate() -> None:
    display, calls = _display(errno.EBUSY)
    with pytest.raises(usb.core.USBError):
        display.send_jpeg(b"jpeg")
    assert calls == {"send": 1, "reconnect": 0}


def test_reconnect_failing_again_propagates() -> None:
    display = ArtinchipDisplay()
    sends = []

    def always_stale(jpeg_data: bytes, media_format: int) -> None:
        sends.append(1)
        raise usb.core.USBError("stale handle", errno=errno.EIO)

    display._send_jpeg = always_stale  # type: ignore[method-assign]
    display._reconnect = lambda timeout=10.0: None  # type: ignore[method-assign]
    with pytest.raises(usb.core.USBError):
        display.send_jpeg(b"jpeg")
    assert len(sends) == 2


def test_connect_keeps_a_pending_stop(fake_panel) -> None:
    display = ArtinchipDisplay(enable_host=False)
    display.request_stop()
    display.connect()
    assert display._stop_requested
    display.close()


@pytest.mark.parametrize("failure", ["params", "auth_error", "auth_rejected"])
def test_connect_failure_releases_the_interface(monkeypatch, fake_panel, failure) -> None:
    released: list[int] = []
    monkeypatch.setattr(usb.util, "release_interface", lambda _dev, i: released.append(i))
    if failure == "params":

        def fail(self):
            raise usb.core.USBError("no params", errno=errno.EIO)

        monkeypatch.setattr(ArtinchipDisplay, "_get_params", fail)
        expected: type[Exception] = usb.core.USBError
    elif failure == "auth_error":

        def fail_auth(self):
            raise usb.core.USBError("auth timeout", errno=errno.ETIMEDOUT)

        monkeypatch.setattr(ArtinchipDisplay, "_authenticate", fail_auth)
        expected = usb.core.USBError
    else:
        monkeypatch.setattr(ArtinchipDisplay, "_authenticate", lambda self: False)
        expected = DisplayError

    display = ArtinchipDisplay(enable_host=False)
    with pytest.raises(expected):
        display.connect()
    assert released == [DISPLAY_INTERFACE]
    assert display._dev is None


def test_reconnect_retries_raw_usb_errors(monkeypatch) -> None:
    monkeypatch.setattr(artinchip, "_RECONNECT_RETRY_S", 0)
    display = ArtinchipDisplay()
    attempts: list[int] = []

    def flaky() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise usb.core.USBError("re-enumerating", errno=errno.ENODEV)

    display.connect = flaky  # type: ignore[method-assign]
    display._reconnect()
    assert len(attempts) == 2


def test_reconnect_gives_up_when_stop_requested() -> None:
    display = ArtinchipDisplay()
    display.request_stop()
    display.connect = lambda: pytest.fail("reconnected after stop")  # type: ignore[method-assign]
    with pytest.raises(DisplayError):
        display._reconnect()


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
