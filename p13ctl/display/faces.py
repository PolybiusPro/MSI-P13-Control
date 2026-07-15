"""Hardware-monitor and clock faces for the P13 panel.

Reverse engineered by observing the panel's on-screen behavior:
The hardware monitor cycles one stat at a time (title, value digits, unit) every
5-30 s, fed by a hardware-monitor sensor string; the six
clock styles are digit-image layouts over looping video, 24-hour, updated
at minute granularity. The original video/digit assets are not redistributable,
so these faces redraw the same layouts with PIL from Linux sensors.
"""

from __future__ import annotations

import bisect
import logging
import math
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .artinchip import DisplayError

_LOGGER = logging.getLogger(__name__)

SIZE = 480
CLOCK_STYLES = (1, 2, 3, 4, 5, 6)

_TRACK = (38, 43, 56)

DEFAULT_COLORS = {
    "accent": "#E8322F",
    "text": "#F0F2F8",
    "label": "#8A93A8",
    "background": "#0A0C12",
}

def _parse_color(value) -> tuple[int, int, int] | None:
    text = str(value or "").lstrip("#")
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None

def resolve_palette(colors: dict | None) -> dict:
    """Hex color overrides -> RGB palette (accent/text/label/background)."""
    palette = {}
    for key, default in DEFAULT_COLORS.items():
        override = _parse_color((colors or {}).get(key))
        palette[key] = override or _parse_color(default)
    return palette

_DEFAULT_PALETTE = resolve_palette(None)

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

def _text_centered(
    draw: ImageDraw.ImageDraw, y: int, text: str, size: int, fill, *, stroke: int = 0
) -> None:
    font = _font(size)
    while size > 20 and draw.textlength(text, font=font) > SIZE - 96:
        size -= 8
        font = _font(size)
    w = draw.textlength(text, font=font)
    draw.text(
        ((SIZE - w) / 2, y),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke,
        stroke_fill=(0, 0, 0),
    )

# --- Linux sensor sources (mirrors the Windows HW_Items list) ---------------
#
# All reads go straight to sysfs/procfs. psutil's sensors scan reads every
# hwmon chip — NVMe SMART temps alone cost ~50 ms — so needed files are
# resolved once and only those are read each tick (~2 ms total).

_HWMON = Path("/sys/class/hwmon")
_CPUFREQ = Path("/sys/devices/system/cpu/cpufreq")
_CPU_TEMP_CHIPS = ("k10temp", "zenpower", "coretemp")
_CPU_TEMP_LABELS = ("tctl", "tdie", "package id 0")

def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None

def _hwmon_chips() -> dict[str, list[Path]]:
    chips: dict[str, list[Path]] = {}
    for hw in sorted(_HWMON.glob("hwmon*")):
        try:
            name = (hw / "name").read_text().strip()
        except OSError:
            continue
        chips.setdefault(name, []).append(hw)
    return chips

def _sensor_label(input_path: Path) -> str:
    label_path = input_path.with_name(input_path.name.replace("_input", "_label"))
    try:
        return label_path.read_text().strip()
    except OSError:
        return ""

def _resolve_cpu_temp(chips: dict[str, list[Path]]) -> Path | None:
    for chip in _CPU_TEMP_CHIPS:
        for hw in chips.get(chip, []):
            fallback = None
            for f in sorted(hw.glob("temp*_input")):
                if _sensor_label(f).lower() in _CPU_TEMP_LABELS:
                    return f
                if fallback is None:
                    fallback = f
            if fallback is not None:
                return fallback
    return None

def _resolve_fans(chips: dict[str, list[Path]]) -> list[tuple[str, Path]]:
    fans: list[tuple[str, Path]] = []
    for name, dirs in sorted(chips.items()):
        if name == "amdgpu":
            continue  # shown as GPU FAN via the drm device
        for hw in dirs:
            for f in sorted(hw.glob("fan*_input")):
                fans.append((_sensor_label(f), f))
    return fans

def _cpu_times() -> tuple[int, int] | None:
    """(total, idle) jiffies from /proc/stat."""
    try:
        with open("/proc/stat", encoding="ascii") as fh:
            fields = fh.readline().split()[1:]
    except OSError:
        return None
    values = [int(v) for v in fields]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle

def _mem_percent() -> float | None:
    total = available = None
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    available = int(line.split()[1])
                if total is not None and available is not None:
                    break
    except OSError:
        return None
    if not total or available is None:
        return None
    return 100.0 * (total - available) / total

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
        self._resolve()
        self._cpu_last = _cpu_times()
        self._cpu_usage = 0.0

    def _resolve(self) -> None:
        chips = _hwmon_chips()
        self._gpu = _find_gpu()
        self._cpu_temp_path = _resolve_cpu_temp(chips)
        self._dram_temps = [hw / "temp1_input" for hw in chips.get("spd5118", [])]
        self._fans = _resolve_fans(chips)
        self._freq_paths = sorted(_CPUFREQ.glob("policy*/scaling_cur_freq"))
        self._freq_max: float | None = None
        if self._freq_paths:
            khz = _read_int(self._freq_paths[0].with_name("cpuinfo_max_freq"))
            self._freq_max = khz / 1000 if khz else None

    def _paths_ok(self) -> bool:
        paths = [self._cpu_temp_path, *self._dram_temps, *(p for _, p in self._fans)]
        return all(p is None or p.exists() for p in paths)

    def _cpu_usage_percent(self) -> float:
        times = _cpu_times()
        if times is not None and self._cpu_last is not None:
            dt = times[0] - self._cpu_last[0]
            di = times[1] - self._cpu_last[1]
            if dt > 0:
                self._cpu_usage = max(0.0, min(100.0, 100.0 * (1 - di / dt)))
        if times is not None:
            self._cpu_last = times
        return self._cpu_usage

    def read(self) -> list[dict]:
        """Available stats in the Windows HW_Items order."""
        if not self._paths_ok():
            self._resolve()  # hwmon re-enumerated (hotplug/suspend)
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

        cpu_temp = None
        if self._cpu_temp_path is not None:
            raw = _read_int(self._cpu_temp_path)
            cpu_temp = raw / 1000 if raw is not None else None
        add("cpu_temp", "CPU TEMP", cpu_temp, "°C", (cpu_temp or 0) / 100)

        freqs = [khz for p in self._freq_paths if (khz := _read_int(p)) is not None]
        if freqs:
            current = max(freqs) / 1000  # boost clock across cores
            add(
                "cpu_clock",
                "CPU CLOCK",
                current,
                "MHz",
                current / self._freq_max if self._freq_max else None,
            )
        usage = self._cpu_usage_percent()
        add("cpu_usage", "CPU USAGE", usage, "%", usage / 100)

        if gpu:
            temp = _read_int(gpu["temp"]) if "temp" in gpu else None
            add("gpu_temp", "GPU TEMP", temp / 1000 if temp is not None else None, "°C", (temp or 0) / 100000)
            gfreq = _read_int(gpu["freq"]) if "freq" in gpu else None
            add("gpu_clock", "GPU CLOCK", gfreq / 1e6 if gfreq is not None else None, "MHz", None)
            busy = _read_int(gpu["busy"])
            add("gpu_usage", "GPU USAGE", busy, "%", (busy or 0) / 100)

        dram_raw = [v for p in self._dram_temps if (v := _read_int(p)) is not None]
        dram = max(dram_raw) / 1000 if dram_raw else None
        add("ram_temp", "RAM TEMP", dram, "°C", (dram or 0) / 100)
        mem = _mem_percent()
        add("ram_usage", "RAM USAGE", mem, "%", (mem or 0) / 100)

        if gpu.get("fan") is not None:
            rpm = _read_int(gpu["fan"])
            add("gpu_fan", "GPU FAN", rpm, "RPM", None)
        counts = {"cpu": 0, "pump": 0, "sys": 0}
        for label, path in self._fans:
            rpm = _read_int(path)
            if rpm is None:
                continue
            low = label.strip().lower()
            if "cpu" in low:
                kind = "cpu"
            elif "pump" in low:
                kind = "pump"
            else:
                kind = "sys"
            counts[kind] += 1
            n = counts[kind]
            suffix = f"_{n}" if kind == "sys" or n > 1 else ""
            title = {"cpu": "CPU FAN", "pump": "PUMP FAN", "sys": "SYS FAN"}[kind]
            add(
                f"{kind}_fan{suffix}",
                f"{title} {n}" if suffix else title,
                rpm,
                "RPM",
                None,
            )
        return items

# --- Backgrounds (Windows uses looping MP4s under DynamicBackground\) --------

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}
VIDEO_FPS = 30

def _fit_panel(img: Image.Image) -> Image.Image:
    """Cover-crop to the square panel."""
    img = img.convert("RGB")
    scale = max(SIZE / img.width, SIZE / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
    left = (img.width - SIZE) // 2
    top = (img.height - SIZE) // 2
    return img.crop((left, top, left + SIZE, top + SIZE))

class _VideoBackground:
    """Loop a video as panel frames via an ffmpeg rawvideo pipe."""

    _FRAME_BYTES = SIZE * SIZE * 3

    def __init__(self, path: str, fps: int = VIDEO_FPS) -> None:
        self.path = path
        self.fps = fps
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise DisplayError("video backgrounds require ffmpeg (dnf install ffmpeg)")
        self._proc = subprocess.Popen(
            [
                ffmpeg,
                "-loglevel", "quiet",
                "-stream_loop", "-1",
                "-i", self.path,
                "-vf", f"scale={SIZE}:{SIZE}:force_original_aspect_ratio=increase,crop={SIZE}:{SIZE}",
                "-r", str(self.fps),
                "-f", "rawvideo",
                "-pix_fmt", "rgb24",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def frame(self) -> Image.Image:
        for _attempt in (0, 1):
            if self._proc is None or self._proc.poll() is not None:
                self.stop()
                self.start()
            assert self._proc is not None and self._proc.stdout is not None
            data = self._proc.stdout.read(self._FRAME_BYTES)
            if data is not None and len(data) == self._FRAME_BYTES:
                return Image.frombytes("RGB", (SIZE, SIZE), data)
            self.stop()  # decoder ended (e.g. formats -stream_loop can't loop)
        raise DisplayError(f"could not decode video background: {self.path}")

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait()
            self._proc = None

class _AnimatedImageBackground:
    """Loop an animated image (WebP/GIF/APNG) decoded via PIL.

    ffmpeg cannot decode animated WebP, so frames are preloaded fitted to the
    panel (subsampled to a memory cap) and picked by wall-clock time so the
    animation runs at its authored speed regardless of send pacing.
    """

    _MAX_BYTES = 64 * 1024 * 1024

    def __init__(self, path: str) -> None:
        frames: list[Image.Image] = []
        starts: list[int] = []
        total_ms = 0
        with Image.open(path) as img:
            count = getattr(img, "n_frames", 1)
            step = max(1, math.ceil(count * SIZE * SIZE * 3 / self._MAX_BYTES))
            for index in range(0, count, step):
                img.seek(index)
                frames.append(_fit_panel(img))
                duration = int(img.info.get("duration") or 100)
                starts.append(total_ms)
                total_ms += max(20, duration) * step
        self._frames = frames
        self._starts = starts
        self._total_ms = max(total_ms, 1)
        self._t0 = time.monotonic()
        self.fps = max(1.0, min(30.0, 1000.0 * len(frames) / self._total_ms))

    def frame(self) -> Image.Image:
        now_ms = ((time.monotonic() - self._t0) * 1000.0) % self._total_ms
        index = bisect.bisect_right(self._starts, now_ms) - 1
        return self._frames[index]

    def next_delay(self) -> float:
        """Seconds until the next frame boundary (send pacing aligns to it)."""
        now_ms = ((time.monotonic() - self._t0) * 1000.0) % self._total_ms
        index = bisect.bisect_right(self._starts, now_ms) - 1
        end = self._starts[index + 1] if index + 1 < len(self._starts) else self._total_ms
        return max(0.0, (end - now_ms) / 1000.0)

    def stop(self) -> None:
        self._frames = []

def open_background(path: str | None):
    """Return None, a static PIL image, or an animated source with
    ``fps``/``frame()``/``stop()`` (PIL animation or ffmpeg video)."""
    if not path:
        return None
    if not Path(path).is_file():
        _LOGGER.warning("background missing, using plain: %s", path)
        return None
    if Path(path).suffix.lower() in _IMAGE_SUFFIXES:
        with Image.open(path) as img:
            if not getattr(img, "is_animated", False):
                return _fit_panel(img)
        return _AnimatedImageBackground(path)
    video = _VideoBackground(path)
    video.start()
    return video

# --- Renderers ---------------------------------------------------------------

def render_stat(
    item: dict,
    background: Image.Image | None = None,
    palette: dict | None = None,
) -> Image.Image:
    """One rotating hardware monitor card: ring gauge, title, big value, unit."""
    pal = palette or _DEFAULT_PALETTE
    if background is not None:
        img = background.copy()
        stroke = 3
    else:
        img = Image.new("RGB", (SIZE, SIZE), pal["background"])
        stroke = 0
    draw = ImageDraw.Draw(img)
    ring = (26, 26, SIZE - 26, SIZE - 26)
    draw.arc(ring, 0, 360, fill=_TRACK, width=14)
    fraction = item.get("fraction")
    if fraction is not None:
        sweep = max(0.0, min(1.0, fraction)) * 360
        draw.arc(ring, -90, -90 + sweep, fill=pal["accent"], width=14)
    _text_centered(draw, 108, item["title"], 42, pal["label"], stroke=stroke)
    _text_centered(draw, 168, item["value"], 132, pal["text"], stroke=stroke)
    _text_centered(draw, 322, item["unit"], 46, pal["accent"], stroke=stroke)
    return img

def render_clock(
    style: int,
    now: datetime,
    background: Image.Image | None = None,
    palette: dict | None = None,
) -> Image.Image:
    """Six layouts matching the Windows clock styles' field combinations."""
    pal = palette or _DEFAULT_PALETTE
    accent, text, label = pal["accent"], pal["text"], pal["label"]
    if background is not None:
        img = background.copy()
        stroke = 3
    else:
        img = Image.new("RGB", (SIZE, SIZE), pal["background"])
        stroke = 0
    draw = ImageDraw.Draw(img)
    draw.arc((14, 14, SIZE - 14, SIZE - 14), 0, 360, fill=_TRACK, width=6)
    hhmm = now.strftime("%H:%M")
    if style == 1:  # month / day / weekday
        _text_centered(draw, 96, now.strftime("%B").upper(), 44, accent, stroke=stroke)
        _text_centered(draw, 140, f"{now.day:02d}", 170, text, stroke=stroke)
        _text_centered(draw, 336, now.strftime("%A").upper(), 40, label, stroke=stroke)
    elif style == 2:  # hour / minute
        _text_centered(draw, 92, now.strftime("%H"), 150, text, stroke=stroke)
        _text_centered(draw, 252, now.strftime("%M"), 150, accent, stroke=stroke)
    elif style == 3:  # year + time + month/day (month art in Windows)
        _text_centered(draw, 88, str(now.year), 40, label, stroke=stroke)
        _text_centered(draw, 140, hhmm, 140, text, stroke=stroke)
        _text_centered(draw, 320, now.strftime("%B %d").upper(), 46, accent, stroke=stroke)
    elif style == 4:  # weekday / day / month
        _text_centered(draw, 96, now.strftime("%A").upper(), 44, accent, stroke=stroke)
        _text_centered(draw, 140, f"{now.day:02d}", 170, text, stroke=stroke)
        _text_centered(draw, 336, now.strftime("%B").upper(), 40, label, stroke=stroke)
    elif style == 5:  # date + time + weekday
        _text_centered(draw, 84, now.strftime("%b %d").upper(), 44, label, stroke=stroke)
        _text_centered(draw, 148, hhmm, 130, text, stroke=stroke)
        _text_centered(draw, 316, now.strftime("%A").upper(), 42, accent, stroke=stroke)
    else:  # 6: time + full date
        _text_centered(draw, 118, hhmm, 150, text, stroke=stroke)
        _text_centered(draw, 300, now.strftime("%Y.%m.%d"), 52, accent, stroke=stroke)
    return img

# --- Run loops ---------------------------------------------------------------

def run_hwmon(
    disp,
    *,
    refresh_s: float = 1.0,
    switch_s: float = 10.0,
    items: list[str] | None = None,
    background: str | None = None,
    colors: dict | None = None,
) -> None:
    """Rotate through available stats like the hardware monitor.

    ``items`` restricts the rotation to the given stat keys (all when None
    or when the selection matches no available sensor). ``background`` is an
    optional image or video file; a video streams at its own frame rate with
    the stat overlay composited per frame. ``colors`` overrides the palette
    (hex strings keyed accent/text/label/background).
    """
    sensors = HwSensors()
    palette = resolve_palette(colors)
    base = open_background(background)
    animated = base if hasattr(base, "frame") else None
    frame_s = 1.0 / animated.fps if animated else refresh_s

    wanted = set(items) if items else None

    def _read_stats() -> list[dict]:
        fresh = sensors.read()
        if wanted:
            selected = [s for s in fresh if s["key"] in wanted]
            fresh = selected or fresh
        if not fresh:
            raise DisplayError("no hardware sensors found")
        return fresh

    # A read takes tens of ms — poll on a thread so animated backgrounds
    # don't hitch once per refresh.
    stats = _read_stats()
    stats_box = {"stats": stats}
    stop_polling = threading.Event()

    def _poll_sensors() -> None:
        while not stop_polling.wait(refresh_s):
            try:
                stats_box["stats"] = _read_stats()
            except Exception as exc:  # noqa: BLE001 — keep last good values
                _LOGGER.debug("sensor poll failed: %s", exc)

    poller = threading.Thread(target=_poll_sensors, daemon=True)
    poller.start()

    index = 0
    next_switch = time.monotonic() + switch_s
    try:
        while not disp._stop_requested:
            t0 = time.monotonic()
            stats = stats_box["stats"]
            if t0 >= next_switch:
                index += 1
                next_switch = t0 + switch_s
            frame = animated.frame() if animated else base
            disp.send_image(render_stat(stats[index % len(stats)], frame, palette))
            if animated is not None and hasattr(animated, "next_delay"):
                # Align the next send to the animation's own frame boundary
                # so frames are neither doubled nor skipped (no judder).
                time.sleep(animated.next_delay() + 0.002)
            else:
                elapsed = time.monotonic() - t0
                if frame_s - elapsed > 0:
                    time.sleep(frame_s - elapsed)
    finally:
        stop_polling.set()
        if animated is not None:
            animated.stop()

def run_clock(
    disp,
    *,
    style: int = 1,
    background: str | None = None,
    colors: dict | None = None,
) -> None:
    """Redraw at minute granularity like the Windows clock faces.

    Animated backgrounds stream continuously with the clock composited
    per frame, mirroring the Windows MP4-loop clocks.
    """
    if style not in CLOCK_STYLES:
        raise DisplayError(f"clock style must be 1-6 (got {style})")
    palette = resolve_palette(colors)
    base = open_background(background)
    animated = base if hasattr(base, "frame") else None
    frame_s = 1.0 / animated.fps if animated else 1.0
    last_key = None
    try:
        while not disp._stop_requested:
            t0 = time.monotonic()
            now = datetime.now()
            if animated is not None:
                disp.send_image(render_clock(style, now, animated.frame(), palette))
                if hasattr(animated, "next_delay"):
                    time.sleep(animated.next_delay() + 0.002)
                else:
                    elapsed = time.monotonic() - t0
                    if frame_s - elapsed > 0:
                        time.sleep(frame_s - elapsed)
                continue
            key = (now.year, now.month, now.day, now.hour, now.minute)
            if key != last_key:
                last_key = key
                disp.send_image(render_clock(style, now, base, palette))
            time.sleep(1.0)
    finally:
        if animated is not None:
            animated.stop()
