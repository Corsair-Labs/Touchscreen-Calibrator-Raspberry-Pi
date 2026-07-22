# Touchscreen Calibrator for Raspberry Pi

Raspberry Pi OS utility for calibrating a USB touchscreen and mapping it to the
correct display. The intended and tested use is with a CORSAIR XENEON EDGE
connected to a Raspberry Pi. The application reads raw touch samples, solves an
affine calibration matrix, and saves persistent libinput and Labwc
configuration.

The application is self-contained in the repository root.

## What the App Does

Touchscreen Calibrator opens a full-screen Pygame interface on each detected
display so the user can identify the screen connected to the touchscreen. It
automatically prefers the direct-touch `evdev` interface for the configured
device, collects five calibration points, and uses NumPy to calculate an affine
matrix.

After calibration, the app saves a device-specific libinput udev rule and maps
the touchscreen to the selected output in Labwc. It can account for rotated or
flipped displays and, when explicitly enabled, reload udev, apply an X11 matrix
immediately, or re-enumerate the touchscreen USB device.

## Use Case Scenario

A Raspberry Pi may be connected to a CORSAIR XENEON EDGE as a control panel,
dashboard, media console, or dedicated information screen. If touch coordinates
are offset, inverted, rotated, or mapped across the entire desktop, this utility
associates the XENEON EDGE touch input with the intended display and computes
the correction from points touched by the user.

The utility is especially useful for multi-display Raspberry Pi installations
using Labwc, where the XENEON EDGE touchscreen must be mapped to one output
rather than the combined desktop. Other USB touchscreens may work, but they
have not been tested and may require changes to the device name, event-device
fallback, output name, or transform settings.

## Disclaimer and License

This is experimental software, not a supported CORSAIR product. Review the
[DISCLAIMER NOTICE](DISCLAIMER%20NOTICE) and [LICENSE](LICENSE) before using,
modifying, or redistributing it. The project uses the standard MIT License,
which permits both commercial and non-commercial use, modification,
distribution, sublicensing, and sale as long as its copyright and permission
notices are retained. The license applies to the software, not to CORSAIR
trademarks or any claim of endorsement. The software is provided as-is, without
warranty or support.

## Prerequisites

Use Raspberry Pi OS with a graphical desktop session and a connected CORSAIR
XENEON EDGE. The launcher installs these system packages when `apt-get` is
available:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip python3-dev build-essential wlr-randr xinput
```

The Python dependencies are pinned in `requirements.txt` and installed into a
local `.venv` by the launcher:

- `evdev` reads the Linux input device.
- `numpy` solves the affine calibration matrix.
- `pygame` provides the display-selection and calibration interface.

## Quick Start

From the project directory, run the launcher as the desktop user:

```bash
chmod +x run-all.sh
./run-all.sh
```

Do not normally start the launcher itself with `sudo`. It requests elevated
access when installing packages and writing the calibration rule while retaining
the desktop user's display session.

The launcher performs the following steps:

1. Offers to install or update Raspberry Pi OS system dependencies.
2. Creates or refreshes the local `.venv` virtual environment.
3. Installs the packages in `requirements.txt`.
4. Caches sudo credentials for the later calibration-rule write.
5. Launches `touchscreen_calibrator_raspberry_pi.py`.

Available launcher options:

```text
./run-all.sh --yes          Accept setup prompts automatically.
./run-all.sh --skip-system  Skip apt dependency installation.
./run-all.sh --root-run     Run the calibrator with sudo -E.
./run-all.sh --help         Show launcher help.
```

Use `--root-run` only if the desktop user cannot read the selected
`/dev/input/event*` device. The launcher preserves the supported display and
calibrator environment variables when it starts the app through `sudo -E`.

## During Calibration

1. On the display connected to the touchscreen, tap the screen or press Space
   (or `S`) to select it.
2. Press Enter to move the selection prompt to the next display when needed.
3. Touch the center of each of the five red crosses, lifting your finger after
   every touch.
4. Review the completion screen, then press Esc to exit.

Press Esc at any earlier point to cancel. The app does not write a calibration
rule until all five points have been collected and the result has been solved.

## Default Hardware Settings

The principal defaults are defined near the top of
`touchscreen_calibrator_raspberry_pi.py` and reflect the tested CORSAIR XENEON EDGE
configuration:

| Setting | Default |
| --- | --- |
| Touch device path fallback | `/dev/input/event11` |
| Touch device name | `wch.cn TouchScreen` |
| Calibration rule | `/etc/udev/rules.d/99-touchscreen-calibration.rules` |
| Preferred display output | `HDMI-A-2` |
| Desktop layout | `horizontal` |
| Output transform | `auto` |

The app searches the Linux input-device records for the configured device name
and prefers its direct-touch interface, so the event number may differ from the
fallback. Edit `EVENT_DEV` and `DEVICE_NAME` in the Python script if the device
uses a different name; use `TOUCHSCREEN_OUTPUT_NAME` to override the preferred
display without editing the source.

## Configuration

Environment variables provide the runtime configuration:

| Variable | Default | Purpose |
| --- | --- | --- |
| `TOUCHSCREEN_OUTPUT_NAME` | `HDMI-A-2` | Preferred display output. |
| `TOUCHSCREEN_DESKTOP_LAYOUT` | `horizontal` | Display arrangement used when output positions cannot be queried. |
| `TOUCHSCREEN_MATRIX_MODE` | `auto` | Chooses local-output or whole-desktop coordinates for the persistent matrix. |
| `TOUCHSCREEN_OUTPUT_TRANSFORM` | `auto` | Uses an explicit transform or detects one from Kanshi configuration. |
| `TOUCHSCREEN_OUTPUT_TRANSFORM_CORRECTION` | `inverse` | Controls correction for rotated or flipped output coordinates. |
| `TOUCHSCREEN_DUPLICATE_IGNORE` | `never` | Controls udev ignore rules for mouse-like interfaces from the same touch device. |
| `TOUCHSCREEN_LABWC_CALIBRATION_MATRIX` | `0` | Also stores the calibration matrix in Labwc when enabled. |
| `TOUCHSCREEN_LABWC_RC` | user Labwc config | Overrides the Labwc `rc.xml` path. |
| `TOUCHSCREEN_KANSHI_CONFIG` | auto-detected | Overrides the Kanshi configuration used for transform detection. |
| `TOUCHSCREEN_RELOAD_UDEV` | `0` | Reloads and triggers udev immediately when enabled. |
| `TOUCHSCREEN_LIVE_XINPUT` | `0` | Applies the result to matching X11 devices immediately when enabled. |
| `TOUCHSCREEN_AUTO_USB_REENUMERATE` | `0` | Disconnects and reconnects the touchscreen in software when enabled. |
| `TOUCHSCREEN_USB_REENUMERATE_DELAY` | `1.0` | Delay between USB disable and enable operations. |
| `TOUCHSCREEN_USB_REENUMERATE_TIMEOUT` | `8.0` | Time to wait for the input device to return. |
| `TOUCHSCREEN_TARGET_MARGIN` | `0.08` | Top, left, and right target inset as a fraction of display size. |
| `TOUCHSCREEN_BOTTOM_DRAW_TARGET_MARGIN` | target margin | Bottom target drawing inset. |

Boolean settings accept values such as `1`, `true`, `yes`, or `on` to enable
them. Transform values include `normal`, `90`, `180`, `270`, and the supported
flipped variants.

Example for a display rotated 270 degrees:

```bash
TOUCHSCREEN_OUTPUT_NAME=HDMI-A-2 \
TOUCHSCREEN_OUTPUT_TRANSFORM=270 \
./run-all.sh --skip-system
```

## Generated and Updated Files

The app may create or update:

- `/etc/udev/rules.d/99-touchscreen-calibration.rules`, containing the
  device-specific `LIBINPUT_CALIBRATION_MATRIX` and output mapping.
- `~/.config/labwc/rc.xml`, containing the touchscreen `mapToOutput` setting.

Existing udev entries for other touchscreens are retained. Rules that conflict
with the newly calibrated device are commented out and the new rule is appended
with a timestamp. Existing Labwc configuration is parsed and rewritten when the
touch mapping is saved, so keep a backup of custom configuration before first
use.

By default, the saved udev rule applies after the input device is re-added or the
system reboots. The app attempts to reload Labwc after updating its mapping.

## Main Components

```text
project-directory/
  touchscreen_calibrator_raspberry_pi.py  Calibration UI, matrix solver, and persistence.
  run-all.sh                          Raspberry Pi setup and launcher script.
  requirements.txt                   Pinned Python dependencies.
  DISCLAIMER NOTICE                  Supplemental project and trademark notice.
  LICENSE                            MIT License terms.
  README.md                          Setup, usage, and troubleshooting guide.
```

## Troubleshooting

- **The input device cannot be opened:** Run `ls -l /dev/input/event*` and
  confirm the desktop user can read the device. If necessary, retry with
  `./run-all.sh --root-run`.
- **The wrong touchscreen interface is selected:** Compare the candidates
  printed at startup. Update `DEVICE_NAME` or the `EVENT_DEV` fallback in the
  Python script if the hardware reports a different identity.
- **The wrong display opens first:** Set `TOUCHSCREEN_OUTPUT_NAME` to the output
  shown by `wlr-randr`. You can also press Enter to cycle through displays in
  the setup interface.
- **Touch is rotated or inverted after reboot:** Check the output's transform in
  `wlr-randr` or Kanshi and set `TOUCHSCREEN_OUTPUT_TRANSFORM` explicitly.
- **Calibration saves but does not take effect immediately:** Reboot or re-add
  the input device. Optional immediate methods are
  `TOUCHSCREEN_RELOAD_UDEV=1`, `TOUCHSCREEN_LIVE_XINPUT=1`, or
  `TOUCHSCREEN_AUTO_USB_REENUMERATE=1`; use the USB option cautiously because it
  temporarily disconnects the device.
- **Saving reports a permissions error:** Run `sudo -v` before launching, or use
  `./run-all.sh --root-run` while preserving the graphical-session environment.
- **Labwc mapping does not apply:** Confirm `~/.config/labwc/rc.xml` is writable,
  the output name is correct, and `labwc --reconfigure` succeeds from the active
  desktop session.
