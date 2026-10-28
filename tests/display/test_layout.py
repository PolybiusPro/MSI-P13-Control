"""Saved display settings and EVDI output detection."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from p13ctl.display import config, layout
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


# EVDI connectors are recognised by what they are, not by their name.


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


# Loading and saving the config sections.


def test_full_config_round_trips(paths, full_config) -> None:
    config.write_config_file(full_config)
    assert layout.load_display_config() == full_config


def test_save_after_legacy_writes_ini_and_keeps_json(paths, full_config) -> None:
    ini, legacy = paths
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(full_config), encoding="utf-8")

    layout.save_display_config(panel={"brightness": 10})

    assert ini.is_file()
    assert legacy.is_file()
    expected = json.loads(json.dumps(full_config))
    expected["panel"] = {"brightness": 10}
    assert layout.load_display_config() == expected


def test_legacy_v1_layout_still_parsed(paths) -> None:
    _, legacy = paths
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "version": 1,
                "outputs": [
                    {"name": "eDP-1", "x": 0},
                    {"name": "DVI-I-1", "priority": 2, "position": "10,20"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert layout.load_display_config()["virtual"] == {
        "name": "DVI-I-1",
        "position": "10,20",
    }


def test_concurrent_saves_do_not_lose_sections(paths, monkeypatch) -> None:
    real_write = config.write_config_file

    def slow_write(data) -> None:
        time.sleep(0.05)
        real_write(data)

    monkeypatch.setattr(config, "write_config_file", slow_write)
    threads = [
        threading.Thread(target=layout.save_display_config, kwargs={"stream": {"fps": 30}}),
        threading.Thread(target=layout.save_display_config, kwargs={"mode": {"name": "off"}}),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    saved = layout.load_display_config()
    assert saved is not None
    assert saved["stream"] == {"fps": 30}
    assert saved["mode"] == {"name": "off"}


def test_save_preserves_sections_it_does_not_know(paths) -> None:
    config.write_config_file({"version": 3, "future": {"flag": True}, "mode": {"name": "off"}})

    layout.save_display_config(stream={"fps": 30})

    raw = config.read_config_file()
    assert raw is not None
    assert raw["future"] == {"flag": True}
    assert raw["stream"] == {"fps": 30}


# Resetting the saved virtual monitor layout.


def test_reset_keeps_other_sections_and_deletes_legacy(paths, full_config) -> None:
    ini, legacy = paths
    config.write_config_file(full_config)
    legacy.write_text(json.dumps(full_config), encoding="utf-8")

    layout.reset_saved_layout()

    loaded = layout.load_display_config()
    assert "virtual" not in loaded
    assert loaded["panel"] == full_config["panel"]
    assert loaded["stream"] == full_config["stream"]
    assert loaded["mode"] == full_config["mode"]
    assert ini.is_file()
    assert not legacy.exists()


def test_reset_from_legacy_only_migrates_and_deletes_legacy(paths, full_config) -> None:
    ini, legacy = paths
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(full_config), encoding="utf-8")

    layout.reset_saved_layout()

    assert ini.is_file()
    assert not legacy.exists()
    assert "virtual" not in layout.load_display_config()


def test_reset_removes_everything_when_nothing_else_remains(paths, full_config) -> None:
    ini, legacy = paths
    config.write_config_file({"version": 3, "virtual": full_config["virtual"]})
    legacy.write_text(json.dumps(full_config["virtual"]), encoding="utf-8")

    layout.reset_saved_layout()

    assert not ini.exists()
    assert not legacy.exists()
    assert layout.load_display_config() is None


def test_reset_with_no_files_is_harmless(paths) -> None:
    layout.reset_saved_layout()
    assert layout.load_display_config() is None


def test_reset_saved_layout_preserves_unknown_sections(paths) -> None:
    config.write_config_file(
        {"version": 3, "future": {"flag": True}, "virtual": {"name": "DVI-I-1"}, "mode": {"name": "off"}}
    )

    layout.reset_saved_layout()

    raw = config.read_config_file()
    assert raw is not None
    assert raw["future"] == {"flag": True}
    assert "virtual" not in raw


# Patching the saved virtual monitor rotation.


def test_patch_virtual_rotation_updates_ini(paths, full_config) -> None:
    ini, _ = paths
    config.write_config_file(full_config)

    layout._patch_virtual_rotation("DVI-I-2", "left")

    loaded = layout.load_display_config()
    assert loaded["virtual"]["name"] == "DVI-I-2"
    assert loaded["virtual"]["rotation"] == "left"
    assert loaded["virtual"]["position"] == "1920,300"
    assert loaded["panel"] == full_config["panel"]
    assert ini.is_file()


def test_patch_virtual_rotation_creates_ini_from_nothing(paths) -> None:
    ini, _ = paths
    layout._patch_virtual_rotation("DVI-I-1", None)
    assert ini.is_file()
    assert layout.load_display_config()["virtual"] == {
        "name": "DVI-I-1",
        "rotation": None,
    }
