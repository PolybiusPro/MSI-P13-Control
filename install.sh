#!/usr/bin/env bash
# Install p13ctl (MSI MPG CoreLiquid P13 360 Linux driver)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LINUX="$ROOT/tools/linux"
VENV="$ROOT/.venv"

DO_SYSTEM_DEPS=1
DO_EVDI=1
DO_UDEV=1
DO_PYTHON=1
DO_BLACKLIST=0
WITH_DESKTOP=1
DO_BOOT_DISPLAY=1
WITH_GUI=1

DISPLAYLINK_TAG="v6.3.0-1"
DISPLAYLINK_VER="1.15.0-1.github_evdi"

die() { echo "error: $*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: $0 [options]

Install p13ctl, EVDI extended-monitor support, udev rules, and user services.

Options:
  --evdi-only       Install only EVDI (kernel module, boot load, libevdi)
  --no-evdi         Skip EVDI (extended monitor mode will be unavailable)
  --no-system-deps  Skip apt/dnf/pacman packages
  --no-udev         Skip udev rules
  --no-python       Skip virtualenv and pip install
  --no-desktop      Skip desktop capture dependencies
  --no-boot-display Do not start p13ctl display desktop at login
  --no-gui            Skip GUI (PySide6) and desktop launcher
  --blacklist-aic   Blacklist aic_usb_display kernel driver
  -h, --help        Show this help

Examples:
  $0
  $0 --evdi-only
  $0 --blacklist-aic
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --evdi-only)
      DO_PYTHON=0
      DO_UDEV=0
      DO_SYSTEM_DEPS=0
      DO_EVDI=1
      shift
      ;;
    --no-evdi) DO_EVDI=0; shift ;;
    --no-system-deps) DO_SYSTEM_DEPS=0; shift ;;
    --no-udev) DO_UDEV=0; shift ;;
    --no-python) DO_PYTHON=0; shift ;;
    --no-desktop) WITH_DESKTOP=0; DO_BOOT_DISPLAY=0; shift ;;
    --no-boot-display) DO_BOOT_DISPLAY=0; shift ;;
    --no-gui) WITH_GUI=0; shift ;;
    --blacklist-aic) DO_BLACKLIST=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  die "this installer is for Linux only"
fi

run_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

install_system_deps() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "==> Installing system packages (apt)"
    run_root apt-get update -qq
    local packages=(
      python3-venv python3-pip libusb-1.0-0-dev libhidapi-hidraw0
      x11-xserver-utils python3-dbus
    )
    if [[ "$DO_EVDI" == "1" ]]; then
      packages+=(dkms libdrm-dev "linux-headers-$(uname -r)")
    fi
    run_root apt-get install -y "${packages[@]}"
    if [[ "$WITH_DESKTOP" == "1" ]]; then
      run_root apt-get install -y gnome-screenshot 2>/dev/null || true
    fi
    if [[ "$DO_EVDI" == "1" ]]; then
      run_root apt-get install -y evdi-dkms libevdi1 2>/dev/null || {
        echo "    warning: evdi-dkms not in apt — EVDI step will retry"
      }
    fi
  elif command -v dnf >/dev/null 2>&1; then
    echo "==> Installing system packages (dnf)"
    local packages=(python3 python3-pip libusb1-devel hidapi xrandr)
    if [[ "$DO_EVDI" == "1" ]]; then
      packages+=(dkms kernel-devel kernel-headers libdrm-devel mokutil curl)
    fi
    run_root dnf install -y --skip-unavailable "${packages[@]}"
    if [[ "$WITH_DESKTOP" == "1" ]]; then
      run_root dnf install -y --skip-unavailable python3-dbus 2>/dev/null || true
    fi
  elif command -v pacman >/dev/null 2>&1; then
    echo "==> Installing system packages (pacman)"
    run_root pacman -S --needed --noconfirm python python-pip libusb hidapi xorg-xrandr
    if [[ "$WITH_DESKTOP" == "1" ]]; then
      run_root pacman -S --needed --noconfirm python-dbus 2>/dev/null || true
    fi
  else
    echo "==> No supported package manager; skipping system packages."
    echo "    Ensure python3-venv, libusb, hidapi, and xrandr are installed."
  fi
}

install_evdi_src_dir() {
  ls -d /usr/src/evdi-* 2>/dev/null | head -1
}

evdi_source_version() {
  local src base
  src="$(install_evdi_src_dir)" || return 1
  base="$(basename "$src")"
  [[ "$base" == evdi-* ]] || return 1
  echo "${base#evdi-}"
}

evdi_module_built() {
  local kver="${1:-$(uname -r)}"
  [[ -n "$(find "/lib/modules/$kver" -name 'evdi.ko*' -print -quit 2>/dev/null)" ]]
}

evdi_dkms_status_for_kernel() {
  local version="${1:?}" kver="${2:?}"
  dkms status "evdi/$version" 2>/dev/null | grep -F "$kver" | grep -q ': installed'
}

evdi_installed_for_kernel() {
  local version="${1:?}"
  local kver="${2:-$(uname -r)}"
  evdi_dkms_status_for_kernel "$version" "$kver" && evdi_module_built "$kver"
}

# Like xone: register source with DKMS and build for the running kernel.
# evdi dkms.conf sets AUTOINSTALL=yes so dkms.service rebuilds on kernel updates.
ensure_evdi_dkms() {
  local src version kver
  src="$(install_evdi_src_dir)" || return 1
  version="$(evdi_source_version)" || return 1
  kver="$(uname -r)"

  if evdi_installed_for_kernel "$version" "$kver"; then
    return 0
  fi

  echo "==> Building EVDI $version for kernel $kver (dkms install)"
  if run_root dkms install "$src" -k "$kver" && evdi_module_built "$kver"; then
    return 0
  fi
  if run_root dkms install "$src" && evdi_module_built "$kver"; then
    return 0
  fi
  if run_root dkms autoinstall -m evdi && evdi_module_built "$kver"; then
    return 0
  fi
  return 1
}

evdi_is_loaded() {
  lsmod | awk '$1=="evdi"{found=1} END{exit !found}'
}

write_evdi_modprobe_conf() {
  local conf="$LINUX/evdi.conf" tmp
  tmp="$(mktemp)"
  cp "$conf" "$tmp"
  run_root cp "$tmp" /etc/modprobe.d/evdi-p13.conf
  rm -f "$tmp"
}

prepare_evdi_modprobe() {
  remove_conflicting_evdi_configs
  write_evdi_modprobe_conf
  run_root depmod -a "$(uname -r)" 2>/dev/null || true
}

disable_displaylink_service() {
  if systemctl list-unit-files displaylink-driver.service &>/dev/null; then
    run_root systemctl stop displaylink-driver.service 2>/dev/null || true
    run_root systemctl disable displaylink-driver.service 2>/dev/null || true
    echo "    Disabled displaylink-driver.service (only EVDI is needed)"
  fi
}

install_evdi_boot_config() {
  echo "==> Configuring EVDI boot load"
  run_root cp "$LINUX/evdi-modules-load.conf" /etc/modules-load.d/evdi-p13.conf
  run_root cp "$LINUX/evdi-p13.service" /etc/systemd/system/evdi-p13.service
  run_root systemctl daemon-reload
  run_root systemctl enable dkms.service 2>/dev/null || true
  run_root systemctl enable evdi-p13.service
}

remove_conflicting_evdi_configs() {
  run_root rm -f /etc/modprobe.d/evdi.conf /etc/modules-load.d/evdi.conf
}

load_evdi_module() {
  local kver modprobe_cmd ko
  kver="$(uname -r)"
  modprobe_cmd="$(command -v modprobe || echo /usr/sbin/modprobe)"

  if evdi_is_loaded; then
    return 0
  fi
  if ! evdi_module_built "$kver"; then
    echo "    evdi.ko missing for kernel $kver" >&2
    return 1
  fi

  if ! run_root "$modprobe_cmd" evdi initial_device_count=1; then
    echo "    modprobe failed, trying insmod" >&2
    ko="$(find "/lib/modules/$kver" -name 'evdi.ko*' -print -quit)"
    if [[ -z "$ko" ]]; then
      return 1
    fi
    run_root "$modprobe_cmd" drm_ttm_helper 2>/dev/null || true
    if ! run_root insmod "$ko" initial_device_count=1; then
      echo "    insmod failed" >&2
      return 1
    fi
  fi

  if evdi_is_loaded; then
    return 0
  fi

  echo "    evdi not loaded; recent kernel messages:" >&2
  run_root dmesg 2>/dev/null | tail -10 >&2 || true
  return 1
}

install_evdi_fedora_rpm() {
  local fedora_ver rpm_url rpm_path evdi_ver
  fedora_ver="$(rpm -E %fedora)"
  rpm_url="https://github.com/displaylink-rpm/displaylink-rpm/releases/download/${DISPLAYLINK_TAG}/fedora-${fedora_ver}-displaylink-${DISPLAYLINK_VER}.x86_64.rpm"
  rpm_path="/tmp/p13ctl-displaylink-${fedora_ver}.rpm"

  echo "==> Installing EVDI build dependencies (dnf)"
  run_root dnf install -y dkms kernel-devel kernel-headers libdrm-devel mokutil curl

  echo "==> Downloading displaylink RPM (EVDI) for Fedora ${fedora_ver}"
  echo "    $rpm_url"
  curl -fsSL -o "$rpm_path" "$rpm_url"
  run_root dnf install -y "$rpm_path"
}

install_evdi_packages() {
  if command -v dnf >/dev/null 2>&1; then
    install_evdi_fedora_rpm
  elif command -v apt-get >/dev/null 2>&1; then
    echo "==> Installing EVDI packages (apt)"
    run_root apt-get install -y evdi-dkms libevdi1 || {
      die "evdi-dkms not found — try: sudo apt install evdi-dkms libevdi1"
    }
  else
    die "unsupported distro for EVDI — install evdi-dkms + libevdi manually"
  fi
}

link_libevdi() {
  if [[ -f /usr/libexec/displaylink/libevdi.so ]] && [[ ! -e /usr/lib64/libevdi.so.1 ]]; then
    echo "==> Linking libevdi for p13ctl"
    run_root ln -sf /usr/libexec/displaylink/libevdi.so /usr/lib64/libevdi.so.1
    run_root ldconfig 2>/dev/null || true
  fi
}

evdi_secure_boot_pending() {
  if ! mokutil --sb-state 2>/dev/null | grep -qi enabled; then
    return 1
  fi
  local version
  version="$(evdi_source_version)" || return 1
  ! evdi_installed_for_kernel "$version"
}

install_evdi() {
  echo "==> Installing EVDI (virtual monitor for display desktop)"
  install_evdi_packages

  if ! ensure_evdi_dkms; then
    if evdi_secure_boot_pending; then
      cat <<'SB'

==> Secure Boot is enabled and EVDI could not be built for this kernel.

Enroll the DKMS signing key, then reboot:

  sudo mokutil --import /var/lib/dkms/mok.pub

In the blue MOK Manager screen: Enroll MOK → Continue → Yes → enter password → Reboot.

After reboot, dkms.service builds EVDI and evdi-p13.service loads it.

SB
      install_evdi_boot_config
      return 0
    fi
    die "DKMS build for evdi failed — check: dkms status; journalctl -u dkms -b"
  fi

  echo "==> Loading EVDI module"
  prepare_evdi_modprobe
  if load_evdi_module; then
    echo "    EVDI loaded ($(lsmod | awk '/^evdi /{print $1, $3}'))"
  else
    die "modprobe evdi failed — try: sudo modprobe evdi initial_device_count=1"
  fi

  disable_displaylink_service
  install_evdi_boot_config
  link_libevdi
  echo "    EVDI rebuilds through DKMS and loads before the display manager"
}

install_udev() {
  echo "==> Installing udev rules"
  run_root cp "$LINUX/99-msi-p13.rules" /etc/udev/rules.d/
  run_root udevadm control --reload-rules
  run_root udevadm trigger
  echo "    Added /etc/udev/rules.d/99-msi-p13.rules"
}

install_boot_display() {
  echo "==> Enabling P13 display at login"
  local unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  local p13ctl_bin="$VENV/bin/p13ctl"
  if [[ ! -x "$p13ctl_bin" ]]; then
    echo "    warning: $p13ctl_bin not found — skipping boot display service"
    return 0
  fi
  mkdir -p "$unit_dir"
  sed -e "s|@ROOT@|$ROOT|g" -e "s|@P13CTL_BIN@|$p13ctl_bin|g" \
    "$LINUX/p13-display.service.in" > "$unit_dir/p13-display.service"
  sed -e "s|@ROOT@|$ROOT|g" -e "s|@P13CTL_BIN@|$p13ctl_bin|g" \
    "$LINUX/p13-panel-off.service.in" > "$unit_dir/p13-panel-off.service"
  if systemctl --user daemon-reload 2>/dev/null; then
    systemctl --user enable p13-display.service p13-panel-off.service
    echo "    Enabled p13-display.service (starts at graphical login)"
    echo "    Enabled p13-panel-off.service (panel off on display sleep, logout, shutdown)"
    if systemctl --user is-active --quiet graphical-session.target 2>/dev/null; then
      systemctl --user start p13-panel-off.service 2>/dev/null || true
      systemctl --user start p13-display.service 2>/dev/null || true
    fi
  else
    echo "    Wrote $unit_dir/p13-display.service and p13-panel-off.service"
    echo "    Run after login: systemctl --user daemon-reload && systemctl --user enable --now p13-display.service p13-panel-off.service"
  fi
}

install_gui_desktop() {
  echo "==> Installing GUI launcher"
  local apps_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  local gui_bin="$VENV/bin/p13ctl-gui"
  if [[ ! -x "$gui_bin" ]]; then
    echo "    warning: $gui_bin not found — skipping GUI launcher"
    return 0
  fi
  mkdir -p "$apps_dir"
  sed -e "s|@P13CTL_GUI_BIN@|$gui_bin|g" \
    "$LINUX/p13ctl-gui.desktop.in" > "$apps_dir/p13ctl-gui.desktop"
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$apps_dir" 2>/dev/null || true
  fi
  echo "    Added $apps_dir/p13ctl-gui.desktop"
}

blacklist_aic_driver() {
  local conf="/etc/modprobe.d/blacklist-aic-usb-display.conf"
  echo "==> Blacklisting Artinchip kernel driver"
  echo 'blacklist aic_usb_display' | run_root tee "$conf" >/dev/null
  run_root modprobe -r aic_usb_display 2>/dev/null || true
  echo "    Wrote $conf"
}

install_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    die "python3 not found — install python3 or re-run without --no-system-deps"
  fi

  echo "==> Creating virtualenv at $VENV"
  python3 -m venv "$VENV"
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"

  echo "==> Installing p13ctl"
  local extras_parts=()
  [[ "$WITH_DESKTOP" == "1" ]] && extras_parts+=("desktop")
  [[ "$WITH_GUI" == "1" ]] && extras_parts+=("gui")
  local extras=""
  if ((${#extras_parts[@]} > 0)); then
    extras="[$(IFS=,; echo "${extras_parts[*]}")]"
  fi
  pip install --upgrade pip
  pip install -e "$ROOT$extras"
}

# --- install ---

if [[ "$DO_SYSTEM_DEPS" == "1" ]]; then
  install_system_deps
fi

if [[ "$DO_EVDI" == "1" ]]; then
  install_evdi
fi

if [[ "$DO_PYTHON" == "1" ]]; then
  install_python
fi

if [[ "$DO_PYTHON" == "1" && "$DO_BOOT_DISPLAY" == "1" && "$WITH_DESKTOP" == "1" ]]; then
  install_boot_display
fi

if [[ "$DO_PYTHON" == "1" && "$WITH_GUI" == "1" ]]; then
  install_gui_desktop
fi

if [[ "$DO_UDEV" == "1" ]]; then
  install_udev
fi

if [[ "$DO_BLACKLIST" == "1" ]]; then
  blacklist_aic_driver
fi

# --- done ---

if [[ "$DO_PYTHON" == "0" && "$DO_EVDI" == "1" ]]; then
  cat <<'EOF'

EVDI installed.

Try (after full install):
  p13ctl display desktop

EOF
  exit 0
fi

cat <<EOF

Done.

Activate the environment:
  source "$VENV/bin/activate"

Try:
  p13ctl list
  p13ctl hid info
  p13ctl display test
  p13ctl display desktop
  p13ctl-gui                      # graphical control panel
  p13ctl display desktop --capture
  p13ctl sysmon

Login: p13-display.service applies the saved Display Mode in the user session.
Extended monitor mode uses EVDI; add --capture to mirror an existing display instead.
Logout/restart: p13-panel-off.service sends brightness 0 before the session exits.

Re-plug the P13 USB cable after udev rule install.

Options:
  $0 --evdi-only
  $0 --no-evdi
  $0 --blacklist-aic
  $ROOT/uninstall.sh

EOF
