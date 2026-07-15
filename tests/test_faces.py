from __future__ import annotations

import hashlib
import itertools
import shutil

import pytest

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
