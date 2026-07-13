"""systemd user service helpers for the desktop mirror."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

SERVICE_NAME = "p13-display.service"
SERVICE_UNIT = Path.home() / ".config/systemd/user" / SERVICE_NAME

def mirror_service_installed() -> bool:
    return SERVICE_UNIT.is_file()

def mirror_is_running() -> bool:
    if not mirror_service_installed():
        return False
    result = subprocess.run(
        ["systemctl", "--user", "is-active", SERVICE_NAME],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "active"

def start_mirror_service() -> None:
    subprocess.run(
        ["systemctl", "--user", "start", SERVICE_NAME],
        check=True,
        capture_output=True,
        text=True,
    )

def stop_mirror_service(*, wait_s: float = 8.0) -> None:
    subprocess.run(
        ["systemctl", "--user", "stop", SERVICE_NAME],
        check=True,
        capture_output=True,
        text=True,
    )
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not mirror_is_running():
            return
        time.sleep(0.2)
    raise subprocess.CalledProcessError(
        1,
        "systemctl",
        "p13-display.service did not stop in time",
        "",
    )
