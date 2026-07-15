# MSI MPG CoreLiquid P13 360 — Linux driver

Userspace driver and CLI for the **MSI MPG CoreLiquid P13 360** (and P13 360 WHITE) AIO on Linux.

The P13 is **not** the same device family as the older [MSI MPG Coreliquid K360](https://github.com/liquidctl/liquidctl/blob/main/docs/msi-mpg-coreliquid-guide.md) supported by liquidctl. The P13 uses an **Artinchip USB display** (`33c3:0e02`) for its 480×480 IPS panel plus a separate **HID control channel** on the same composite USB device.

## What works today

| Feature                         | Linux support        | Notes                                                          |
| ------------------------------- | -------------------- | -------------------------------------------------------------- |
| Extended monitor on LCD         | **Implemented**      | EVDI virtual monitor (`p13ctl display desktop`)                |
| LCD custom image / test pattern | **Implemented**      | JPEG streaming via Artinchip USB protocol                      |
| System monitor on LCD           | **Implemented**      | Five software-rendered styles (`p13ctl sysmon --style 1..5`)   |
| Brightness / rotation (HID)     | **Implemented**      | `p13ctl hid brightness` / `p13ctl hid rotate`                  |
| Leave firmware splash (HID)     | **Implemented**      | Auto on display connect; or `p13ctl hid host on`               |
| Display layout persistence      | **Implemented**      | Position, orientation, brightness, and Display Mode in `~/.config/p13ctl/` |
| Boot auto-start | **Implemented** | `p13-display.service` applies saved Display Mode at login |
| GUI control panel               | **Implemented**      | `p13ctl-gui` — display mode toggle and layout reset            |
| Pump / radiator fans            | **Motherboard PWM**  | Use BIOS/fancontrol — not USB-controlled on P13                |
| ARGB lighting                   | **Motherboard ARGB** | Use OpenRGB / motherboard software — not USB-controlled        |

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

`install.sh` installs EVDI, system dependencies, udev access rules, and `p13ctl` into a local virtualenv. `p13-display.service` starts the saved **Display Mode** at graphical login, while `p13-panel-off.service` sends HID brightness 0 during logout, shutdown, or restart. For shutdown and restart it holds a systemd-logind delay lock (the same mechanism desktop apps use for pre-shutdown cleanup), so the machine waits until the panel is off.

```bash
./install.sh --no-boot-display   # skip login autostart
./install.sh --no-gui            # skip GUI / PySide6
./install.sh --no-evdi           # omit extended-monitor support
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

`p13ctl display desktop` creates a 480×480 extended monitor that can be arranged in your desktop settings. Use `p13ctl display desktop --capture` to mirror an existing screen without EVDI.

System monitor style 5 is a five-metric dashboard with circular CPU/GPU gauges
and RAM usage. Add a photo or looping video behind it with
`p13ctl sysmon --style 5 --background /path/to/background.jpg`.
## Project layout

## License

GPL-3.0-or-later. Artinchip display code is adapted from [hevnsnt/artinchip-linux](https://github.com/hevnsnt/artinchip-linux) (MIT-style community RE).
