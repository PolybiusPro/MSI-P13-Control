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
WITH_SYSMON=1
WITH_DESKTOP=1

DISPLAYLINK_TAG="v6.3.0-1"
DISPLAYLINK_VER="1.15.0-1.github_evdi"

die() { echo "error: $*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: $0 [options]

Install p13ctl, system dependencies, udev rules, and EVDI (virtual monitor).

Options:
  --evdi-only       Install only EVDI (kernel module, boot load, libevdi)
  --no-evdi         Skip EVDI
  --no-system-deps  Skip apt/dnf/pacman packages
  --no-udev         Skip udev rules
  --no-python       Skip virtualenv and pip install
  --no-desktop      Skip desktop capture dependencies
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
    --no-desktop) WITH_DESKTOP=0; shift ;;
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

install_evdi_configs() {
  echo "==> Installing EVDI boot/modprobe config"
  run_root cp "$LINUX/evdi.conf" /etc/modprobe.d/evdi-p13.conf
  run_root cp "$LINUX/evdi-modules-load.conf" /etc/modules-load.d/evdi-p13.conf
  if systemctl list-unit-files dkms-autoinstall.service &>/dev/null; then
    echo "==> Enabling DKMS autoinstall on boot"
    run_root systemctl enable dkms-autoinstall.service 2>/dev/null || true
  fi
  if systemctl list-unit-files dkms.service &>/dev/null; then
    run_root systemctl enable dkms.service 2>/dev/null || true
  fi
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

  echo "==> Building EVDI kernel module (DKMS)"
  run_root dkms autoinstall -m evdi || {
    evdi_ver="$(ls /usr/src 2>/dev/null | grep '^evdi-' | head -1 | sed 's/^evdi-//')"
    [[ -n "$evdi_ver" ]] && run_root dkms install "evdi/$evdi_ver" || true
  }

  if systemctl list-unit-files displaylink-driver.service &>/dev/null; then
    run_root systemctl disable --now displaylink-driver.service 2>/dev/null || true
    echo "    Disabled displaylink-driver.service (only EVDI is needed)"
  fi
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
  mokutil --sb-state 2>/dev/null | grep -qi enabled \
    && ! dkms status 2>/dev/null | grep -E '^evdi,' | grep -q installed
}

install_evdi() {
  echo "==> Installing EVDI (virtual monitor for display desktop)"
  install_evdi_configs
  install_evdi_packages

  if evdi_secure_boot_pending; then
    cat <<'SB'

==> Secure Boot is enabled and EVDI is not built yet.

Enroll the DKMS signing key, then reboot:

  sudo mokutil --import /var/lib/dkms/mok.pub

In the blue MOK Manager screen: Enroll MOK → Continue → Yes → enter password → Reboot.

After reboot, DKMS builds evdi and it loads automatically via /etc/modules-load.d/evdi-p13.conf

SB
    return 0
  fi

  echo "==> Loading EVDI module"
  if run_root modprobe evdi initial_device_count=1; then
    echo "    EVDI loaded ($(lsmod | awk '/^evdi /{print $1, $3}'))"
  else
    die "modprobe evdi failed — check: dkms status; journalctl -k | tail"
  fi

  link_libevdi
  echo "    EVDI loads automatically on boot"
}

install_udev() {
  echo "==> Installing udev rules"
  run_root cp "$LINUX/99-msi-p13.rules" /etc/udev/rules.d/
  run_root udevadm control --reload-rules
  run_root udevadm trigger
  echo "    Added /etc/udev/rules.d/99-msi-p13.rules"
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
  [[ "$WITH_SYSMON" == "1" ]] && extras_parts+=("sysmon")
  [[ "$WITH_DESKTOP" == "1" ]] && extras_parts+=("desktop")
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
  p13ctl display desktop --capture
  p13ctl sysmon

Re-plug the P13 USB cable after udev rule install.

Options:
  $0 --evdi-only
  $0 --no-evdi
  $0 --blacklist-aic
  $ROOT/uninstall.sh

EOF
