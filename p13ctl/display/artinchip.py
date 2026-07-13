"""Artinchip USB display driver for MSI P13 (33c3:0e02, interface 0).

Protocol reverse-engineered by the Artinchip Linux community; see:
https://github.com/hevnsnt/artinchip-linux
"""

from __future__ import annotations

import io
import logging
import os
import struct
import time
from typing import Optional

import usb.core
import usb.util
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from PIL import Image

_LOGGER = logging.getLogger(__name__)

FRAME_START_MAGIC = 0xA1C62B01
AUTH_DEV_MAGIC = 0xA1C62B10
AUTH_HOST_MAGIC = 0xA1C62B11

VENDOR_ID = 0x33C3
PRODUCT_ID = 0x0E02
DISPLAY_INTERFACE = 0
EP_OUT = 0x01
EP_IN = 0x81
MAX_TRANSFER = 4096 * 64

# Public key from Artinchip aic-render / tinyscreen (community RE).
RSA_PUB_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAybdtvB1uNA4XICh+xJi1
KJWO0GYal4lNiW69zSMIJFGzb2wkiFBX2txFaH5ZYh0TYdwmjzBqinzTsWhIasW3
rl9QN5cv73zFalO3J4hADXz1g7hlHVB0BKDD280NUKUGAbwDv+KMHTprs+B/T4QU
a0s4RBNnN4fMPk2H0UAWU1jKAvMYjh/YR+MLYbl04ZCLlOfX9zQjRBVan7aLARQg
v5QRahAlAoBsYK864VrBKq91lRCXt4XP5d/sDtZM7kGcpLi2i4xHtRct37M+bkZv
Lf/3aVpAVsqZy5P2NXEe6HMv4Q+YP6QKz2wuk3xWYHWFn+88ydjv394tN28rjl56
hwIDAQAB
-----END PUBLIC KEY-----"""

class DisplayError(RuntimeError):
    pass

def _load_rsa_key():
    return serialization.load_pem_public_key(RSA_PUB_PEM)

def _rsa_public_decrypt(pub_key, ciphertext: bytes) -> bytes:
    n = pub_key.public_numbers().n
    e = pub_key.public_numbers().e
    m = pow(int.from_bytes(ciphertext, "big"), e, n)
    m_bytes = m.to_bytes((n.bit_length() + 7) // 8, "big")
    if m_bytes[0] != 0 or m_bytes[1] != 1:
        raise DisplayError("RSA PKCS#1 type-1 padding invalid")
    idx = 2
    while idx < len(m_bytes) and m_bytes[idx] == 0xFF:
        idx += 1
    if idx >= len(m_bytes) or m_bytes[idx] != 0:
        raise DisplayError("RSA PKCS#1 separator invalid")
    return m_bytes[idx + 1 :]

class ArtinchipDisplay:
    """Drive the P13 LCD via Artinchip bulk JPEG streaming."""

    def __init__(self, rotate: int = 0):
        self._dev: Optional[usb.core.Device] = None
        self.width = 0
        self.height = 0
        self.pixel_format = 0
        self.fps = 0
        self.frame_id = 0
        self.rotate = rotate % 360
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    @staticmethod
    def find():
        return usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)

    def connect(self) -> None:
        dev = self.find()
        if dev is None:
            raise DisplayError("Artinchip display 33c3:0e02 not found")

        try:
            if dev.is_kernel_driver_active(DISPLAY_INTERFACE):
                dev.detach_kernel_driver(DISPLAY_INTERFACE)
        except usb.core.USBError as exc:
            _LOGGER.debug("kernel driver detach: %s", exc)

        try:
            usb.util.claim_interface(dev, DISPLAY_INTERFACE)
        except usb.core.USBError as exc:
            if getattr(exc, "errno", None) == 16:
                raise DisplayError(
                    "Display USB interface is busy — stop the extended display first"
                ) from exc
            raise DisplayError(f"USB claim failed: {exc}") from exc
        self._dev = dev
        self._stop_requested = False
        from .session import register_display

        register_display(self)
        self.width, self.height, self.pixel_format, self.fps = self._get_params()
        if not self._authenticate():
            raise DisplayError("Artinchip RSA authentication failed")
        self.frame_id = 0
        _LOGGER.info("Display connected: %dx%d @ %dfps", self.width, self.height, self.fps)

    def close(self) -> None:
        from .session import unregister_display

        if self._dev is not None:
            try:
                usb.util.release_interface(self._dev, DISPLAY_INTERFACE)
            except usb.core.USBError:
                pass
            unregister_display(self)
            self._dev = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()

    def _bulk_out(self, data: bytes, timeout: int = 5000) -> None:
        assert self._dev is not None
        self._dev.write(EP_OUT, data, timeout=timeout)

    def _bulk_in(self, size: int = 256, timeout: int = 5000) -> bytes:
        assert self._dev is not None
        return bytes(self._dev.read(EP_IN, size, timeout=timeout))

    def _get_params(self) -> tuple[int, int, int, int]:
        assert self._dev is not None
        data = self._dev.ctrl_transfer(
            usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
            0,
            0,
            0,
            256,
            timeout=5000,
        )
        ver, chip, fmt, bus, modes, w, h, fps = struct.unpack_from("<8H", data, 0)
        _LOGGER.debug("params ver=%s chip=%s fmt=%s modes=%s", ver, chip, fmt, modes)
        return w, h, fmt, fps

    def _authenticate(self) -> bool:
        assert self._dev is not None
        pk = _load_rsa_key()
        challenge = os.urandom(32)
        encrypted = pk.encrypt(challenge, asym_padding.PKCS1v15())
        self._bulk_out(struct.pack("<IIHHII", AUTH_DEV_MAGIC, 0x100, 0, 0, 0, AUTH_DEV_MAGIC))
        self._bulk_out(encrypted)
        response = self._bulk_in(256, timeout=3000)
        if len(response) < len(challenge) or response[: len(challenge)] != challenge:
            return False

        self._bulk_out(struct.pack("<IIHHII", AUTH_HOST_MAGIC, 0x100, 0, 0, 0, AUTH_HOST_MAGIC))
        signed = self._bulk_in(256, timeout=3000)
        plaintext = _rsa_public_decrypt(pk, signed)
        self._bulk_out(plaintext)
        return True

    def send_jpeg(self, jpeg_data: bytes, media_format: int = 0) -> None:
        assert self._dev is not None
        header = struct.pack(
            "<IIHHII",
            FRAME_START_MAGIC,
            len(jpeg_data),
            self.frame_id & 0xFFFF,
            media_format,
            0,
            FRAME_START_MAGIC,
        )
        self._bulk_out(header)
        for pos in range(0, len(jpeg_data), MAX_TRANSFER):
            self._bulk_out(bytes(jpeg_data[pos : pos + MAX_TRANSFER]), timeout=10000)
        self.frame_id += 1

    def send_image(self, image: Image.Image, quality: int = 85) -> None:
        target = (self.width or 480, self.height or 480)
        img = image.convert("RGB")
        if img.size != target:
            img = img.resize(target, Image.Resampling.LANCZOS)
        if self.rotate:
            img = img.rotate(-self.rotate, expand=True)
            img = img.resize(target, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        self.send_jpeg(buf.getvalue())

    def show_test_pattern(self) -> None:
        w, h = self.width or 480, self.height or 480
        img = Image.new("RGB", (w, h), (20, 24, 36))
        from PIL import ImageDraw, ImageFont

        draw = ImageDraw.Draw(img)
        draw.rectangle((10, 10, w - 10, h - 10), outline=(0, 180, 255), width=3)
        draw.text((24, 24), "MSI P13\np13ctl test", fill=(230, 240, 255))
        draw.text((24, h - 48), f"{w}x{h}", fill=(120, 200, 255))
        self.send_image(img)

    def show_static_file(self, path: str) -> None:
        with Image.open(path) as img:
            self.send_image(img)

    def run_desktop(
        self,
        interval: float | None = None,
        monitor: int = 0,
        crop: str = "center",
        quality: int = 75,
    ) -> None:
        """Mirror the desktop to the panel until interrupted."""
        from .capture import CropMode, grab_desktop

        crop_mode: CropMode = "center" if crop == "center" else "stretch"
        target_interval = interval
        if target_interval is None and self.fps:
            target_interval = 1.0 / self.fps
        if target_interval is None:
            target_interval = 1.0 / 15

        while not self._stop_requested:
            t0 = time.monotonic()
            image = grab_desktop(monitor=monitor, crop=crop_mode)
            self.send_image(image, quality=quality)
            elapsed = time.monotonic() - t0
            sleep_for = target_interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    def run_sysmon(self, interval: float = 1.0) -> None:
        try:
            import psutil
        except ImportError as exc:
            raise DisplayError("sysmon requires: pip install p13ctl[sysmon]") from exc

        from PIL import ImageDraw, ImageFont

        w, h = self.width or 480, self.height or 480
        while not self._stop_requested:
            img = Image.new("RGB", (w, h), (12, 14, 20))
            draw = ImageDraw.Draw(img)
            temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
            cpu_temp = None
            for name, entries in temps.items():
                if "core" in name.lower() or "cpu" in name.lower() or "k10" in name.lower():
                    if entries:
                        cpu_temp = entries[0].current
                        break
            cpu = psutil.cpu_percent()
            mem = psutil.virtual_memory().percent
            lines = [
                "P13 sysmon",
                f"CPU {cpu:4.0f}%",
                f"RAM {mem:4.0f}%",
            ]
            if cpu_temp is not None:
                lines.append(f"CPU {cpu_temp:.0f} C")
            y = 36
            for line in lines:
                draw.text((32, y), line, fill=(180, 220, 255))
                y += 44
            self.send_image(img)
            time.sleep(interval)
