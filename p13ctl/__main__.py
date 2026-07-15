"""CLI for MSI MPG CoreLiquid P13 360."""

from __future__ import annotations

import argparse
import logging
import sys

from p13ctl.device import list_devices
from p13ctl.display.artinchip import ArtinchipDisplay, DisplayError
from p13ctl.display.layout import (
    apply_content_rotation,
    load_display_config,
    resolve_stream_settings,
    update_saved_panel,
)
from p13ctl.hid.msi_p13 import HidError, P13HidController

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

def cmd_list(_args: argparse.Namespace) -> int:
    devices = list_devices()
    if not devices:
        print("No MSI P13 devices (33c3:0e02) found.")
        return 1
    for dev in devices:
        print(f"MSI MPG CoreLiquid P13")
        print(f"  USB ID:      {dev.usb_id}")
        if dev.serial:
            print(f"  Serial:      {dev.serial}")
        if dev.manufacturer:
            print(f"  Manufacturer: {dev.manufacturer}")
        if dev.product:
            print(f"  Product:     {dev.product}")
        print(f"  Display IF:  {dev.display_interface}")
        print(f"  HID IF:      {dev.hid_interface}")
        if dev.hid_path:
            print(f"  HID path:    {dev.hid_path}")
    return 0

def _stream_settings(args: argparse.Namespace) -> dict[str, int]:
    config = load_display_config()
    return resolve_stream_settings(
        config,
        fps=getattr(args, "fps", None),
        quality=getattr(args, "quality", None),
        rotate=getattr(args, "rotate", None),
    )

def cmd_display_test(args: argparse.Namespace) -> int:
    stream = _stream_settings(args)
    try:
        with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
            disp.show_test_pattern()
        print("Test pattern sent.")
        return 0
    except DisplayError as exc:
        print(f"Display error: {exc}", file=sys.stderr)
        return 1

def cmd_display_image(args: argparse.Namespace) -> int:
    stream = _stream_settings(args)
    try:
        with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
            disp.show_static_file(args.path)
        print("Image sent.")
        return 0
    except DisplayError as exc:
        print(f"Display error: {exc}", file=sys.stderr)
        return 1

def cmd_display_desktop(args: argparse.Namespace) -> int:
    stream = _stream_settings(args)
    interval = args.interval
    if interval is None:
        interval = 1.0 / max(1, stream["fps"])
    try:
        with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
            print("Mirroring desktop via userspace screen capture (Ctrl+C to stop)...")
            disp.run_desktop(
                interval=interval,
                monitor=args.monitor,
                crop=args.crop,
                quality=stream["quality"],
            )
    except KeyboardInterrupt:
        print()
        return 0
    except DisplayError as exc:
        print(f"Display error: {exc}", file=sys.stderr)
        return 1
    return 0

def cmd_display_mode(_args: argparse.Namespace) -> int:
    """Apply the saved Display Mode (used at login)."""
    from p13ctl.display.mode import run_saved_display_mode

    try:
        return run_saved_display_mode()
    except KeyboardInterrupt:
        print()
        return 0

def cmd_display_sleepwatch(args: argparse.Namespace) -> int:
    from p13ctl.display.sleepwatch import run_sleep_watch

    return run_sleep_watch(interval=args.interval)

def cmd_display_monitor(args: argparse.Namespace) -> int:
    """Alias for the userspace desktop mirror."""
    return cmd_display_desktop(args)

def cmd_sysmon(args: argparse.Namespace) -> int:
    stream = _stream_settings(args)
    try:
        with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
            print("Running system monitor (Ctrl+C to stop)...")
            items = [i.strip() for i in args.items.split(",") if i.strip()] if args.items else None
            disp.run_sysmon(
                interval=args.interval,
                switch=args.switch,
                items=items,
                background=args.background,
            )
    except KeyboardInterrupt:
        print()
        return 0
    except DisplayError as exc:
        print(f"Display error: {exc}", file=sys.stderr)
        return 1
    return 0

def cmd_clock(args: argparse.Namespace) -> int:
    stream = _stream_settings(args)
    try:
        with ArtinchipDisplay(rotate=stream["rotate"]) as disp:
            print(f"Running clock style {args.style} (Ctrl+C to stop)...")
            disp.run_clock(style=args.style, background=args.background)
    except KeyboardInterrupt:
        print()
        return 0
    except DisplayError as exc:
        print(f"Display error: {exc}", file=sys.stderr)
        return 1
    return 0

def cmd_hid_info(_args: argparse.Namespace) -> int:
    try:
        with P13HidController() as hid_dev:
            info = hid_dev.connect_session()
        print(f"Model:      {info.model}")
        print(f"Serial:     {info.serial}")
        print(f"Brightness: {info.brightness}")
        print(f"Rotation:   {info.degree}")
        print(f"Extended:   {info.extended_display}")
        print(f"Realtime:   {info.realtime_display}")
        print(f"BootFinish: {info.boot_finish}")
        if info.firmware_version:
            print(f"Firmware:   {info.firmware_version}")
        if info.hardware_version:
            print(f"Hardware:   {info.hardware_version}")
        return 0
    except HidError as exc:
        print(f"HID error: {exc}", file=sys.stderr)
        return 1

def cmd_hid_host(args: argparse.Namespace) -> int:
    try:
        with P13HidController() as hid_dev:
            if args.state == "on":
                info = hid_dev.enable_host_display()
                print(
                    f"Host display enabled for {info.model or info.serial or 'P13'} "
                    f"(extended={info.extended_display})."
                )
            else:
                hid_dev.set_extended_display(False)
                hid_dev.set_realtime_display(False)
                print("Host display disabled (firmware may return to default image).")
        return 0
    except HidError as exc:
        print(f"HID error: {exc}", file=sys.stderr)
        return 1

def cmd_hid_brightness(args: argparse.Namespace) -> int:
    try:
        with P13HidController() as hid_dev:
            hid_dev.set_brightness(args.percent)
        if not args.no_save:
            try:
                update_saved_panel(brightness=args.percent)
            except DisplayError:
                pass
        print(f"Brightness set to {args.percent}%.")
        return 0
    except HidError as exc:
        print(f"HID error: {exc}", file=sys.stderr)
        return 1

def cmd_hid_rotate(args: argparse.Namespace) -> int:
    try:
        with P13HidController() as hid_dev:
            hid_dev.set_rotate(args.degrees)
        detail = ""
        try:
            detail = apply_content_rotation(args.degrees)
        except DisplayError as exc:
            # HID Degree was set; content apply is best-effort.
            try:
                update_saved_panel(rotation=args.degrees)
            except DisplayError:
                pass
            detail = str(exc)
        if detail:
            print(f"Rotation set to {args.degrees}° ({detail}).")
        else:
            print(f"Rotation set to {args.degrees}°.")
        return 0
    except (HidError, ValueError) as exc:
        print(f"HID error: {exc}", file=sys.stderr)
        return 1

def cmd_gui(_args: argparse.Namespace) -> int:
    try:
        from p13ctl.gui.main import main as gui_main
    except ImportError as exc:
        print(
            "GUI requires PySide6: pip install -e '.[gui]'",
            file=sys.stderr,
        )
        print(f"Import error: {exc}", file=sys.stderr)
        return 1
    return gui_main()

def cmd_hid_sniff(args: argparse.Namespace) -> int:
    try:
        with P13HidController() as hid_dev:
            packets = hid_dev.sniff(args.duration)
        if not packets:
            print("No HID packets received.")
            return 1
        for i, pkt in enumerate(packets):
            print(f"[{i}] {pkt}")
        return 0
    except HidError as exc:
        print(f"HID error: {exc}", file=sys.stderr)
        return 1

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="p13ctl",
        description="Control the MSI MPG CoreLiquid P13 360 AIO on Linux",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List connected P13 devices").set_defaults(func=cmd_list)

    p_test = sub.add_parser("display", help="Display commands")
    disp_sub = p_test.add_subparsers(dest="display_cmd", required=True)
    p_dt = disp_sub.add_parser("test", help="Show test pattern")
    p_dt.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270])
    p_dt.set_defaults(func=cmd_display_test)
    p_di = disp_sub.add_parser("image", help="Show image file")
    p_di.add_argument("path")
    p_di.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270])
    p_di.set_defaults(func=cmd_display_image)
    p_dd = disp_sub.add_parser(
        "desktop",
        help="Mirror desktop to the P13 with userspace screen capture",
    )
    p_dd.add_argument("--fps", type=int, default=None, help="target frame rate (default: from saved config or 60)")
    p_dd.add_argument(
        "--interval",
        type=float,
        default=None,
        help="alias for 1/fps (capture mode only)",
    )
    p_dd.add_argument("--quality", type=int, default=None, help="JPEG quality 1-95 (default: from saved config or 75)")
    p_dd.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270], help="software image rotation")
    p_dd.add_argument(
        "--no-configure",
        action="store_true",
        help="skip auto-positioning the virtual output in the desktop",
    )
    p_dd.add_argument(
        "--no-save-layout",
        action="store_true",
        help="do not save settings to ~/.config/p13ctl/display-layout.json",
    )
    p_dd.add_argument(
        "--reset-layout",
        action="store_true",
        help="discard saved settings and use defaults",
    )
    p_dd.add_argument(
        "--capture",
        action="store_true",
        help="deprecated compatibility option (screen capture is now always used)",
    )
    p_dd.add_argument("--monitor", type=int, default=0, help="capture mode: monitor index")
    p_dd.add_argument(
        "--crop",
        choices=["center", "stretch"],
        default="center",
        help="capture mode: crop before scaling",
    )
    p_dd.set_defaults(func=cmd_display_desktop)
    p_dmode = disp_sub.add_parser(
        "mode",
        help="Apply saved Display Mode from ~/.config/p13ctl (used at login)",
    )
    p_dmode.set_defaults(func=cmd_display_mode)
    p_sw = disp_sub.add_parser(
        "sleepwatch",
        help="Blank panel while desktop displays sleep (used by p13-panel-off.service)",
    )
    p_sw.add_argument("--interval", type=float, default=10.0)
    p_sw.set_defaults(func=cmd_display_sleepwatch)
    p_dm = disp_sub.add_parser(
        "monitor",
        help="Alias for the userspace desktop mirror",
    )
    p_dm.add_argument("--fps", type=int, default=None)
    p_dm.add_argument("--quality", type=int, default=None)
    p_dm.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270])
    p_dm.add_argument("--no-configure", action="store_true")
    p_dm.add_argument("--no-save-layout", action="store_true")
    p_dm.add_argument("--reset-layout", action="store_true")
    p_dm.add_argument("--interval", type=float, default=None)
    p_dm.add_argument("--capture", action="store_true")
    p_dm.add_argument("--monitor", type=int, default=0)
    p_dm.add_argument("--crop", choices=["center", "stretch"], default="center")
    p_dm.set_defaults(func=cmd_display_monitor)

    p_sm = sub.add_parser("sysmon", help="Hardware monitor loop (rotating stats)")
    p_sm.add_argument("--interval", type=float, default=1.0, help="value refresh seconds")
    p_sm.add_argument("--switch", type=float, default=10.0, help="seconds per stat")
    p_sm.add_argument(
        "--items",
        default=None,
        help="comma-separated stat keys to rotate (e.g. cpu_temp,gpu_usage)",
    )
    p_sm.add_argument(
        "--background",
        default=None,
        help="image or video file behind the stats (video loops via ffmpeg)",
    )
    p_sm.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270])
    p_sm.set_defaults(func=cmd_sysmon)

    p_ck = sub.add_parser("clock", help="Digital clock face")
    p_ck.add_argument("--style", type=int, default=1, choices=range(1, 7))
    p_ck.add_argument(
        "--background",
        default=None,
        help="image or video file behind the clock (video loops via ffmpeg)",
    )
    p_ck.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270])
    p_ck.set_defaults(func=cmd_clock)

    p_hid = sub.add_parser("hid", help="HID control (brightness, rotation, info)")
    hid_sub = p_hid.add_subparsers(dest="hid_cmd", required=True)
    hid_sub.add_parser("info", help="Query device info (POST conn)").set_defaults(func=cmd_hid_info)
    p_host = hid_sub.add_parser(
        "host",
        help="Enable/disable host display mode (leave firmware splash)",
    )
    p_host.add_argument("state", choices=["on", "off"])
    p_host.set_defaults(func=cmd_hid_host)
    p_br = hid_sub.add_parser("brightness", help="Set LCD brightness (0-100)")
    p_br.add_argument("percent", type=int)
    p_br.add_argument(
        "--no-save",
        action="store_true",
        help="Do not persist the value (for sleep/shutdown hooks)",
    )
    p_br.set_defaults(func=cmd_hid_brightness)
    p_rot = hid_sub.add_parser("rotate", help="Set panel rotation")
    p_rot.add_argument("degrees", type=int, choices=[0, 90, 180, 270])
    p_rot.set_defaults(func=cmd_hid_rotate)
    p_sn = hid_sub.add_parser("sniff", help="Read HID traffic")
    p_sn.add_argument("--duration", type=float, default=5.0)
    p_sn.set_defaults(func=cmd_hid_sniff)

    p_gui = sub.add_parser("gui", help="Open the graphical control panel")
    p_gui.set_defaults(func=cmd_gui)

    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())
