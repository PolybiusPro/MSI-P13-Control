"""The INI config file layer: value encoding, file format, legacy fallback, atomic writes."""

from __future__ import annotations

import configparser
import json
from pathlib import Path

import pytest

from p13ctl.display import config


def _round_trip_mode(mode: dict) -> dict:
    config.write_config_file({"version": 3, "mode": mode})
    raw = config.read_config_file()
    assert raw is not None
    return raw["mode"]


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
        "café",
    ],
)
def test_ambiguous_strings_round_trip(paths, text: str) -> None:
    loaded = _round_trip_mode({"name": "image", "image": text})
    assert loaded["image"] == text
    assert isinstance(loaded["image"], str)


def test_scalar_types_keep_their_type(paths) -> None:
    mode = {"a": True, "b": False, "c": None, "d": 7, "e": 0.25, "f": "7"}
    assert _round_trip_mode(mode) == mode


def test_written_file_is_real_ini(paths, full_config) -> None:
    ini, _ = paths
    config.write_config_file(full_config)

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
    assert config.read_config_file() is None

    ini.write_bytes(b"\xff\xfe\x00\x80 binary \xc3\x28")
    assert config.read_config_file() is None

    ini.write_text("[general]\nversion = 3\n[general]\n", encoding="utf-8")
    assert config.read_config_file() is None


def test_legacy_json_is_read_when_no_ini(paths, full_config) -> None:
    ini, legacy = paths
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(full_config), encoding="utf-8")

    assert config.read_config_file() == full_config
    assert not ini.exists()


def test_ini_wins_over_stale_json(paths) -> None:
    _, legacy = paths
    config.write_config_file({"version": 3, "panel": {"brightness": 99}})
    legacy.write_text(
        json.dumps({"version": 3, "panel": {"brightness": 5}}), encoding="utf-8"
    )

    raw = config.read_config_file()
    assert raw is not None
    assert raw["panel"] == {"brightness": 99}


def test_corrupt_legacy_json_returns_none(paths) -> None:
    _, legacy = paths
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{not json", encoding="utf-8")
    assert config.read_config_file() is None


def test_write_is_atomic_and_leaves_no_temp_files(paths, full_config) -> None:
    ini, _ = paths
    config.write_config_file(full_config)
    config.write_config_file({"version": 3, "panel": {"brightness": 1}})
    assert [p.name for p in ini.parent.iterdir()] == ["display.ini"]


def test_failed_write_keeps_previous_file(paths, monkeypatch) -> None:
    ini, _ = paths
    config.write_config_file({"version": 3, "panel": {"brightness": 42}})
    before = ini.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(config.os, "replace", boom)
    with pytest.raises(OSError):
        config.write_config_file({"version": 3, "panel": {"brightness": 7}})

    assert ini.read_bytes() == before
    assert [p.name for p in ini.parent.iterdir()] == ["display.ini"]


def test_config_dir_honors_xdg_config_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.config_dir() == tmp_path / "p13ctl"


@pytest.mark.parametrize("value", [None, ""])
def test_config_dir_falls_back_to_dot_config(monkeypatch, tmp_path: Path, value) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    if value is None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", value)
    assert config.config_dir() == tmp_path / ".config" / "p13ctl"
