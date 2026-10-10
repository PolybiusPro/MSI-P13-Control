from __future__ import annotations

import hashlib
import itertools
import shutil
import time
import types

import pytest

from p13ctl.display import faces
from p13ctl.display.artinchip import DisplayError
from p13ctl.display.faces import (
    HW_MONITOR_STYLES,
    TEST_BACKGROUNDS,
    _AnimatedImageBackground,
    _VideoBackground,
    open_background,
    render_dashboard,
    render_stat,
    resolve_palette,
    run_hwmon,
)

_ITEM = {
    "title": "CPU TEMP",
    "value": "67",
    "unit": "°C",
    "fraction": 0.67,
}

def test_all_hardware_monitor_styles_render_distinct_panel_frames() -> None:
    frames = [render_stat(_ITEM, style=style) for style in HW_MONITOR_STYLES]

    assert all(frame.mode == "RGB" and frame.size == (480, 480) for frame in frames)
    assert len({hashlib.sha256(frame.tobytes()).digest() for frame in frames}) == 5

def test_hardware_monitor_style_supports_metrics_without_progress() -> None:
    item = {**_ITEM, "title": "GPU CLOCK", "unit": "MHz", "fraction": None}

    for style in HW_MONITOR_STYLES:
        assert render_stat(item, style=style).getbbox() == (0, 0, 480, 480)

@pytest.mark.parametrize("style", [0, 6])
def test_invalid_hardware_monitor_style_is_rejected(style: int) -> None:
    with pytest.raises(DisplayError, match="style must be 1-5"):
        render_stat(_ITEM, style=style)

    with pytest.raises(DisplayError, match="style must be 1-5"):
        run_hwmon(object(), style=style)

class _FakeSensors:
    def read(self) -> list[dict]:
        return [_ITEM]

class _FakeDisplay:
    """Records send_image kwargs and stops the run loop after one frame."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._stop_requested = False

    def send_image(self, _image, **kwargs) -> None:
        self.calls.append(kwargs)
        self._stop_requested = True

class _FakeVideo(_VideoBackground):
    def __init__(self, fps: int = 30) -> None:
        self.fps = fps

    def frame(self):
        return faces.Image.new("RGB", (480, 480))

    def stop(self) -> None:
        pass

class _FakeAnimation(_AnimatedImageBackground):
    def __init__(self, fps: float, boundary: float) -> None:
        self.fps = fps
        self._boundary = boundary

    def frame(self):
        return faces.Image.new("RGB", (480, 480))

    def next_delay(self) -> float:
        return self._boundary

    def stop(self) -> None:
        pass

def _record_sleeps(monkeypatch) -> list[float]:
    """Swap the module's clock for a stub so the global time.sleep is untouched."""
    sleeps: list[float] = []
    stub = types.SimpleNamespace(monotonic=time.monotonic, sleep=sleeps.append)
    monkeypatch.setattr(faces, "time", stub)
    return sleeps

def test_hardware_monitor_static_face_keeps_default_encoding(monkeypatch) -> None:
    disp = _FakeDisplay()
    monkeypatch.setattr(faces, "HwSensors", _FakeSensors)
    _record_sleeps(monkeypatch)

    run_hwmon(disp, fps=10, quality=72)

    assert disp.calls == [{"quality": 72}]

def test_hardware_monitor_animated_face_uses_low_cost_encoding(monkeypatch) -> None:
    disp = _FakeDisplay()
    requested: list[int] = []

    def fake_open(_path, *, video_fps):
        requested.append(video_fps)
        return _FakeVideo(fps=video_fps)

    monkeypatch.setattr(faces, "HwSensors", _FakeSensors)
    monkeypatch.setattr(faces, "open_background", fake_open)
    _record_sleeps(monkeypatch)

    run_hwmon(disp, fps=10, quality=72, background="clip.mp4")

    assert disp.calls == [{"quality": 72, "optimize": False}]
    assert requested == [10]

@pytest.mark.parametrize(
    ("fps", "expected"),
    [(None, faces.VIDEO_FPS), (0, 1), (-5, 1), (12, 12)],
)
def test_hardware_monitor_clamps_video_fps(monkeypatch, fps, expected) -> None:
    requested: list[int] = []

    def fake_open(_path, *, video_fps):
        requested.append(video_fps)
        return None

    monkeypatch.setattr(faces, "HwSensors", _FakeSensors)
    monkeypatch.setattr(faces, "open_background", fake_open)
    _record_sleeps(monkeypatch)

    kwargs = {} if fps is None else {"fps": fps}
    run_hwmon(_FakeDisplay(), **kwargs)

    assert requested == [expected]

def test_hardware_monitor_defaults_match_pre_optimization_behaviour(monkeypatch) -> None:
    disp = _FakeDisplay()
    monkeypatch.setattr(faces, "HwSensors", _FakeSensors)
    _record_sleeps(monkeypatch)

    run_hwmon(disp)

    assert disp.calls == [{"quality": 85}]

def test_animation_pacing_respects_fps_cap(monkeypatch) -> None:
    sleeps = _record_sleeps(monkeypatch)
    monkeypatch.setattr(faces, "HwSensors", _FakeSensors)
    monkeypatch.setattr(
        faces, "open_background", lambda *_a, **_k: _FakeAnimation(30.0, 0.033)
    )

    run_hwmon(_FakeDisplay(), fps=10, background="clip.webp")

    assert len(sleeps) == 1
    assert 0.09 < sleeps[0] <= 0.1

def test_animation_pacing_waits_for_later_frame_boundary(monkeypatch) -> None:
    sleeps = _record_sleeps(monkeypatch)
    monkeypatch.setattr(faces, "HwSensors", _FakeSensors)
    monkeypatch.setattr(
        faces, "open_background", lambda *_a, **_k: _FakeAnimation(2.0, 0.5)
    )

    run_hwmon(_FakeDisplay(), fps=10, background="clip.webp")

    assert sleeps == [pytest.approx(0.502)]

@pytest.mark.parametrize(
    ("requested", "expected"), [(0, 1), (10, 10), (30, 30), (120, 30)]
)
def test_open_background_clamps_video_fps(
    monkeypatch, tmp_path, requested, expected
) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"")
    seen: list[int] = []

    class RecordingVideo:
        def __init__(self, _path: str, fps: int) -> None:
            seen.append(fps)

        def start(self) -> None:
            pass

    monkeypatch.setattr(faces, "_VideoBackground", RecordingVideo)

    open_background(str(clip), video_fps=requested)

    assert seen == [expected]

def test_dashboard_renders_all_five_reference_metrics() -> None:
    items = [
        {"key": "cpu_temp", "value": "46", "unit": "°C", "fraction": 0.46},
        {"key": "cpu_usage", "value": "6", "unit": "%", "fraction": 0.06},
        {"key": "gpu_temp", "value": "43", "unit": "°C", "fraction": 0.43},
        {"key": "gpu_usage", "value": "16", "unit": "%", "fraction": 0.16},
        {"key": "ram_usage", "value": "34", "unit": "%", "fraction": 0.34},
    ]

    frame = render_dashboard(items)

    assert frame.mode == "RGB" and frame.size == (480, 480)
    assert frame.getbbox() == (0, 0, 480, 480)

def test_dashboard_gauges_change_with_current_levels() -> None:
    empty = render_dashboard(
        [{"key": "cpu_usage", "value": "50", "unit": "%", "fraction": 0.0}]
    )
    full = render_dashboard(
        [{"key": "cpu_usage", "value": "50", "unit": "%", "fraction": 1.0}]
    )

    assert empty.tobytes() != full.tobytes()

def test_dashboard_ram_bar_changes_with_current_level() -> None:
    empty = render_dashboard(
        [{"key": "ram_usage", "value": "50", "unit": "%", "fraction": 0.0}]
    )
    full = render_dashboard(
        [{"key": "ram_usage", "value": "50", "unit": "%", "fraction": 1.0}]
    )

    assert empty.tobytes() != full.tobytes()

def test_every_dashboard_color_option_affects_rendering() -> None:
    items = [
        {
            "key": "cpu_usage",
            "value": "50",
            "unit": "%",
            "fraction": 0.5,
        },
        {
            "key": "ram_usage",
            "value": "50",
            "unit": "%",
            "fraction": 0.5,
        },
    ]
    baseline = render_dashboard(items)

    for color_key in ("accent", "track", "text", "label", "background"):
        changed = render_dashboard(items, palette=resolve_palette({color_key: "#00FF00"}))
        assert changed.tobytes() != baseline.tobytes(), color_key

def test_bundled_test_backgrounds_exist() -> None:
    for name, path in TEST_BACKGROUNDS.items():
        assert path.is_file(), name

def test_webp_test_background_opens_as_looping_animation() -> None:
    background = open_background("red-ball.webp")
    assert isinstance(background, _AnimatedImageBackground)
    try:
        frame = background.frame()
        assert frame.mode == "RGB" and frame.size == (480, 480)
        # The 5s clip keeps 75 subsampled frames (15fps); losing the
        # per-frame durations to the 100ms fallback would report 5fps.
        assert 10.0 < background.fps <= 30.0
        assert background.next_delay() >= 0.0
    finally:
        background.stop()

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requires ffmpeg")
def test_mp4_test_background_loops_without_freezing() -> None:
    background = open_background("red-ball.mp4")
    assert isinstance(background, _VideoBackground)
    try:
        first = background.frame()
        assert first.mode == "RGB" and first.size == (480, 480)
        # Two passes of the 5s/30fps clip; a stuck loop transition would
        # repeat the final frame for dozens of reads (the -stream_loop bug).
        digests = [
            hashlib.sha256(background.frame().tobytes()).digest() for _ in range(300)
        ]
    finally:
        background.stop()
    longest_freeze = max(len(list(run)) for _, run in itertools.groupby(digests))
    assert longest_freeze < 10
