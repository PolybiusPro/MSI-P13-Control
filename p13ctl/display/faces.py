"""Hardware-monitor and clock faces for the P13 panel.

Reverse engineered by observing the panel's on-screen behavior:
The hardware monitor cycles one stat at a time (title, value digits, unit) every
5-30 s, fed by a hardware-monitor sensor string; the six
clock styles are digit-image layouts over looping video, 24-hour, updated
at minute granularity. The original video/digit assets are not redistributable,
so these faces redraw the same layouts with PIL from Linux sensors.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .artinchip import DisplayError

_LOGGER = logging.getLogger(__name__)

SIZE = 480
CLOCK_STYLES = (1, 2, 3, 4, 5, 6)

_BG = (10, 12, 18)
_TRACK = (38, 43, 56)
_ACCENT = (232, 50, 47)
_TEXT = (240, 242, 248)
_DIM = (138, 147, 168)

_FONT_PATHS = (
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
)
_font_cache: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

def _font(size: int):
    if size not in _font_cache:
        for path in _FONT_PATHS:
            if Path(path).is_file():
                _font_cache[size] = ImageFont.truetype(path, size)
                break
        else:
            _font_cache[size] = ImageFont.load_default(size)
    return _font_cache[size]

def _text_centered(draw: ImageDraw.ImageDraw, y: int, text: str, size: int, fill) -> None:
    font = _font(size)
    while size > 20 and draw.textlength(text, font=font) > SIZE - 96:
        size -= 8
        font = _font(size)
    w = draw.textlength(text, font=font)
    draw.text(((SIZE - w) / 2, y), text, font=font, fill=fill)

# --- Linux sensor sources (mirrors the Windows HW_Items list) ---------------

def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None

def _find_gpu() -> dict[str, Path]:
    """Locate the busiest-featured amdgpu card (discrete preferred: has a fan)."""
    best: dict[str, Path] = {}
    best_score = -1
    for dev in sorted(Path("/sys/class/drm").glob("card[0-9]/device")):
        busy = dev / "gpu_busy_percent"
        if not busy.is_file():
            continue
        paths: dict[str, Path] = {"busy": busy}
        for hwmon in (dev / "hwmon").glob("hwmon*"):
            for key, name in (("temp", "temp1_input"), ("freq", "freq1_input"), ("fan", "fan1_input")):
                if (hwmon / name).is_file():
                    paths[key] = hwmon / name
        score = len(paths) + (2 if "fan" in paths else 0)
        if score > best_score:
            best, best_score = paths, score
    return best

class HwSensors:
    """One-pass reader for the stats the hardware monitor rotates through."""

    def __init__(self) -> None:
        import psutil

        self._psutil = psutil
        self._gpu = _find_gpu()
        self._dram_temps = sorted(
            p for p in Path("/sys/class/hwmon").glob("hwmon*")
            if (p / "name").is_file() and (p / "name").read_text().strip() == "spd5118"
        )
        freq = psutil.cpu_freq()
        self._cpu_freq_max = freq.max if freq and freq.max else None
        psutil.cpu_percent()  # prime the interval counter

    def _cpu_temp(self) -> float | None:
        temps = self._psutil.sensors_temperatures()
        for chip in ("k10temp", "zenpower", "coretemp"):
            for entry in temps.get(chip, []):
                if entry.current:
                    return entry.current
        for entries in temps.values():
            for entry in entries:
                if "cpu" in (entry.label or "").lower():
                    return entry.current
        return None

    def _dram_temp(self) -> float | None:
        values = [v for p in self._dram_temps if (v := _read_int(p / "temp1_input")) is not None]
        return max(values) / 1000 if values else None

    def read(self) -> list[dict]:
        """Available stats in the Windows HW_Items order."""
        psutil = self._psutil
        gpu = self._gpu
        items: list[dict] = []

        def add(key: str, title: str, value: float | None, unit: str, fraction: float | None) -> None:
            if value is not None:
                items.append(
                    {
                        "key": key,
                        "title": title,
                        "value": f"{value:.0f}",
                        "unit": unit,
                        "fraction": fraction,
                    }
                )

        cpu_temp = self._cpu_temp()
        add("cpu_temp", "CPU TEMP", cpu_temp, "°C", (cpu_temp or 0) / 100)
        freq = psutil.cpu_freq()
        if freq and freq.current:
            add(
                "cpu_clock",
                "CPU CLOCK",
                freq.current,
                "MHz",
                freq.current / self._cpu_freq_max if self._cpu_freq_max else None,
            )
        usage = psutil.cpu_percent()
        add("cpu_usage", "CPU USAGE", usage, "%", usage / 100)

        if gpu:
            temp = _read_int(gpu["temp"]) if "temp" in gpu else None
            add("gpu_temp", "GPU TEMP", temp / 1000 if temp is not None else None, "°C", (temp or 0) / 100000)
            gfreq = _read_int(gpu["freq"]) if "freq" in gpu else None
            add("gpu_clock", "GPU CLOCK", gfreq / 1e6 if gfreq is not None else None, "MHz", None)
            busy = _read_int(gpu["busy"])
            add("gpu_usage", "GPU USAGE", busy, "%", (busy or 0) / 100)

        dram = self._dram_temp()
        add("ram_temp", "RAM TEMP", dram, "°C", (dram or 0) / 100)
        mem = psutil.virtual_memory().percent
        add("ram_usage", "RAM USAGE", mem, "%", mem / 100)

        if gpu.get("fan") is not None:
            rpm = _read_int(gpu["fan"])
            add("gpu_fan", "GPU FAN", rpm, "RPM", None)
        fans = psutil.sensors_fans()
        n = 0
        for chip, entries in fans.items():
            if chip == "amdgpu":
                continue  # already shown as GPU FAN
            for entry in entries:
                n += 1
                add(f"fan_{n}", (entry.label or f"SYS FAN {n}").upper(), entry.current, "RPM", None)
        return items

# --- Renderers ---------------------------------------------------------------

def render_stat(item: dict) -> Image.Image:
    """One rotating hardware monitor card: ring gauge, title, big value, unit."""
    img = Image.new("RGB", (SIZE, SIZE), _BG)
    draw = ImageDraw.Draw(img)
    ring = (26, 26, SIZE - 26, SIZE - 26)
    draw.arc(ring, 0, 360, fill=_TRACK, width=14)
    fraction = item.get("fraction")
    if fraction is not None:
        sweep = max(0.0, min(1.0, fraction)) * 360
        draw.arc(ring, -90, -90 + sweep, fill=_ACCENT, width=14)
    _text_centered(draw, 108, item["title"], 42, _DIM)
    _text_centered(draw, 168, item["value"], 132, _TEXT)
    _text_centered(draw, 322, item["unit"], 46, _ACCENT)
    return img

def render_clock(style: int, now: datetime) -> Image.Image:
    """Six layouts matching the Windows clock styles' field combinations."""
    img = Image.new("RGB", (SIZE, SIZE), _BG)
    draw = ImageDraw.Draw(img)
    draw.arc((14, 14, SIZE - 14, SIZE - 14), 0, 360, fill=_TRACK, width=6)
    hhmm = now.strftime("%H:%M")
    if style == 1:  # month / day / weekday
        _text_centered(draw, 96, now.strftime("%B").upper(), 44, _ACCENT)
        _text_centered(draw, 140, f"{now.day:02d}", 170, _TEXT)
        _text_centered(draw, 336, now.strftime("%A").upper(), 40, _DIM)
    elif style == 2:  # hour / minute
        _text_centered(draw, 92, now.strftime("%H"), 150, _TEXT)
        _text_centered(draw, 252, now.strftime("%M"), 150, _ACCENT)
    elif style == 3:  # year + time + month/day (month art in Windows)
        _text_centered(draw, 88, str(now.year), 40, _DIM)
        _text_centered(draw, 140, hhmm, 140, _TEXT)
        _text_centered(draw, 320, now.strftime("%B %d").upper(), 46, _ACCENT)
    elif style == 4:  # weekday / day / month
        _text_centered(draw, 96, now.strftime("%A").upper(), 44, _ACCENT)
        _text_centered(draw, 140, f"{now.day:02d}", 170, _TEXT)
        _text_centered(draw, 336, now.strftime("%B").upper(), 40, _DIM)
    elif style == 5:  # date + time + weekday
        _text_centered(draw, 84, now.strftime("%b %d").upper(), 44, _DIM)
        _text_centered(draw, 148, hhmm, 130, _TEXT)
        _text_centered(draw, 316, now.strftime("%A").upper(), 42, _ACCENT)
    else:  # 6: time + full date
        _text_centered(draw, 118, hhmm, 150, _TEXT)
        _text_centered(draw, 300, now.strftime("%Y.%m.%d"), 52, _ACCENT)
    return img

# --- Run loops ---------------------------------------------------------------

def run_hwmon(
    disp,
    *,
    refresh_s: float = 1.0,
    switch_s: float = 10.0,
    items: list[str] | None = None,
) -> None:
    """Rotate through available stats like the hardware monitor.

    ``items`` restricts the rotation to the given stat keys (all when None
    or when the selection matches no available sensor).
    """
    try:
        sensors = HwSensors()
    except ImportError as exc:
        raise DisplayError("sysmon requires: pip install p13ctl[sysmon]") from exc

    wanted = set(items) if items else None
    index = 0
    next_switch = time.monotonic() + switch_s
    while not disp._stop_requested:
        t0 = time.monotonic()
        stats = sensors.read()
        if wanted:
            selected = [s for s in stats if s["key"] in wanted]
            stats = selected or stats
        if not stats:
            raise DisplayError("no hardware sensors found")
        if t0 >= next_switch:
            index += 1
            next_switch = t0 + switch_s
        disp.send_image(render_stat(stats[index % len(stats)]))
        elapsed = time.monotonic() - t0
        if refresh_s - elapsed > 0:
            time.sleep(refresh_s - elapsed)

def run_clock(disp, *, style: int = 1) -> None:
    """Redraw at minute granularity like the Windows clock faces."""
    if style not in CLOCK_STYLES:
        raise DisplayError(f"clock style must be 1-6 (got {style})")
    last_key = None
    while not disp._stop_requested:
        now = datetime.now()
        key = (now.year, now.month, now.day, now.hour, now.minute)
        if key != last_key:
            last_key = key
            disp.send_image(render_clock(style, now))
        time.sleep(1.0)
