"""Command line parser structure."""

from __future__ import annotations

import pytest

from p13ctl.__main__ import build_parser, cmd_display_desktop


@pytest.mark.parametrize("name", ["desktop", "monitor"])
def test_desktop_and_monitor_share_options(name: str) -> None:
    args = build_parser().parse_args(
        ["display", name, "--fps", "30", "--quality", "50", "--no-configure"]
    )
    assert args.func is cmd_display_desktop
    assert args.fps == 30
    assert args.quality == 50
    assert args.no_configure is True
