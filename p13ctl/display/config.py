"""Read and write the p13ctl INI config file.

This is the file layer only: encoding, atomic writes, the process-wide lock and
the legacy JSON fallback. What the sections mean lives in ``layout``.
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

CONFIG_VERSION = 3


def config_dir() -> Path:
    """Return the p13ctl config directory, honoring XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "p13ctl"


LAYOUT_PATH = config_dir() / "display.ini"
LEGACY_JSON_PATH = config_dir() / "display-layout.json"
# Serializes read-modify-write cycles on the config file within this process.
CONFIG_LOCK = threading.RLock()
KNOWN_SECTIONS = frozenset({"version", "virtual", "panel", "stream", "mode"})
GENERAL_SECTION = "general"


def encode_value(value: Any) -> str:
    """Encode one config value for an INI line.

    Everything is JSON except plain strings that JSON cannot parse (paths,
    "0,0"), which stay raw so the file is readable. A string that JSON would
    parse as something else ("123", "true") is JSON-quoted to keep its type.
    """
    if isinstance(value, str) and value == value.strip() and value.isprintable():
        try:
            json.loads(value)
        except json.JSONDecodeError:
            return value
    return json.dumps(value, ensure_ascii=False)


def decode_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _new_parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    return parser


def read_config_file() -> dict[str, Any] | None:
    """Return the raw config dict from display.ini, else the legacy JSON, else None."""
    if LAYOUT_PATH.is_file():
        try:
            parser = _new_parser()
            parser.read_string(LAYOUT_PATH.read_text(encoding="utf-8"))
        except (configparser.Error, OSError, UnicodeDecodeError) as exc:
            _LOGGER.warning("ignoring invalid saved config: %s", exc)
            return None
        data: dict[str, Any] = {}
        if parser.has_section(GENERAL_SECTION):
            for key, raw in parser.items(GENERAL_SECTION):
                data[key] = decode_value(raw)
        for section in parser.sections():
            if section != GENERAL_SECTION:
                data[section] = {k: decode_value(v) for k, v in parser.items(section)}
        return data

    if LEGACY_JSON_PATH.is_file():
        try:
            legacy = json.loads(LEGACY_JSON_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            _LOGGER.warning("ignoring invalid saved config: %s", exc)
            return None
        return legacy if isinstance(legacy, dict) else None
    return None


def write_config_file(data: dict[str, Any]) -> None:
    """Atomically write the config dict to display.ini."""
    parser = _new_parser()
    parser.add_section(GENERAL_SECTION)
    parser.set(GENERAL_SECTION, "version", encode_value(data.get("version", CONFIG_VERSION)))
    for name, section in data.items():
        if name == "version" or not isinstance(section, dict):
            continue
        parser.add_section(name)
        for key, value in section.items():
            parser.set(name, key, encode_value(value))

    LAYOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=LAYOUT_PATH.parent, prefix=f".{LAYOUT_PATH.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            parser.write(handle)
        os.replace(tmp_name, LAYOUT_PATH)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def unknown_sections() -> dict[str, Any]:
    """Sections written by other versions, to carry through a rewrite."""
    raw = read_config_file() or {}
    return {
        name: section
        for name, section in raw.items()
        if name not in KNOWN_SECTIONS and isinstance(section, dict)
    }
