#!/usr/bin/env bash
set -euo pipefail

APP_SCRIPT="touchscreen_calibrator_raspberry_pi.py"
VENV_DIR=".venv"
SYSTEM_PACKAGES=(
  python3
  python3-venv
  python3-pip
  python3-dev
  build-essential
  wlr-randr
  xinput
)

YES=0
SKIP_SYSTEM=0
RUN_AS_ROOT=0

usage() {
  cat <<USAGE
Usage: ./run-all.sh [options]

Options:
  -y, --yes          Accept prompts and install/update dependencies.
  --skip-system      Skip apt dependency installation.
  --root-run         Run the calibrator with sudo -E after setup.
  -h, --help         Show this help.
USAGE
}

for arg in "$@"; do
  case "$arg" in
    -y|--yes)
      YES=1
      ;;
    --skip-system)
      SKIP_SYSTEM=1
      ;;
    --root-run)
      RUN_AS_ROOT=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      usage
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ask_yes_no() {
  local prompt="$1"
  local default="${2:-Y}"
  local reply

  if [[ "$YES" -eq 1 ]]; then
    return 0
  fi

  if [[ "$default" == "Y" ]]; then
    read -r -p "$prompt [Y/n] " reply
    [[ -z "$reply" || "$reply" =~ ^[Yy]$ ]]
  else
    read -r -p "$prompt [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]]
  fi
}

need_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1
}

echo
echo "Touchscreen Calibrator 15 setup"
echo "Project: $SCRIPT_DIR"
echo

if [[ "$EUID" -eq 0 ]]; then
  echo "This script should usually be run as your desktop user, not with sudo."
  echo "It will ask for sudo only when system packages or calibration permissions need it."
  echo
fi

if [[ ! -f "$APP_SCRIPT" ]]; then
  echo "Missing $APP_SCRIPT in $SCRIPT_DIR" >&2
  exit 1
fi

if [[ ! -f "requirements.txt" ]]; then
  echo "Missing requirements.txt in $SCRIPT_DIR" >&2
  exit 1
fi

if [[ "$SKIP_SYSTEM" -eq 0 ]]; then
  if need_command apt-get; then
    if ask_yes_no "Install/update Raspberry Pi OS system dependencies now?" "Y"; then
      sudo apt-get update
      sudo apt-get install -y "${SYSTEM_PACKAGES[@]}"
    else
      echo "Skipping system package installation."
    fi
  else
    echo "apt-get was not found; skipping system package installation."
  fi
else
  echo "Skipping system package installation."
fi

if ! need_command python3; then
  echo "python3 was not found. Install Python 3 and rerun this script." >&2
  exit 1
fi

echo
echo "Creating/updating virtual environment: $VENV_DIR"
python3 -m venv "$VENV_DIR"

PYTHON_BIN="$SCRIPT_DIR/$VENV_DIR/bin/python"
PIP_BIN="$SCRIPT_DIR/$VENV_DIR/bin/pip"

echo "Installing Python dependencies from requirements.txt"
"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel
"$PIP_BIN" install -r requirements.txt

echo
echo "Setup complete."
echo "The calibrator writes a udev rule after calibration, so sudo credentials may be requested."
sudo -v || true

if [[ "$RUN_AS_ROOT" -eq 0 ]]; then
  if ask_yes_no "Run the calibrator with sudo -E for direct /dev/input access?" "N"; then
    RUN_AS_ROOT=1
  fi
fi

echo
echo "Launching $APP_SCRIPT"

if [[ "$RUN_AS_ROOT" -eq 1 ]]; then
  SUDO_ENV=()
  for name in \
    XDG_RUNTIME_DIR \
    WAYLAND_DISPLAY \
    DISPLAY \
    XAUTHORITY \
    TOUCHSCREEN_OUTPUT_NAME \
    TOUCHSCREEN_DESKTOP_LAYOUT \
    TOUCHSCREEN_OUTPUT_TRANSFORM \
    TOUCHSCREEN_RELOAD_UDEV \
    TOUCHSCREEN_LIVE_XINPUT \
    TOUCHSCREEN_AUTO_USB_REENUMERATE
  do
    if [[ -n "${!name-}" ]]; then
      SUDO_ENV+=("$name=${!name}")
    fi
  done

  sudo -E env "${SUDO_ENV[@]}" "$PYTHON_BIN" "$SCRIPT_DIR/$APP_SCRIPT"
else
  "$PYTHON_BIN" "$SCRIPT_DIR/$APP_SCRIPT"
fi
