"""Minimal ctypes wrapper for libevdi.so — the EVDI userspace library."""

from __future__ import annotations

import ctypes
import os

# Device status enum (matches evdi_device_status)
AVAILABLE = 0
UNRECOGNIZED = 1
NOT_PRESENT = 2

def _find_libevdi() -> ctypes.CDLL:
    for name in ("libevdi.so.1", "libevdi.so"):
        try:
            return ctypes.cdll.LoadLibrary(name)
        except OSError:
            pass
    for path in (
        "/usr/libexec/displaylink/libevdi.so",
        "/usr/libexec/displaylink/libevdi.so.1.15.0",
        "/usr/lib64/libevdi.so.1",
        "/usr/lib/x86_64-linux-gnu/libevdi.so.1",
        "/usr/local/lib/libevdi.so.1",
        "/usr/lib/libevdi.so.1",
    ):
        if os.path.exists(path):
            return ctypes.cdll.LoadLibrary(path)
    raise RuntimeError(
        "libevdi.so not found. Install EVDI:\n"
        "  ./install.sh --evdi-only"
    )

_lib = None

def _get_lib() -> ctypes.CDLL:
    global _lib
    if _lib is None:
        _lib = _find_libevdi()
        _setup_signatures(_lib)
    return _lib

class EvdiRect(ctypes.Structure):
    _fields_ = [
        ("x1", ctypes.c_int),
        ("y1", ctypes.c_int),
        ("x2", ctypes.c_int),
        ("y2", ctypes.c_int),
    ]

class EvdiMode(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("refresh_rate", ctypes.c_int),
        ("bits_per_pixel", ctypes.c_int),
        ("pixel_format", ctypes.c_uint),
    ]

class EvdiBuffer(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_int),
        ("buffer", ctypes.c_void_p),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("stride", ctypes.c_int),
        ("rects", ctypes.POINTER(EvdiRect)),
        ("rect_count", ctypes.c_int),
    ]

class EvdiCursorSet(ctypes.Structure):
    _fields_ = [
        ("hot_x", ctypes.c_int32),
        ("hot_y", ctypes.c_int32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("enabled", ctypes.c_uint8),
        ("buffer_length", ctypes.c_uint32),
        ("buffer", ctypes.POINTER(ctypes.c_uint32)),
        ("pixel_format", ctypes.c_uint32),
        ("stride", ctypes.c_uint32),
    ]

class EvdiCursorMove(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
    ]

class EvdiDdcciData(ctypes.Structure):
    _fields_ = [
        ("address", ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
        ("buffer_length", ctypes.c_uint32),
        ("buffer", ctypes.POINTER(ctypes.c_uint8)),
    ]

class EvdiVersion(ctypes.Structure):
    _fields_ = [
        ("version_major", ctypes.c_int),
        ("version_minor", ctypes.c_int),
        ("version_patchlevel", ctypes.c_int),
    ]

DPMS_HANDLER = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)
MODE_CHANGED_HANDLER = ctypes.CFUNCTYPE(None, EvdiMode, ctypes.c_void_p)
UPDATE_READY_HANDLER = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)
CRTC_STATE_HANDLER = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)
CURSOR_SET_HANDLER = ctypes.CFUNCTYPE(None, EvdiCursorSet, ctypes.c_void_p)
CURSOR_MOVE_HANDLER = ctypes.CFUNCTYPE(None, EvdiCursorMove, ctypes.c_void_p)
DDCCI_HANDLER = ctypes.CFUNCTYPE(None, EvdiDdcciData, ctypes.c_void_p)

class EvdiEventContext(ctypes.Structure):
    _fields_ = [
        ("dpms_handler", DPMS_HANDLER),
        ("mode_changed_handler", MODE_CHANGED_HANDLER),
        ("update_ready_handler", UPDATE_READY_HANDLER),
        ("crtc_state_handler", CRTC_STATE_HANDLER),
        ("cursor_set_handler", CURSOR_SET_HANDLER),
        ("cursor_move_handler", CURSOR_MOVE_HANDLER),
        ("ddcci_data_handler", DDCCI_HANDLER),
        ("user_data", ctypes.c_void_p),
    ]

def _setup_signatures(lib: ctypes.CDLL) -> None:
    lib.evdi_check_device.argtypes = [ctypes.c_int]
    lib.evdi_check_device.restype = ctypes.c_int

    lib.evdi_add_device.argtypes = []
    lib.evdi_add_device.restype = ctypes.c_int

    lib.evdi_open.argtypes = [ctypes.c_int]
    lib.evdi_open.restype = ctypes.c_void_p

    lib.evdi_close.argtypes = [ctypes.c_void_p]
    lib.evdi_close.restype = None

    lib.evdi_connect.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint32]
    lib.evdi_connect.restype = None

    lib.evdi_disconnect.argtypes = [ctypes.c_void_p]
    lib.evdi_disconnect.restype = None

    lib.evdi_register_buffer.argtypes = [ctypes.c_void_p, EvdiBuffer]
    lib.evdi_register_buffer.restype = None

    lib.evdi_unregister_buffer.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.evdi_unregister_buffer.restype = None

    lib.evdi_request_update.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.evdi_request_update.restype = ctypes.c_bool

    lib.evdi_grab_pixels.argtypes = [ctypes.c_void_p, ctypes.POINTER(EvdiRect), ctypes.POINTER(ctypes.c_int)]
    lib.evdi_grab_pixels.restype = None

    lib.evdi_handle_events.argtypes = [ctypes.c_void_p, ctypes.POINTER(EvdiEventContext)]
    lib.evdi_handle_events.restype = None

    lib.evdi_get_event_ready.argtypes = [ctypes.c_void_p]
    lib.evdi_get_event_ready.restype = ctypes.c_int

    lib.evdi_get_lib_version.argtypes = [ctypes.POINTER(EvdiVersion)]
    lib.evdi_get_lib_version.restype = None

def get_lib_version() -> tuple[int, int, int]:
    lib = _get_lib()
    version = EvdiVersion()
    lib.evdi_get_lib_version(ctypes.byref(version))
    return version.version_major, version.version_minor, version.version_patchlevel

def check_device(device_index: int) -> int:
    return _get_lib().evdi_check_device(device_index)

def add_device() -> int:
    return _get_lib().evdi_add_device()

def open_device(device_index: int):
    handle = _get_lib().evdi_open(device_index)
    if not handle:
        return None
    return handle

def close_device(handle) -> None:
    _get_lib().evdi_close(handle)

def connect(handle, edid_bytes: bytes, pixel_area_limit: int = 0) -> None:
    _get_lib().evdi_connect(handle, edid_bytes, len(edid_bytes), pixel_area_limit)

def disconnect(handle) -> None:
    _get_lib().evdi_disconnect(handle)

def register_buffer(handle, buf_id: int, buffer, width: int, height: int, stride: int, rects_array, rect_count: int):
    buf = EvdiBuffer()
    buf.id = buf_id
    buf.buffer = buffer
    buf.width = width
    buf.height = height
    buf.stride = stride
    buf.rects = rects_array
    buf.rect_count = rect_count
    _get_lib().evdi_register_buffer(handle, buf)
    return buf

def unregister_buffer(handle, buf_id: int) -> None:
    _get_lib().evdi_unregister_buffer(handle, buf_id)

def request_update(handle, buf_id: int) -> bool:
    return _get_lib().evdi_request_update(handle, buf_id)

def grab_pixels(handle, rects_array, num_rects_ptr) -> None:
    _get_lib().evdi_grab_pixels(handle, rects_array, num_rects_ptr)

def get_event_fd(handle) -> int:
    return _get_lib().evdi_get_event_ready(handle)

def handle_events(handle, event_ctx: EvdiEventContext) -> None:
    _get_lib().evdi_handle_events(handle, ctypes.byref(event_ctx))
