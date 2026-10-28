from __future__ import annotations

import configparser
import json
import threading
import time
from pathlib import Path

import pytest

from p13ctl.display import layout

FULL_CONFIG = {
    "version": 3,
    "virtual": {
        "name": "DVI-I-1",
        "enabled": True,
        "mode": "480x480@60",
        "position": "1920,300",
        "rotation": None,
        "scale": 1.5,
        "anchor": "eDP-1",
        "offset": {"x": 1920, "y": 300},
    },
    "panel": {"brightness": 80, "rotation": 90},
    "stream": {"fps": 30, "quality": 75, "rotate": 0},
    "mode": {
        "name": "sysmon",
        "items": ["cpu_temp", "gpu_temp"],
        "colors": {"clock": "#ffffff", "label": "#00ff00"},
        "style": 3,
        "background": "/home/user/Pictures/my wallpaper.png",
    },
}


@pytest.fixture
def paths(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    ini = tmp_path / "p13ctl" / "display.ini"
    legacy = tmp_path / "p13ctl" / "display-layout.json"
    monkeypatch.setattr(layout, "LAYOUT_PATH", ini)
    monkeypatch.setattr(layout, "LEGACY_JSON_PATH", legacy)
    return ini, legacy


def _save_full() -> None:
    layout._write_config_file(FULL_CONFIG)


def test_full_config_round_trips(paths) -> None:
    _save_full()
    assert layout.load_display_config() == FULL_CONFIG


@pytest.mark.parametrize(
    "text",
    [
        "123",
        "true",
        "null",
        "0,0",
        "-1.5e3",
        "NaN",
        "[1, 2]",
        '"quoted"',
        "/home/me/some dir/pic.png",
        "#ff0000;x",
        "",
        " padded ",
        "line1\nline2",
        "tab\tand\rcr",
        "50% of %(x)s",
        "caf\u00e9",
    ],
)
def test_ambiguous_strings_round_trip(paths, text: str) -> None:
    layout.save_display_config(mode={"name": "image", "image": text})
    loaded = layout.load_display_config()
    assert loaded["mode"]["image"] == text
    assert isinstance(loaded["mode"]["image"], str)


def test_scalar_types_keep_their_type(paths) -> None:
    mode = {"a": True, "b": False, "c": None, "d": 7, "e": 0.25, "f": "7"}
    layout.save_display_config(mode=mode)
    assert layout.load_display_config()["mode"] == mode


def test_written_file_is_real_ini(paths) -> None:
    ini, _ = paths
    _save_full()

    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(ini, encoding="utf-8")

    assert parser["general"]["version"] == "3"
    assert {"virtual", "panel", "stream", "mode"} <= set(parser.sections())
    assert parser["panel"]["brightness"] == "80"
    assert parser["mode"]["items"] == '["cpu_temp", "gpu_temp"]'
    assert parser["virtual"]["position"] == "1920,300"
    assert parser["virtual"]["enabled"] == "true"


def test_corrupt_ini_returns_none(paths) -> None:
    ini, _ = paths
    ini.parent.mkdir(parents=True)
    ini.write_text("this is [[[ not an ini\n", encoding="utf-8")
    assert layout.load_display_config() is None

    ini.write_bytes(b"\xff\xfe\x00\x80 binary \xc3\x28")
    assert layout.load_display_config() is None

    ini.write_text("[general]\nversion = 3\n[general]\n", encoding="utf-8")
    assert layout.load_display_config() is None


def test_legacy_json_is_read_when_no_ini(paths) -> None:
    ini, legacy = paths
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(FULL_CONFIG), encoding="utf-8")

    assert layout.load_display_config() == FULL_CONFIG
    assert not ini.exists()


def test_save_after_legacy_writes_ini_and_keeps_json(paths) -> None:
    ini, legacy = paths
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(FULL_CONFIG), encoding="utf-8")

    layout.save_display_config(panel={"brightness": 10})

    assert ini.is_file()
    assert legacy.is_file()
    expected = json.loads(json.dumps(FULL_CONFIG))
    expected["panel"] = {"brightness": 10}
    assert layout.load_display_config() == expected


def test_ini_wins_over_stale_json(paths) -> None:
    ini, legacy = paths
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps({"version": 3, "panel": {"brightness": 1}}), encoding="utf-8"
    )
    layout.save_display_config(panel={"brightness": 99})
    # The save above merged legacy then wrote the INI; edit the JSON afterwards.
    legacy.write_text(
        json.dumps({"version": 3, "panel": {"brightness": 5}}), encoding="utf-8"
    )

    assert layout.load_display_config()["panel"] == {"brightness": 99}


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


def test_corrupt_legacy_json_returns_none(paths) -> None:
    _, legacy = paths
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{not json", encoding="utf-8")
    assert layout.load_display_config() is None


def test_reset_keeps_other_sections_and_deletes_legacy(paths) -> None:
    ini, legacy = paths
    _save_full()
    legacy.write_text(json.dumps(FULL_CONFIG), encoding="utf-8")

    layout.reset_saved_layout()

    loaded = layout.load_display_config()
    assert "virtual" not in loaded
    assert loaded["panel"] == FULL_CONFIG["panel"]
    assert loaded["stream"] == FULL_CONFIG["stream"]
    assert loaded["mode"] == FULL_CONFIG["mode"]
    assert ini.is_file()
    assert not legacy.exists()


def test_reset_from_legacy_only_migrates_and_deletes_legacy(paths) -> None:
    ini, legacy = paths
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(FULL_CONFIG), encoding="utf-8")

    layout.reset_saved_layout()

    assert ini.is_file()
    assert not legacy.exists()
    assert "virtual" not in layout.load_display_config()


def test_reset_removes_everything_when_nothing_else_remains(paths) -> None:
    ini, legacy = paths
    layout._write_config_file({"version": 3, "virtual": FULL_CONFIG["virtual"]})
    legacy.write_text(json.dumps(FULL_CONFIG["virtual"]), encoding="utf-8")

    layout.reset_saved_layout()

    assert not ini.exists()
    assert not legacy.exists()
    assert layout.load_display_config() is None


def test_reset_with_no_files_is_harmless(paths) -> None:
    layout.reset_saved_layout()
    assert layout.load_display_config() is None


def test_patch_virtual_rotation_updates_ini(paths) -> None:
    ini, _ = paths
    _save_full()

    layout._patch_virtual_rotation("DVI-I-2", "left")

    loaded = layout.load_display_config()
    assert loaded["virtual"]["name"] == "DVI-I-2"
    assert loaded["virtual"]["rotation"] == "left"
    assert loaded["virtual"]["position"] == "1920,300"
    assert loaded["panel"] == FULL_CONFIG["panel"]
    assert ini.is_file()


def test_patch_virtual_rotation_creates_ini_from_nothing(paths) -> None:
    ini, _ = paths
    layout._patch_virtual_rotation("DVI-I-1", None)
    assert ini.is_file()
    assert layout.load_display_config()["virtual"] == {
        "name": "DVI-I-1",
        "rotation": None,
    }


def test_write_is_atomic_and_leaves_no_temp_files(paths) -> None:
    ini, _ = paths
    _save_full()
    layout.save_display_config(panel={"brightness": 1})
    assert [p.name for p in ini.parent.iterdir()] == ["display.ini"]


def test_failed_write_keeps_previous_file(paths, monkeypatch) -> None:
    ini, _ = paths
    layout.save_display_config(panel={"brightness": 42})
    before = ini.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(layout.os, "replace", boom)
    with pytest.raises(OSError):
        layout.save_display_config(panel={"brightness": 7})

    assert ini.read_bytes() == before
    assert [p.name for p in ini.parent.iterdir()] == ["display.ini"]


def test_config_dir_honors_xdg_config_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert layout._config_dir() == tmp_path / "p13ctl"


@pytest.mark.parametrize("value", [None, ""])
def test_config_dir_falls_back_to_dot_config(monkeypatch, tmp_path: Path, value) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    if value is None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", value)
    assert layout._config_dir() == tmp_path / ".config" / "p13ctl"


def test_concurrent_saves_do_not_lose_sections(paths, monkeypatch) -> None:
    real_write = layout._write_config_file

    def slow_write(data) -> None:
        time.sleep(0.05)
        real_write(data)

    monkeypatch.setattr(layout, "_write_config_file", slow_write)
    threads = [
        threading.Thread(target=layout.save_display_config, kwargs={"stream": {"fps": 30}}),
        threading.Thread(target=layout.save_display_config, kwargs={"mode": {"name": "off"}}),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    config = layout.load_display_config()
    assert config is not None
    assert config["stream"] == {"fps": 30}
    assert config["mode"] == {"name": "off"}


def test_save_preserves_sections_it_does_not_know(paths) -> None:
    layout._write_config_file({"version": 3, "future": {"flag": True}, "mode": {"name": "off"}})

    layout.save_display_config(stream={"fps": 30})

    raw = layout._read_config_file()
    assert raw is not None
    assert raw["future"] == {"flag": True}
    assert raw["stream"] == {"fps": 30}


def test_reset_saved_layout_preserves_unknown_sections(paths) -> None:
    layout._write_config_file(
        {"version": 3, "future": {"flag": True}, "virtual": {"name": "DVI-I-1"}, "mode": {"name": "off"}}
    )

    layout.reset_saved_layout()

    raw = layout._read_config_file()
    assert raw is not None
    assert raw["future"] == {"flag": True}
    assert "virtual" not in raw
