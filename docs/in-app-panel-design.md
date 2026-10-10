# In-app panel control (no user services)

## Goal

The Python GUI application owns the P13 panel connection. No systemd user
services run in the background. Closing the window hides it to the system tray
and the panel keeps running; Quit from the tray menu is the only thing that
stops the panel.

## Decisions

- Approach A: remove the service code paths from the existing `MainWindow`
  and make the in-process workers (`MirrorWorker`, `FaceWorker`) the only path.
  No headless controller refactor.
- `evdi-p13.service` (root, loads the EVDI module after DKMS) stays.
- Sleep watching is removed entirely (see Removals).
- Quit sends a black frame before exiting.

## Lifecycle

- Launch at login: XDG autostart entry `~/.config/autostart/p13ctl-gui.desktop`
  running `p13ctl-gui --minimized`. The app starts in the tray with no window
  and applies the saved Display Mode. Launching from the app menu shows the
  window.
- Single instance: a `QLocalServer` guard (`gui/single_instance.py`). A second
  launch tells the first instance to show its window, then exits. Needed
  because only one process can hold the USB display interface.
- Close button: hides the window; `setQuitOnLastWindowClosed(False)`.
- Tray (`gui/tray.py`): icon with a menu containing Show, current mode, Quit.
- Quit (`MainWindow.quit_app()`): stop workers, `wait_for_display_usb()`,
  `send_black_image()`, exit. This is the previous `shutdown()` plus the black
  frame.
- Session end: SIGTERM from the session manager runs the same quit path. The
  Python signal handler is driven by a short `QTimer` so the Qt event loop
  gives it a chance to run.
- Errors while the window is hidden surface as tray notifications instead of
  modal dialogs.

## Login behaviour

Today a failed mode start saves Display Mode = off. At login this would
overwrite the saved mode if the panel has not enumerated yet. The autostart
apply path therefore never persists "off" on failure and retries every
10 seconds until the panel is available (parity with the old
`Restart=on-failure` / `RestartSec=10`). The retry stops when the user selects
a mode manually.

## Removals

- `tools/linux/p13-display.service.in`, `tools/linux/p13-panel-off.service.in`
- `p13ctl/gui/service.py`, `p13ctl/display/service.py`, `tests/test_service.py`
- `stop_display_service()` call in `send_black_image()` and its test stub
- `p13ctl/display/sleepwatch.py` and `p13ctl/display/shutdown_inhibit.py`
  (nothing else uses them), and the `display sleepwatch` CLI command
- `p13ctl display mode` and `run_saved_display_mode()` (only the unit used
  them; the GUI applies the saved mode itself)
- All `mirror_service_installed()` branches in `gui/window.py`

One-shot CLI commands (`display off|test|image|desktop`, `sysmon`, `clock`)
stay. While the app is running they report "USB busy", which is correct.

## Behaviour changes to be aware of

- The panel is driven only while the app runs. Headless and `--no-gui`
  installs lose boot autostart.
- The panel no longer blanks when the desktop displays sleep in sysmon, clock,
  image or test modes. Extended mode still follows DPMS through EVDI.
- No logind shutdown delay lock. Blanking at shutdown relies on the app
  handling session end (SIGTERM) before the session is torn down. If the app
  cannot finish in time the panel keeps its last frame.

## Installer

- `install.sh` writes the autostart entry from
  `tools/linux/p13ctl-gui-autostart.desktop.in`, and removes legacy units if
  present (`systemctl --user disable --now`, delete files, daemon-reload).
- `--no-boot-display` is replaced by `--no-autostart`.
- `uninstall.sh` removes the autostart entry and sweeps leftover legacy units.
- EVDI units, udev rules and dracut config are untouched.
- README and `docs/architecture.md` are updated.

## Testing

- Unit: single-instance guard (Qt offscreen), the login retry rule (must not
  persist "off"), updated `tests/test_mode.py`, quit path ordering.
- Not verifiable without hardware: tray behaviour on KDE Wayland, the real
  black frame on logout and shutdown, hot-plug at login.
