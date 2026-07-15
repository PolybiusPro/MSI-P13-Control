from __future__ import annotations

import hashlib

import pytest

from p13ctl.display.artinchip import DisplayError
from p13ctl.display.faces import HW_MONITOR_STYLES, render_stat, run_hwmon

_ITEM = {
    "title": "CPU TEMP",
    "value": "67",
    "unit": "°C",
    "fraction": 0.67,
}

def test_all_hardware_monitor_styles_render_distinct_panel_frames() -> None:
    frames = [render_stat(_ITEM, style=style) for style in HW_MONITOR_STYLES]

    assert all(frame.mode == "RGB" and frame.size == (480, 480) for frame in frames)
    assert len({hashlib.sha256(frame.tobytes()).digest() for frame in frames}) == 4

def test_hardware_monitor_style_supports_metrics_without_progress() -> None:
    item = {**_ITEM, "title": "GPU CLOCK", "unit": "MHz", "fraction": None}

    for style in HW_MONITOR_STYLES:
        assert render_stat(item, style=style).getbbox() == (0, 0, 480, 480)

@pytest.mark.parametrize("style", [0, 5])
def test_invalid_hardware_monitor_style_is_rejected(style: int) -> None:
    with pytest.raises(DisplayError, match="style must be 1-4"):
        render_stat(_ITEM, style=style)

    with pytest.raises(DisplayError, match="style must be 1-4"):
        run_hwmon(object(), style=style)
