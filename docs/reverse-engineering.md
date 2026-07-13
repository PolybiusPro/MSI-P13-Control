# Reverse engineering the P13 panel

The P13's USB protocol was worked out by capturing the device's USB traffic
with Wireshark / USBPcap on Windows and replaying the observed HID reports on
Linux. See [../tools/windows/README.md](../tools/windows/README.md) for the
capture workflow and [protocol-hid.md](protocol-hid.md) for the decoded HID
protocol.

## Device enumeration

```
USB\VID_33C3&PID_0E02\BYZL2525WC07AM007084   # composite root
USB\VID_33C3&PID_0E02&MI_00\...              # AicUsbDisplay Device
USB\VID_33C3&PID_0E02&MI_01\...              # HID
DISPLAY\AIC010E\...                            # 480×480 USB monitor
```

## HID protocol summary (from USB captures)

| Item | Value |
| --- | --- |
| Frame sync | `0x5A` … `0x5A` |
| Length | BE u16 = payload + 5 (escaped on wire) |
| Checksum | `(sum(payload) + len_hi + len_lo) & 0xFF` |
| Escape | `5A`→`5B 01`, `5B`→`5B 02` (length + payload) |
| Brightness | `POST brightness 1` + `{"value":N}` |
| Rotation | `POST rotate 1` + `{"degree":N}` |
| Connect | `POST conn 1` → JSON device info |
| HID report | 1025 bytes, report ID `0x00` at offset 0 |

See [protocol-hid.md](protocol-hid.md).

## What the panel does *not* control over USB

- Radiator fan PWM → motherboard fan header
- Pump PWM → `PUMP_FAN` header
- ARGB → `ARGB_V2` or JAF connector

Only the **LCD** and its firmware settings use USB.

## Artinchip display RE

Already documented by the community:

- [hevnsnt/artinchip-linux](https://github.com/hevnsnt/artinchip-linux) — `tinyscreen.py`
- Artinchip vendor docs — USB display install (`aicdoc.artinchip.com`)

## Suggested upstream contributions

1. **liquidctl** — new device class for Artinchip + HID hybrids (not K360-compatible).
2. **artinchip-linux** — confirm 480×480 panel params for P13 serial prefix `BYZL`.
3. **OpenRGB** — only if a separate ARGB USB device is found (P13 fans on motherboard are not).
