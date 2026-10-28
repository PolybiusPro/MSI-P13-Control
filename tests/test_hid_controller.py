"""Pure protocol helpers and command semantics of the HID controller."""

from __future__ import annotations

import pytest

from p13ctl.hid import msi_p13
from p13ctl.hid.framing import decode
from p13ctl.hid.msi_p13 import HidError, P13HidController


class _FakeHid:
    def __init__(self) -> None:
        self.reports: list[bytes] = []

    def write(self, report: bytes) -> int:
        self.reports.append(report)
        return len(report)

    def messages(self) -> list[str]:
        return [decode(r[1:]).decode() for r in self.reports]


@pytest.fixture
def ctrl(monkeypatch):
    monkeypatch.setattr(msi_p13.time, "sleep", lambda _s: None)
    fake = _FakeHid()
    controller = P13HidController(device=fake)
    return controller, fake


def test_build_post_with_body_matches_wire_format() -> None:
    assert msi_p13._build_post("brightness 1", '{"value":50}') == (
        "POST brightness 1\r\nSeqNumber=100\r\nContentType=json\r\n"
        'ContentLength=12\r\n\r\n{"value":50}'
    )


def test_build_post_without_body_has_no_content_length() -> None:
    assert msi_p13._build_post("conn 1", seq=7) == (
        "POST conn 1\r\nSeqNumber=7\r\nContentType=json\r\n"
    )


def test_parse_post_response_splits_headers_and_body() -> None:
    headers, body = msi_p13._parse_post_response(
        'POST conn 1\r\nSeqNumber=101\r\nContentLength=2\r\n\r\n{}'
    )
    assert headers == {"command": "POST conn 1", "SeqNumber": "101", "ContentLength": "2"}
    assert body == "{}"


def test_parse_json_body_honors_content_length_and_strips_padding(ctrl) -> None:
    controller, _ = ctrl
    text = 'POST conn 1\r\nContentLength=8\r\n\r\n{"a": 1}\x00\x00garbage'
    assert controller._parse_json_body(text) == {"a": 1}


def test_parse_json_body_rejects_invalid_json(ctrl) -> None:
    controller, _ = ctrl
    with pytest.raises(HidError, match="invalid JSON"):
        controller._parse_json_body("POST conn 1\r\n\r\nnot json")


# Reply shapes are assumed: no real firmware ack has been captured.
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("POST extendedDisplay 1\r\nSeqNumber=111\r\n", "ack"),
        ("HTTP/1.1 200 OK\r\nSeqNumber=5\r\n", "ack"),
        ("HTTP/1.1 400 Bad Request\r\nSeqNumber=111\r\n", "nack"),
        ("POST other 1\r\nSeqNumber=5\r\nContentLength=200\r\n", None),
    ],
)
def test_classify_ack(response: str, expected) -> None:
    assert msi_p13._classify_ack(response, 110) == expected


def test_command_with_ack_reports_failure_when_nothing_acks(ctrl, monkeypatch) -> None:
    controller, fake = ctrl

    def no_reply(*_a, **_k):
        raise HidError("HID read timeout")

    monkeypatch.setattr(controller, "_read_until", no_reply)
    assert controller._command_with_ack("extendedDisplay 1", "{}", 110, retries=2) is False
    # one write per attempt, no blind resend that pretends success
    assert len(fake.reports) == 2


def test_command_with_ack_reports_failure_on_repeated_nack(ctrl, monkeypatch) -> None:
    controller, _ = ctrl
    monkeypatch.setattr(
        controller, "_read_until", lambda **_k: "HTTP/1.1 400 Bad Request\r\n"
    )
    assert controller._command_with_ack("rotate 1", "{}", 100) is False


def test_command_with_ack_succeeds_on_ack(ctrl, monkeypatch) -> None:
    controller, fake = ctrl
    monkeypatch.setattr(
        controller, "_read_until", lambda **_k: "POST extendedDisplay 1\r\nSeqNumber=111\r\n"
    )
    assert controller._command_with_ack("extendedDisplay 1", "{}", 110) is True
    assert len(fake.reports) == 1


def test_enable_host_display_sends_both_commands_and_returns_nothing(ctrl) -> None:
    controller, fake = ctrl
    assert controller.enable_host_display() is None
    sent = fake.messages()
    assert sent[0].startswith("POST extendedDisplay 1")
    assert sent[-1].startswith("POST realtimeDisplay 1")
