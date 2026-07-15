from __future__ import annotations

from subprocess import CompletedProcess

from p13ctl.display import service

def test_stop_display_service_stops_active_unit(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(service.shutil, "which", lambda _name: "/usr/bin/systemctl")

    def run(command, **_kwargs):
        calls.append(command)
        return CompletedProcess(command, 0)

    monkeypatch.setattr(service.subprocess, "run", run)

    assert service.stop_display_service() is True
    assert calls == [
        ["systemctl", "--user", "is-active", "--quiet", service.DISPLAY_SERVICE],
        ["systemctl", "--user", "stop", service.DISPLAY_SERVICE],
    ]

def test_stop_display_service_skips_inactive_unit(monkeypatch) -> None:
    monkeypatch.setattr(service.shutil, "which", lambda _name: "/usr/bin/systemctl")
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda command, **_kwargs: CompletedProcess(command, 3),
    )

    assert service.stop_display_service() is False
