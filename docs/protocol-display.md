# Artinchip USB display protocol (33c3:0e02, interface 0)

The P13 LCD uses an **Artinchip Technology** USB display bridge—the same family as generic “USB bar monitors” (`33c3:0e02`).

| Property | P13 value |
| --- | --- |
| USB VID:PID | `33c3:0e02` |
| Interface 0 class | Vendor-specific (display) |
| Bulk OUT / IN | `0x01` / `0x81` |
| Panel resolution | **480 × 480** (also returned by vendor GET_PARAMS) |
| Frame format | **JPEG** compressed blobs |
| Authentication | **RSA PKCS#1 v1.5** challenge/response before frames are accepted |

## Protocol constants

| Name | Value |
| --- | --- |
| `FRAME_START_MAGIC` | `0xA1C62B01` |
| `AUTH_DEV_MAGIC` | `0xA1C62B10` |
| `AUTH_HOST_MAGIC` | `0xA1C62B11` |
| Max bulk chunk | `4096 * 64` bytes |

## Session flow

1. **GET_PARAMS** — vendor control transfer `bmRequestType=0xC0`, `bRequest=0`, 256-byte response → width, height, pixel format, FPS.
2. **AUTH_DEV** — host encrypts random challenge with embedded RSA public key; device decrypts and echoes challenge.
3. **AUTH_HOST** — device sends signed blob; host recovers plaintext via RSA public operation and returns it.
4. **FRAME** — header `{magic, jpeg_len, frame_id, media_format, 0, magic}` then JPEG bytes in chunks.

The public key is embedded in Artinchip’s `aic-render` binary and reproduced in `p13ctl/display/artinchip.py` (from [hevnsnt/artinchip-linux](https://github.com/hevnsnt/artinchip-linux)).

## Linux kernel interaction

Artinchip ships `aic_drm` / `aic_usb_display` drivers that create a DRM framebuffer. They conflict with direct userspace access and with some proprietary GPU stacks.

**Recommended for p13ctl:**

```bash
# Prevent kernel driver from binding (example modprobe drop-in)
echo 'blacklist aic_usb_display' | sudo tee /etc/modprobe.d/blacklist-aic-usb-display.conf
sudo modprobe -r aic_usb_display 2>/dev/null || true
```

Then run `p13ctl` as root or with udev permissions on `33c3:0e02`.

## Host-display handoff

Leaving the cold-boot firmware splash requires the HID ``POST extendedDisplay {"enable":true}`` command. On Linux, ``p13ctl``:

1. Authenticates Artinchip USB (IF0) and streams JPEG frames.
2. Enables host display mode over HID (IF1) via ``POST extendedDisplay`` so the firmware stops the default splash.
