# MSI MPG CoreLiquid P13 360 — Linux driver

Userspace driver and CLI for the **MSI MPG CoreLiquid P13 360** (and P13 360 WHITE) AIO on Linux.

The P13 is **not** the same device family as the older [MSI MPG Coreliquid K360](https://github.com/liquidctl/liquidctl/blob/main/docs/msi-mpg-coreliquid-guide.md) supported by liquidctl. The P13 uses an **Artinchip USB display** (`33c3:0e02`) for its 480×480 IPS panel plus a separate **HID control channel** on the same composite USB device.

## What works today

| Feature | Linux support | Notes |
| --- | --- | --- |
| Desktop on LCD @ 60fps | **Implemented** | EVDI virtual monitor (`p13ctl display desktop`) |
| LCD custom image / test pattern | **Implemented** | JPEG streaming via Artinchip USB protocol |
| System monitor on LCD | **Experimental** | Software-rendered HUD (`p13ctl sysmon`) |
| Brightness / rotation (HID) | **Implemented** | `p13ctl hid brightness` / `p13ctl hid rotate` |
| Display layout persistence | **Implemented** | Position, orientation, brightness saved to `~/.config/p13ctl/` |
| Boot auto-start | **Implemented** | `p13-display.service` starts virtual monitor at login |
| GUI control panel | **Implemented** | `p13ctl-gui` — extended display and quick actions |
| Pump / radiator fans | **Motherboard PWM** | Use BIOS/fancontrol — not USB-controlled on P13 |
| ARGB lighting | **Motherboard ARGB** | Use OpenRGB / motherboard software — not USB-controlled |

## Hardware summary

```
USB composite device: 33c3:0e02  (serial often BYZL…)
├── Interface 0: Artinchip AicUsbDisplay  (480×480, bulk JPEG frames)
└── Interface 1: HID vendor control       (framed HID reports)

Monitor EDID: AIC010E
```

Pump, fans, and ARGB connect to standard motherboard headers. Only the LCD requires this driver.

## Quick start

```bash
./install.sh
source .venv/bin/activate
p13ctl list
p13ctl display desktop
p13ctl-gui
```

`install.sh` installs system dependencies, EVDI (virtual monitor), udev rules, and `p13ctl` into a local virtualenv. It also enables `p13-display.service` so the panel mirrors your desktop at login, and installs a **MSI P13 Control** launcher for the GUI. No environment variables required.

```bash
./install.sh --no-boot-display   # skip login autostart
./install.sh --no-gui            # skip GUI / PySide6
systemctl --user status p13-display.service
p13ctl-gui
```

```bash
./uninstall.sh                  # remove p13ctl artifacts
./uninstall.sh --remove-config  # also delete saved display settings
```

If the Artinchip kernel driver (`aic_usb_display`) conflicts with userspace access:

```bash
./install.sh --blacklist-aic
```

See [docs/protocol-display.md](docs/protocol-display.md) for details.
## Project layout

## License

GPL-3.0-or-later. Artinchip display code is adapted from [hevnsnt/artinchip-linux](https://github.com/hevnsnt/artinchip-linux) (MIT-style community RE).
