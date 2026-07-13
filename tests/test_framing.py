"""Golden vectors for the P13 HID frame protocol."""

from __future__ import annotations

import unittest

from p13ctl.hid.framing import ProtocolError, decode, encode

def _hex(data: bytes) -> str:
    return "-".join(f"{b:02X}" for b in data)

class FramingTests(unittest.TestCase):
    def test_brightness_50_frame(self) -> None:
        msg = (
            "POST brightness 1\r\n"
            "SeqNumber=100\r\n"
            "ContentType=json\r\n"
            "ContentLength=12\r\n"
            "\r\n"
            '{"value":50}'
        )
        frame = encode(msg.encode())
        self.assertEqual(
            _hex(frame),
            "5A-00-59-50-4F-53-54-20-62-72-69-67-68-74-6E-65-73-73-20-31-0D-0A-"
            "53-65-71-4E-75-6D-62-65-72-3D-31-30-30-0D-0A-"
            "43-6F-6E-74-65-6E-74-54-79-70-65-3D-6A-73-6F-6E-0D-0A-"
            "43-6F-6E-74-65-6E-74-4C-65-6E-67-74-68-3D-31-32-0D-0A-0D-0A-"
            "7B-22-76-61-6C-75-65-22-3A-35-30-7D-65-5A",
        )
        self.assertEqual(decode(frame), msg.encode())

    def test_brightness_100_escapes_length_0x5a(self) -> None:
        # logical_len = 90 = 0x005A → length low byte escaped as 5B 01
        msg = (
            "POST brightness 1\r\n"
            "SeqNumber=100\r\n"
            "ContentType=json\r\n"
            "ContentLength=13\r\n"
            "\r\n"
            '{"value":100}'
        )
        frame = encode(msg.encode())
        self.assertEqual(frame[0], 0x5A)
        self.assertEqual(frame[1:4], bytes([0x00, 0x5B, 0x01]))
        self.assertEqual(decode(frame), msg.encode())

    def test_payload_escape_5a_and_5b(self) -> None:
        payload = bytes([0x41, 0x5A, 0x5B, 0x42])
        frame = encode(payload)
        # After start: escaped length (5) then escaped payload
        self.assertIn(bytes([0x5B, 0x01]), frame)
        self.assertIn(bytes([0x5B, 0x02]), frame)
        self.assertEqual(decode(frame), payload)

    def test_decode_tolerates_hid_padding(self) -> None:
        payload = b"hello"
        frame = encode(payload) + bytes(100)
        self.assertEqual(decode(frame), payload)

    def test_roundtrip_lengths_crossing_escape_boundaries(self) -> None:
        for size in (84, 85, 86, 250, 251, 300):
            payload = b"A" * size
            self.assertEqual(decode(encode(payload)), payload)

    def test_bad_checksum_rejected(self) -> None:
        frame = bytearray(encode(b"test"))
        frame[-2] ^= 0xFF
        with self.assertRaises(ProtocolError):
            decode(bytes(frame))

if __name__ == "__main__":
    unittest.main()
