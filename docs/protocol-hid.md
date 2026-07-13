# HID frame protocol (33c3:0e02, interface 1)

Reverse engineered by capturing the device's USB HID traffic with Wireshark.

## Device identification

| Field | Value |
| --- | --- |
| USB VID:PID | `33c3:0e02` |
| HID interface | **1** (`MI_01`) |
| Output report size | **1025** bytes (report ID `0x00` + 1024 data) |
| Max payload buffer | **1024** bytes (`FRAME_SIZE_MAX`) |

## Frame format

Observed in captured USB HID reports.

```
5A  escape(LL LL <payload>)  CC  5A
│   └──┬──┘                    │   └── end marker (0x5A)
│      │                       └── checksum (not escaped)
│      └── length + UTF-8 HTTP body, with escaping
└── start marker (0x5A)
```

| Field | Rule |
| --- | --- |
| Logical length | BE u16 = `len(payload) + 5` (start + len + payload + cksum + end) |
| Checksum | `(sum(payload) + length_hi + length_lo) & 0xFF` |
| Escape | Inside length+payload only: `5A` → `5B 01`, `5B` → `5B 02` |

Escaping matters whenever a length byte would be `0x5A`/`0x5B` (e.g. brightness
100 → logical length 90 = `0x005A`) or when those bytes appear in the payload.

The framed bytes go at offset **1** of the 1025-byte HID output report
(report ID `0x00` at offset 0).

## Application protocol (inside the frame payload)

Commands are **HTTP-like POST messages** with JSON bodies:

### Connect / enumerate

```
POST conn 1\r\n
SeqNumber=100\r\n
ContentType=json\r\n
```

Response body is JSON:

```json
{
  "Manufacturer": "...",
  "Model": "MPG CORELIQUID P13 ...",
  "SN": "BYZL...",
  "Brightness": 100,
  "Degree": 0,
  "BootFinish": 1,
  "RealtimeDisplay": 0,
  "ExtendedDisplay": 0,
  "Version": { "App": "...", "Firmware": "...", "Hardware": "..." }
}
```

The host sends `POST conn` **twice**, then reads the response.

### Leave firmware splash / enable host framebuffer

After cold boot the panel shows a firmware-stored default image until this succeeds:

```
POST extendedDisplay 1\r\n
SeqNumber=110\r\n
ContentType=json\r\n
ContentLength=15\r\n
\r\n
{"enable":true}
```

Related:

```
POST realtimeDisplay 1\r\n
SeqNumber=112\r\n
...
{"enable":true}
```

`p13ctl` calls these automatically from `ArtinchipDisplay.connect()` (desktop / image / test / sysmon). Manual:

```bash
p13ctl hid host on
```

### Set brightness

```
POST brightness 1\r\n
SeqNumber=100\r\n
ContentType=json\r\n
ContentLength=12\r\n
\r\n
{"value":50}
```

Sent **twice** per change (observed on the wire).

### Set rotation

```
POST rotate 1\r\n
SeqNumber=100\r\n
ContentType=json\r\n
ContentLength=13\r\n
\r\n
{"degree":90}
```

Also sent twice. User-facing `degree` is `0`, `90`, `180`, or `270`; firmware
wire Degree is offset by +180° (panel mount). Host JPEG is rotated by firmware —
do not also software-rotate the same frames or orientation doubles
(CLI 0→180°, 90→0°). `p13ctl hid rotate` re-sends static content without a
second rotate, or applies KScreen rotation for extended display.

## Linux usage

```bash
p13ctl hid info
p13ctl hid host on          # leave cold-boot splash (also done by display commands)
p13ctl hid brightness 75
p13ctl hid rotate 90
p13ctl hid sniff --duration 10
```
