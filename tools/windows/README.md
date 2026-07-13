# Windows USB capture helpers

Notes for capturing the P13's USB traffic on Windows to work out its protocol.
Run from a PowerShell session with the P13 connected.

## USB capture

1. Install [USBPcap](https://desowin.org/usbpcap/) and Wireshark.
2. Capture the bus with the P13 attached.
3. Filter: `usb.idVendor == 0x33c3 && usb.idProduct == 0x0e02`
4. Change **one** panel setting at a time (brightness, rotation, clock).
5. Export the relevant HID URB payloads as hex for `docs/protocol-hid.md`.

## Device Manager IDs (reference)

```
USB\VID_33C3&PID_0E02&MI_00  → AicUsbDisplay Device
USB\VID_33C3&PID_0E02&MI_01  → HID
DISPLAY\AIC010E              → 480×480 panel
```

Serial numbers often start with `BYZL` for P13 units.
