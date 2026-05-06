# Touchscreen Calibrator 15 Release

Minimal release package for `raspi_touchscreen_calibrator_15.py`.

This calibrator opens a full-screen Pygame UI, reads raw touchscreen samples through
`evdev`, solves an affine calibration matrix with `numpy`, and saves the result as
a libinput udev calibration rule.

## Files

- `run-all.sh` - installs dependencies, creates/updates `.venv`, and launches the app.
- `raspi_touchscreen_calibrator_15.py` - current calibrator script.
- `requirements.txt` - Python packages needed by the script.
- `README.md` - this release guide.

## Quick Start

From this release folder:

```bash
chmod +x run-all.sh
./run-all.sh
```

The script walks you through:

1. Installing Raspberry Pi OS system dependencies with `apt-get`.
2. Creating or updating the local `.venv` virtual environment.
3. Installing Python packages from `requirements.txt`.
4. Requesting sudo credentials for calibration rule writes.
5. Running `raspi_touchscreen_calibrator_15.py`.

To accept prompts automatically:

```bash
./run-all.sh --yes
```

If system packages are already installed:

```bash
./run-all.sh --skip-system
```

If your desktop user cannot read `/dev/input/event*`, run the calibrator through
sudo while preserving display session variables:

```bash
./run-all.sh --root-run
```

## Default Hardware Settings

The script defaults are configured near the top of
`raspi_touchscreen_calibrator_15.py`:

- Touch device path fallback: `/dev/input/event11`
- Touch device name: `wch.cn TouchScreen`
- Calibration rule output: `/etc/udev/rules.d/99-touchscreen-calibration.rules`
- Preferred display output: `HDMI-A-2`

Edit those constants if your touchscreen device or output name is different.

## During Calibration

- Tap the screen that belongs to the touchscreen, or press Space to select it.
- Press Enter to move the setup prompt to the next display.
- Press Esc to cancel.
- Touch the center of each red cross, then lift your finger.

## Useful Environment Variables

- `TOUCHSCREEN_OUTPUT_NAME=HDMI-A-2`
- `TOUCHSCREEN_DESKTOP_LAYOUT=horizontal`
- `TOUCHSCREEN_OUTPUT_TRANSFORM=auto`
- `TOUCHSCREEN_RELOAD_UDEV=0`
- `TOUCHSCREEN_LIVE_XINPUT=0`
- `TOUCHSCREEN_AUTO_USB_REENUMERATE=0`

Example:

```bash
TOUCHSCREEN_OUTPUT_NAME=HDMI-A-2 TOUCHSCREEN_OUTPUT_TRANSFORM=270 ./run-all.sh --skip-system
```

## Generated Files

The app may update:

- `/etc/udev/rules.d/99-touchscreen-calibration.rules`
- `~/.config/labwc/rc.xml`

By default, the saved udev rule usually applies after rebooting or after the input
device is re-added. Set `TOUCHSCREEN_RELOAD_UDEV=1` if you want the app to try
reloading udev immediately after saving.
