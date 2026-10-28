"""EVDI connectors are recognised by what they are, not by their name."""

from __future__ import annotations

from pathlib import Path

import pytest

from p13ctl.display import layout
from p13ctl.display.edid import generate_edid


def _connector(root: Path, name: str, *, edid: bytes | None = None, driver: str | None = None):
    node = root / name
    node.mkdir(parents=True)
    if edid is not None:
        (node / "edid").write_bytes(edid)
    if driver is not None:
        drv = root / "drivers" / driver
        drv.mkdir(parents=True, exist_ok=True)
        (node / "device").mkdir()
        (node / "device" / "driver").symlink_to(drv)


@pytest.fixture
def drm(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "drm"
    root.mkdir()
    monkeypatch.setattr(layout, "DRM_SYSFS", root)
    return root


def test_connector_with_the_panel_edid_is_evdi(drm: Path) -> None:
    _connector(drm, "card2-HDMI-A-5", edid=generate_edid())
    assert layout.is_evdi_output("HDMI-A-5")


def test_connector_bound_to_the_evdi_driver_is_evdi(drm: Path) -> None:
    _connector(drm, "card2-DP-9", driver="evdi")
    assert layout.is_evdi_output("DP-9")


def test_a_real_dvi_port_is_not_evdi_when_evdi_is_identifiable(drm: Path) -> None:
    _connector(drm, "card1-DVI-D-1", driver="amdgpu")
    _connector(drm, "card2-DVI-I-1", driver="evdi")
    assert not layout.is_evdi_output("DVI-D-1")
    assert layout.is_evdi_output("DVI-I-1")


def test_name_prefix_is_the_fallback_when_sysfs_has_no_answer(drm: Path) -> None:
    assert layout.is_evdi_output("DVI-I-1")
    assert not layout.is_evdi_output("eDP-1")
