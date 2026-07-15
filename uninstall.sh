#!/usr/bin/env bash
# Uninstall p13ctl (MSI MPG CoreLiquid P13 360 Linux driver)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
CONFIG_DIR="${HOME}/.config/p13ctl"

DO_PYTHON=1
DO_UDEV=1
DO_EVDI=1
DO_CONFIG=0
DO_BLACKLIST=0
DO_PACKAGES=0
DO_BOOT_DISPLAY=1
DO_GUI=1

die() { echo "error: $*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: $0 [options]

Remove p13ctl install artifacts (virtualenv, udev rules, EVDI config/module).

By default keeps user settings in ~/.config/p13ctl and system packages.

Options:
  --remove-config     Delete ~/.config/p13ctl
  --remove-blacklist  Delete /etc/modprobe.d/blacklist-aic-usb-display.conf
  --remove-packages   Uninstall displaylink/evdi-dkms packages (dnf/apt)
  --remove-evdi       Explicitly remove EVDI (also the default)
  --keep-evdi         Leave EVDI kernel module and boot config in place
  --keep-udev         Leave udev rules in place
  --keep-python       Leave .venv in place
  --keep-boot-display Leave p13-display.service user unit in place
  --keep-gui            Leave GUI desktop launcher in place
  -h, --help          Show this help

Examples:
  $0
  $0 --remove-config
  $0 --remove-packages --remove-config
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remove-config) DO_CONFIG=1; shift ;;
    --remove-blacklist) DO_BLACKLIST=1; shift ;;
    --remove-packages) DO_PACKAGES=1; shift ;;
    --remove-evdi) DO_EVDI=1; shift ;;
    --keep-evdi) DO_EVDI=0; shift ;;
    --keep-udev) DO_UDEV=0; shift ;;
    --keep-python) DO_PYTHON=0; shift ;;
    --keep-boot-display) DO_BOOT_DISPLAY=0; shift ;;
    --keep-gui) DO_GUI=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  die "this uninstaller is for Linux only"
fi

run_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

remove_file() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    run_root rm -f "$path"
    echo "    removed $path"
  fi
}

unload_evdi() {
  if lsmod | grep -q '^evdi '; then
    echo "==> Unloading EVDI kernel module"
    run_root modprobe -r evdi 2>/dev/null || true
  fi
}

remove_evdi_dkms() {
  if ! command -v dkms >/dev/null 2>&1; then
    return
  fi
  local line version
  while IFS= read -r line; do
    if [[ "$line" =~ ^evdi/([^,]+), ]]; then
      version="${BASH_REMATCH[1]}"
      version="${version# }"
      echo "==> Removing DKMS module evdi/$version"
      run_root dkms remove "evdi/$version" --all 2>/dev/null || true
    fi
  done < <(dkms status 2>/dev/null | grep -E '^evdi/' || true)
}

remove_evdi_configs() {
  echo "==> Removing EVDI boot/modprobe config"
  if systemctl is-enabled evdi-p13.service &>/dev/null; then
    run_root systemctl disable evdi-p13.service 2>/dev/null || true
  fi
  run_root systemctl stop evdi-p13.service 2>/dev/null || true
  remove_file /etc/systemd/system/evdi-p13.service
  run_root systemctl daemon-reload 2>/dev/null || true
  remove_file /etc/modprobe.d/evdi-p13.conf
  remove_file /etc/modules-load.d/evdi-p13.conf
  # Do not restore displaylink's /etc/modprobe.d/evdi.conf — p13ctl owns evdi config.
}

remove_libevdi_link() {
  local link="/usr/lib64/libevdi.so.1"
  if [[ -L "$link" ]] && readlink "$link" | grep -q displaylink; then
    echo "==> Removing libevdi symlink"
    remove_file "$link"
    run_root ldconfig 2>/dev/null || true
  fi
}

remove_evdi_packages() {
  if command -v dnf >/dev/null 2>&1; then
    if rpm -q displaylink &>/dev/null; then
      echo "==> Removing displaylink package (dnf)"
      run_root dnf remove -y displaylink 2>/dev/null || true
    fi
  elif command -v apt-get >/dev/null 2>&1; then
    if dpkg -l evdi-dkms &>/dev/null 2>&1; then
      echo "==> Removing evdi-dkms package (apt)"
      run_root apt-get remove -y evdi-dkms libevdi1 2>/dev/null || true
    fi
  fi
}

remove_udev() {
  echo "==> Removing udev rules"
  remove_file /etc/udev/rules.d/99-msi-p13.rules
  run_root udevadm control --reload-rules 2>/dev/null || true
  run_root udevadm trigger 2>/dev/null || true
}

remove_blacklist() {
  echo "==> Removing Artinchip blacklist"
  remove_file /etc/modprobe.d/blacklist-aic-usb-display.conf
}

remove_python() {
  if [[ -d "$VENV" ]]; then
    echo "==> Removing virtualenv at $VENV"
    rm -rf "$VENV"
  fi
}

remove_config() {
  if [[ -d "$CONFIG_DIR" ]]; then
    echo "==> Removing user config at $CONFIG_DIR"
    rm -rf "$CONFIG_DIR"
  fi
}

remove_boot_display() {
  local unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  local name
  for name in p13-display.service p13-panel-off.service; do
    if systemctl --user is-enabled "$name" &>/dev/null; then
      echo "==> Disabling $name"
      systemctl --user disable --now "$name" 2>/dev/null || true
    fi
    if [[ -f "$unit_dir/$name" ]]; then
      rm -f "$unit_dir/$name"
      echo "    removed $unit_dir/$name"
    fi
  done
  systemctl --user daemon-reload 2>/dev/null || true
}

remove_gui_desktop() {
  local desktop="${XDG_DATA_HOME:-$HOME/.local/share}/applications/p13ctl-gui.desktop"
  if [[ -f "$desktop" ]]; then
    echo "==> Removing GUI launcher"
    rm -f "$desktop"
    echo "    removed $desktop"
    if command -v update-desktop-database >/dev/null 2>&1; then
      update-desktop-database "$(dirname "$desktop")" 2>/dev/null || true
    fi
  fi
}

# --- uninstall ---

if [[ "$DO_EVDI" == "1" ]]; then
  unload_evdi
  remove_evdi_configs
  remove_libevdi_link
fi

if [[ "$DO_PACKAGES" == "1" ]]; then
  remove_evdi_dkms
  remove_evdi_packages
fi

if [[ "$DO_UDEV" == "1" ]]; then
  remove_udev
fi

if [[ "$DO_BLACKLIST" == "1" ]]; then
  remove_blacklist
fi

if [[ "$DO_PYTHON" == "1" ]]; then
  remove_python
fi

if [[ "$DO_BOOT_DISPLAY" == "1" ]]; then
  remove_boot_display
fi

if [[ "$DO_GUI" == "1" ]]; then
  remove_gui_desktop
fi

if [[ "$DO_CONFIG" == "1" ]]; then
  remove_config
fi

cat <<EOF

Uninstall complete.

Removed:
  - p13ctl virtualenv (unless --keep-python)
  - udev rules (unless --keep-udev)
$( [[ "$DO_EVDI" == "1" ]] && echo "  - EVDI module/config" )
$( [[ "$DO_PACKAGES" == "1" ]] && echo "  - displaylink/evdi-dkms packages" )
$( [[ "$DO_CONFIG" == "1" ]] && echo "  - ~/.config/p13ctl" )
$( [[ "$DO_BLACKLIST" == "1" ]] && echo "  - aic_usb_display blacklist" )
$( [[ "$DO_BOOT_DISPLAY" == "1" ]] && echo "  - p13-display.service / p13-panel-off.service (login display, logout panel off)" )
$( [[ "$DO_GUI" == "1" ]] && echo "  - p13ctl-gui desktop launcher" )

System packages (python3, libusb, dkms, etc.) were left installed.
Reboot if EVDI was loaded and you removed the module.

EOF
