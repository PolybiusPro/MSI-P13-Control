"""Device discovery for MSI MPG CoreLiquid P13."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import hid

from .display.artinchip import ArtinchipDisplay, VENDOR_ID, PRODUCT_ID

@dataclass
class P13DeviceInfo:
  vendor_id: int
  product_id: int
  serial: Optional[str]
  manufacturer: Optional[str]
  product: Optional[str]
  display_interface: int
  hid_interface: int
  hid_path: Optional[str]

  @property
  def usb_id(self) -> str:
    return f"{self.vendor_id:04x}:{self.product_id:04x}"

def list_devices() -> list[P13DeviceInfo]:
  """Return P13 candidates visible on the system."""
  results: list[P13DeviceInfo] = []
  seen_serial: set[str] = set()

  for info in hid.enumerate(VENDOR_ID, PRODUCT_ID):
    serial = info.get("serial_number") or ""
    key = serial or str(info.get("path"))
    if key in seen_serial:
      continue
    seen_serial.add(key)
    hid_path = None
    if info.get("interface_number") == 1:
      hid_path = info["path"]
    results.append(
      P13DeviceInfo(
        vendor_id=info.get("vendor_id", VENDOR_ID),
        product_id=info.get("product_id", PRODUCT_ID),
        serial=info.get("serial_number"),
        manufacturer=info.get("manufacturer_string"),
        product=info.get("product_string"),
        display_interface=0,
        hid_interface=1,
        hid_path=hid_path,
      )
    )

  # hidapi may only expose HID IF; still note display if USB node exists.
  if not results and ArtinchipDisplay.find() is not None:
    results.append(
      P13DeviceInfo(
        vendor_id=VENDOR_ID,
        product_id=PRODUCT_ID,
        serial=None,
        manufacturer="Artinchip Technology Co., Ltd.",
        product="AicUsbDisplay / MPG CoreLiquid P13",
        display_interface=0,
        hid_interface=1,
        hid_path=None,
      )
    )
  return results
