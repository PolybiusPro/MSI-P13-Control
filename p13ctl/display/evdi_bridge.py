"""EVDI virtual monitor bridge for MSI P13 — streams framebuffer to USB panel."""

from __future__ import annotations

import ctypes
import io
import logging
import select
import time

import usb.core
from PIL import Image

from . import evdi_wrapper as evdi
from .artinchip import ArtinchipDisplay, DisplayError
from .edid import generate_edid

_LOGGER = logging.getLogger(__name__)

PANEL_WIDTH = 480
PANEL_HEIGHT = 480
STRIDE = PANEL_WIDTH * 4
BUF_SIZE = PANEL_WIDTH * PANEL_HEIGHT * 4
MAX_RECTS = 16
STATS_INTERVAL_S = 5.0

class EvdiBridge:
    """Bridge an EVDI virtual display to the Artinchip P13 USB panel."""

    def __init__(
        self,
        *,
        fps: int = 60,
        quality: int = 75,
        rotate: int = 0,
        on_mode_changed_hook=None,
    ):
        self.fps = max(1, fps)
        self.frame_interval = 1.0 / self.fps
        self.quality = quality
        self.rotate = rotate % 360
        self._on_mode_changed_hook = on_mode_changed_hook
        self._layout_reapply_pending = False
        self.handle = None
        self.display: ArtinchipDisplay | None = None
        self.running = False
        self._buf = (ctypes.c_ubyte * BUF_SIZE)()
        self._buf_ptr = ctypes.cast(self._buf, ctypes.c_void_p)
        self._rects = (evdi.EvdiRect * MAX_RECTS)()
        self._num_rects = ctypes.c_int(0)
        self._reregister_buffer = False
        self._edid = generate_edid()
        self._jpeg_buf = io.BytesIO()
        self._last_usb_retry = 0.0

    def setup_evdi(self) -> None:
        version = evdi.get_lib_version()
        _LOGGER.info("libevdi %d.%d.%d", *version)

        device_idx = -1
        for i in range(20):
            if evdi.check_device(i) == evdi.AVAILABLE:
                device_idx = i
                break

        if device_idx < 0:
            _LOGGER.info("adding EVDI device")
            if evdi.add_device() < 0:
                raise DisplayError(
                    "failed to add EVDI device — is the kernel module loaded?\n"
                    "  sudo modprobe evdi initial_device_count=1"
                )
            for i in range(20):
                if evdi.check_device(i) == evdi.AVAILABLE:
                    device_idx = i
                    break

        if device_idx < 0:
            raise DisplayError("no EVDI device available after add")

        _LOGGER.info("opening EVDI device %d", device_idx)
        self.handle = evdi.open_device(device_idx)
        if self.handle is None:
            raise DisplayError("failed to open EVDI device")

        evdi.connect(self.handle, self._edid, PANEL_WIDTH * PANEL_HEIGHT)
        _LOGGER.info("EVDI connected with %dx%d EDID", PANEL_WIDTH, PANEL_HEIGHT)
        evdi.register_buffer(
            self.handle,
            0,
            self._buf_ptr,
            PANEL_WIDTH,
            PANEL_HEIGHT,
            STRIDE,
            self._rects,
            MAX_RECTS,
        )

    def setup_usb(self) -> bool:
        try:
            self.display = ArtinchipDisplay(rotate=self.rotate)
            self.display.connect()
        except DisplayError as exc:
            _LOGGER.debug("USB display not ready: %s", exc)
            self.display = None
            return False

        if self.display.fps and self.display.fps < self.fps:
            self.fps = int(self.display.fps)
            self.frame_interval = 1.0 / self.fps
            _LOGGER.info("capped to panel native %d fps", self.fps)
        return True

    def _encode_jpeg(self) -> bytes:
        img = Image.frombuffer(
            "RGB",
            (PANEL_WIDTH, PANEL_HEIGHT),
            self._buf,
            "raw",
            "BGRX",
            STRIDE,
            1,
        )
        self._jpeg_buf.seek(0)
        self._jpeg_buf.truncate()
        img.save(
            self._jpeg_buf,
            format="JPEG",
            quality=self.quality,
            optimize=False,
            subsampling=2,
        )
        return self._jpeg_buf.getvalue()

    def _send_frame(self) -> None:
        assert self.display is not None
        self._num_rects.value = MAX_RECTS
        evdi.grab_pixels(self.handle, self._rects, ctypes.byref(self._num_rects))
        # Always encode — many GPU drivers don't report dirty rects to EVDI.
        self.display.send_jpeg(self._encode_jpeg())

    def _poll_events(self, event_fd: int, timeout: float) -> None:
        if timeout <= 0:
            timeout = 0
        try:
            ready, _, _ = select.select([event_fd], [], [], timeout)
        except (OSError, ValueError):
            return
        if ready:
            evdi.handle_events(self.handle, self._evt_ctx)

    def run(self) -> None:
        if self.handle is None:
            self.setup_evdi()
        if self.display is None:
            self.setup_usb()

        self._cb_update = evdi.UPDATE_READY_HANDLER(lambda _buf_id, _ud: None)
        self._cb_mode = evdi.MODE_CHANGED_HANDLER(self._on_mode_changed)
        self._cb_dpms = evdi.DPMS_HANDLER(lambda _mode, _ud: None)
        self._cb_crtc = evdi.CRTC_STATE_HANDLER(lambda _state, _ud: None)
        self._cb_cursor_set = evdi.CURSOR_SET_HANDLER(lambda _cs, _ud: None)
        self._cb_cursor_move = evdi.CURSOR_MOVE_HANDLER(lambda _cm, _ud: None)
        self._cb_ddcci = evdi.DDCCI_HANDLER(lambda _dd, _ud: None)

        self._evt_ctx = evdi.EvdiEventContext()
        self._evt_ctx.dpms_handler = self._cb_dpms
        self._evt_ctx.mode_changed_handler = self._cb_mode
        self._evt_ctx.update_ready_handler = self._cb_update
        self._evt_ctx.crtc_state_handler = self._cb_crtc
        self._evt_ctx.cursor_set_handler = self._cb_cursor_set
        self._evt_ctx.cursor_move_handler = self._cb_cursor_move
        self._evt_ctx.ddcci_data_handler = self._cb_ddcci
        self._evt_ctx.user_data = None

        event_fd = evdi.get_event_fd(self.handle)
        _LOGGER.info("EVDI bridge target %d fps (event fd=%d)", self.fps, event_fd)

        self.running = True
        next_frame = time.perf_counter()
        stats_t0 = time.perf_counter()
        stats_frames = 0

        while self.running:
            now = time.perf_counter()
            wait = next_frame - now
            if wait > 0.001:
                self._poll_events(event_fd, min(wait, 0.01))
                continue

            self._poll_events(event_fd, 0)

            if self._reregister_buffer:
                self._reregister_buffer = False
                _LOGGER.info("re-registering EVDI buffer after mode change")
                evdi.unregister_buffer(self.handle, 0)
                evdi.register_buffer(
                    self.handle,
                    0,
                    self._buf_ptr,
                    PANEL_WIDTH,
                    PANEL_HEIGHT,
                    STRIDE,
                    self._rects,
                    MAX_RECTS,
                )
                if self._layout_reapply_pending and self._on_mode_changed_hook:
                    self._layout_reapply_pending = False
                    time.sleep(0.5)
                    try:
                        self._on_mode_changed_hook()
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.warning("layout re-apply after mode change failed: %s", exc)

            if self.display is None:
                if now - self._last_usb_retry >= 2.0:
                    self.setup_usb()
                    self._last_usb_retry = now
                next_frame += self.frame_interval
                continue

            try:
                evdi.request_update(self.handle, 0)
                self._send_frame()
                stats_frames += 1
            except (DisplayError, usb.core.USBError) as exc:
                _LOGGER.warning("frame send failed: %s", exc)
                if self.display is not None:
                    self.display.close()
                self.display = None
                self._last_usb_retry = now

            next_frame += self.frame_interval
            now = time.perf_counter()
            if next_frame < now:
                # Behind schedule — resync instead of piling up latency.
                next_frame = now + self.frame_interval

            if stats_frames and (now - stats_t0) >= STATS_INTERVAL_S:
                _LOGGER.info("stream: %.1f fps", stats_frames / (now - stats_t0))
                stats_t0 = now
                stats_frames = 0

    def _on_mode_changed(self, mode, _user_data) -> None:
        _LOGGER.info(
            "EVDI mode changed: %dx%d @ %dHz %dbpp",
            mode.width,
            mode.height,
            mode.refresh_rate,
            mode.bits_per_pixel,
        )
        self._reregister_buffer = True
        self._layout_reapply_pending = True

    def shutdown(self) -> None:
        self.running = False
        if self.display is not None:
            self.display.close()
            self.display = None
        if self.handle is not None:
            evdi.unregister_buffer(self.handle, 0)
            evdi.disconnect(self.handle)
            evdi.close_device(self.handle)
            self.handle = None
            _LOGGER.info("EVDI disconnected")
