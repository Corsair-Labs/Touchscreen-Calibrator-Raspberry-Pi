#!/usr/bin/env python3
import os
import re
import sys
import time
import threading
import subprocess
import shutil
import pwd
import xml.etree.ElementTree as ET
from typing import Any, TypeAlias
import numpy as np

if "SDL_VIDEODRIVER" not in os.environ and os.environ.get("DISPLAY"):
    os.environ["SDL_VIDEODRIVER"] = "x11"

import pygame
from evdev import InputDevice, ecodes

EVENT_DEV = "/dev/input/event11"
DEVICE_NAME = "wch.cn TouchScreen"
RULES_FILE = "/etc/udev/rules.d/99-touchscreen-calibration.rules"
INPUT_DEVICES_FILE = "/proc/bus/input/devices"
TargetPoint: TypeAlias = tuple[float, float]

DESKTOP_LAYOUT = os.environ.get("TOUCHSCREEN_DESKTOP_LAYOUT", "horizontal").strip().lower()
MATRIX_MODE = os.environ.get("TOUCHSCREEN_MATRIX_MODE", "auto").strip().lower()
SETUP_TITLE = "Touchscreen setup"
LABWC_XML_NS = "http://openbox.org/3.4/rc"
PREFERRED_OUTPUT_NAME = os.environ.get("TOUCHSCREEN_OUTPUT_NAME", "HDMI-A-2").strip()
OUTPUT_TRANSFORM = os.environ.get("TOUCHSCREEN_OUTPUT_TRANSFORM", "auto").strip().lower()
OUTPUT_TRANSFORM_CORRECTION = os.environ.get(
    "TOUCHSCREEN_OUTPUT_TRANSFORM_CORRECTION",
    "inverse",
).strip().lower()
DUPLICATE_IGNORE_MODE = os.environ.get("TOUCHSCREEN_DUPLICATE_IGNORE", "never").strip().lower()
LABWC_WRITE_CALIBRATION_MATRIX = os.environ.get(
    "TOUCHSCREEN_LABWC_CALIBRATION_MATRIX",
    "0",
).strip().lower() not in ("0", "false", "no", "off")
RELOAD_UDEV_AFTER_SAVE = os.environ.get("TOUCHSCREEN_RELOAD_UDEV", "0").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
APPLY_LIVE_XINPUT = os.environ.get("TOUCHSCREEN_LIVE_XINPUT", "0").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
AUTO_USB_REENUMERATE = os.environ.get("TOUCHSCREEN_AUTO_USB_REENUMERATE", "0").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
USB_REENUMERATE_DELAY = float(os.environ.get("TOUCHSCREEN_USB_REENUMERATE_DELAY", "1.0"))
USB_REENUMERATE_TIMEOUT = float(os.environ.get("TOUCHSCREEN_USB_REENUMERATE_TIMEOUT", "8.0"))
TARGET_MARGIN = min(max(float(os.environ.get("TOUCHSCREEN_TARGET_MARGIN", "0.08")), 0.02), 0.20)
BOTTOM_DRAW_TARGET_MARGIN = min(
    max(float(os.environ.get("TOUCHSCREEN_BOTTOM_DRAW_TARGET_MARGIN", str(TARGET_MARGIN))), 0.05),
    0.35,
)
TARGET_ARM_LENGTH = 45
TARGET_RADIUS = 25
TARGET_LINE_WIDTH = 3
TARGET_EDGE_PADDING = 18

# Targets drawn relative to the selected touchscreen monitor.
DRAW_TARGETS: list[TargetPoint] = [
    (TARGET_MARGIN, TARGET_MARGIN),
    (1.0 - TARGET_MARGIN, TARGET_MARGIN),
    (1.0 - TARGET_MARGIN, 1.0 - BOTTOM_DRAW_TARGET_MARGIN),
    (TARGET_MARGIN, 1.0 - BOTTOM_DRAW_TARGET_MARGIN),
    (0.50, 0.50),
]

def udev_escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')

def event_name_from_path(devpath):
    return os.path.basename(devpath)

def parse_proc_bitmask(value):
    try:
        return int(value.replace(" ", ""), 16)
    except ValueError:
        return 0

def proc_bit_is_set(record, key, bit):
    return bool(record.get(key, 0) & (1 << bit))

def read_input_device_records():
    try:
        with open(INPUT_DEVICES_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return []

    records = []
    for block in content.split("\n\n"):
        record: dict[str, Any] = {"handlers": []}

        for line in block.splitlines():
            if line.startswith("I:"):
                for key, value in re.findall(r"(\w+)=([0-9A-Fa-f]+)", line):
                    record[key.lower()] = value
            elif line.startswith("N:"):
                match = re.search(r'Name="(.*)"', line)
                if match:
                    record["name"] = match.group(1)
            elif line.startswith("P:"):
                record["phys"] = line.partition("Phys=")[2].strip()
            elif line.startswith("S:"):
                record["sysfs"] = line.partition("Sysfs=")[2].strip()
            elif line.startswith("U:"):
                record["uniq"] = line.partition("Uniq=")[2].strip()
            elif line.startswith("H:"):
                record["handlers"] = line.partition("Handlers=")[2].split()
            elif line.startswith("B:"):
                key, _, value = line[3:].partition("=")
                record[key.strip().lower()] = parse_proc_bitmask(value.strip())

        if record.get("name") and record.get("handlers"):
            records.append(record)

    return records

def event_path_for_record(record):
    for handler in record.get("handlers", []):
        if handler.startswith("event"):
            return f"/dev/input/{handler}"

    return None

def record_for_event(devpath, records=None):
    event_name = event_name_from_path(devpath)
    records = records if records is not None else read_input_device_records()

    for record in records:
        if event_name in record.get("handlers", []):
            return record

    return None

def touch_record_score(record, preferred_event_name):
    score = 0
    direct_bit = getattr(ecodes, "INPUT_PROP_DIRECT", 1)

    if proc_bit_is_set(record, "prop", direct_bit):
        score += 100
    if (
        proc_bit_is_set(record, "abs", ecodes.ABS_MT_POSITION_X)
        and proc_bit_is_set(record, "abs", ecodes.ABS_MT_POSITION_Y)
    ):
        score += 60
    if not record.get("rel", 0):
        score += 20
    if preferred_event_name in record.get("handlers", []):
        score += 10

    return score

def select_touch_event_device(preferred_devpath, device_name):
    records = [
        record
        for record in read_input_device_records()
        if record.get("name") == device_name and event_path_for_record(record)
    ]

    if not records:
        return preferred_devpath

    preferred_event_name = event_name_from_path(preferred_devpath)
    selected = max(
        records,
        key=lambda record: touch_record_score(record, preferred_event_name),
    )
    selected_devpath = event_path_for_record(selected)

    if selected_devpath and selected_devpath != preferred_devpath:
        print(
            f"Using detected direct touchscreen device {selected_devpath} "
            f"instead of configured {preferred_devpath}."
        )

    return selected_devpath or preferred_devpath

def record_from_evdev(dev):
    record = {
        "name": dev.name,
        "phys": getattr(dev, "phys", "") or "",
        "uniq": getattr(dev, "uniq", "") or "",
        "handlers": [event_name_from_path(dev.path)],
    }

    info = getattr(dev, "info", None)
    if info is not None:
        record["bus"] = f"{info.bustype:04x}"
        record["vendor"] = f"{info.vendor:04x}"
        record["product"] = f"{info.product:04x}"
        record["version"] = f"{info.version:04x}"

    return record

def input_interface_base(phys):
    if not phys:
        return ""

    return re.sub(r"/input\d+$", "", phys)

def same_physical_touch_device(left, right):
    if left.get("name") != right.get("name"):
        return False

    left_base = input_interface_base(left.get("phys", ""))
    right_base = input_interface_base(right.get("phys", ""))
    if left_base and right_base and left_base == right_base:
        return True

    left_uniq = left.get("uniq", "")
    right_uniq = right.get("uniq", "")
    return bool(left_uniq and right_uniq and left_uniq == right_uniq)

def is_mouse_like_duplicate(record):
    direct_bit = getattr(ecodes, "INPUT_PROP_DIRECT", 1)
    return record.get("rel", 0) or not proc_bit_is_set(record, "prop", direct_bit)

def duplicate_records_for_target(target_record):
    target_event = event_path_for_record(target_record)
    duplicates = []

    for record in read_input_device_records():
        if not event_path_for_record(record):
            continue
        if event_path_for_record(record) == target_event:
            continue
        if not same_physical_touch_device(target_record, record):
            continue
        if is_mouse_like_duplicate(record):
            duplicates.append(record)

    return duplicates

def duplicate_ignore_records_for_transform(duplicate_records, transform):
    mode = DUPLICATE_IGNORE_MODE
    normalized = normalize_output_transform(transform)

    if mode in ("0", "false", "no", "off", "never", "none"):
        return [], f"duplicate-interface ignore disabled by TOUCHSCREEN_DUPLICATE_IGNORE={DUPLICATE_IGNORE_MODE!r}"

    if mode in ("1", "true", "yes", "on", "always"):
        return duplicate_records, "duplicate-interface ignore forced on"

    if mode not in ("", "auto"):
        print(f"Ignoring unknown TOUCHSCREEN_DUPLICATE_IGNORE={DUPLICATE_IGNORE_MODE!r}; using auto.")

    return duplicate_records, (
        "duplicate-interface ignore auto-enabled for output transform "
        f"{normalized!r}"
    )

def read_text_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""

def input_record_summary(record):
    event_path = event_path_for_record(record) or "unknown event"
    phys = record.get("phys") or "unknown phys"
    sysfs = record.get("sysfs") or "unknown sysfs"
    direct_bit = getattr(ecodes, "INPUT_PROP_DIRECT", 1)
    kind = "direct-touch" if proc_bit_is_set(record, "prop", direct_bit) else "mouse-like"
    if record.get("rel", 0):
        kind += ", relative"

    return f"{event_path} ({kind}, phys={phys}, sysfs={sysfs})"

def print_touchscreen_candidates(device_name):
    records = [
        record
        for record in read_input_device_records()
        if record.get("name") == device_name and event_path_for_record(record)
    ]

    if not records:
        print(f"No input candidates found for {device_name!r}.")
        return

    print(f"Input candidates for {device_name!r}:")
    preferred_event_name = event_name_from_path(EVENT_DEV)
    for record in sorted(
        records,
        key=lambda item: touch_record_score(item, preferred_event_name),
        reverse=True,
    ):
        print(f"  score={touch_record_score(record, preferred_event_name):3d} {input_record_summary(record)}")

def usb_device_from_record(record):
    sysfs = record.get("sysfs", "")
    if sysfs:
        current = os.path.realpath(os.path.join("/sys", sysfs.lstrip("/")))
        while current and current != "/":
            if os.path.exists(os.path.join(current, "idVendor")) and os.path.exists(
                os.path.join(current, "idProduct")
            ):
                return os.path.basename(current), current
            current = os.path.dirname(current)

    vendor = (record.get("vendor") or "").lower()
    product = (record.get("product") or "").lower()
    uniq = record.get("uniq") or ""

    usb_devices_dir = "/sys/bus/usb/devices"
    try:
        candidates = os.listdir(usb_devices_dir)
    except OSError:
        return None, None

    for candidate in candidates:
        candidate_path = os.path.join(usb_devices_dir, candidate)
        candidate_vendor = read_text_file(os.path.join(candidate_path, "idVendor")).lower()
        candidate_product = read_text_file(os.path.join(candidate_path, "idProduct")).lower()
        candidate_serial = read_text_file(os.path.join(candidate_path, "serial"))

        if candidate_vendor != vendor or candidate_product != product:
            continue
        if uniq and candidate_serial and candidate_serial != uniq:
            continue

        return candidate, candidate_path

    return None, None

def usb_device_description(usb_name, usb_path):
    if not usb_name or not usb_path:
        return "unknown USB device"

    vendor = read_text_file(os.path.join(usb_path, "idVendor")) or "unknown"
    product = read_text_file(os.path.join(usb_path, "idProduct")) or "unknown"
    product_name = read_text_file(os.path.join(usb_path, "product")) or "unknown product"
    serial = read_text_file(os.path.join(usb_path, "serial")) or "unknown serial"
    return f"{usb_name} ({vendor}:{product}, {product_name}, serial={serial})"

def wait_for_touchscreen_reenumeration(device_name, previous_event=None, timeout=USB_REENUMERATE_TIMEOUT):
    deadline = time.monotonic() + timeout
    selected_record = None

    while time.monotonic() < deadline:
        selected_devpath = select_touch_event_device(EVENT_DEV, device_name)
        selected_record = record_for_event(selected_devpath)

        if selected_record is not None:
            selected_event = event_path_for_record(selected_record)
            if selected_event and (previous_event is None or selected_event != previous_event):
                return selected_record
            if selected_event and previous_event == selected_event:
                return selected_record

        time.sleep(0.25)

    return selected_record

def reenumerate_usb_device_for_record(record):
    if not AUTO_USB_REENUMERATE:
        return False, "USB re-enumeration disabled by TOUCHSCREEN_AUTO_USB_REENUMERATE.", None, []

    usb_name, usb_path = usb_device_from_record(record)
    if not usb_name or not usb_path:
        return False, "Could not find the parent USB device for touchscreen re-enumeration.", None, []

    authorized_path = os.path.join(usb_path, "authorized")
    if not os.path.exists(authorized_path):
        return False, f"USB device {usb_name} does not expose an authorized control.", None, []

    previous_event = event_path_for_record(record)
    print("\nUSB re-enumeration:")
    print(f"  target USB: {usb_device_description(usb_name, usb_path)}")
    print(f"  previous direct input: {input_record_summary(record)}")
    print(f"  authorized control: {authorized_path}")
    print("  disconnecting touchscreen USB in software...")
    run_privileged(["tee", authorized_path], input_text="0\n", quiet_stdout=True)
    time.sleep(max(0.1, USB_REENUMERATE_DELAY))
    print("  reconnecting touchscreen USB in software...")
    run_privileged(["tee", authorized_path], input_text="1\n", quiet_stdout=True)

    selected_record = wait_for_touchscreen_reenumeration(
        DEVICE_NAME,
        previous_event,
        USB_REENUMERATE_TIMEOUT,
    )
    if selected_record is None:
        return False, "USB re-enumeration completed, but the touchscreen input did not reappear.", None, []

    duplicate_records = duplicate_records_for_target(selected_record)
    duplicate_summary = ", ".join(input_record_summary(item) for item in duplicate_records) or "none"
    selected_summary = input_record_summary(selected_record)
    print(f"  new direct input: {selected_summary}")
    print(f"  duplicate interfaces after re-enumeration: {duplicate_summary}")

    return True, f"USB re-enumerated: {selected_summary}", selected_record, duplicate_records

def make_udev_conditions(record):
    conditions = [
        'ACTION=="add|change"',
        'SUBSYSTEM=="input"',
        'KERNEL=="event[0-9]*"',
        f'ATTRS{{name}}=="{udev_escape(record.get("name", DEVICE_NAME))}"',
    ]

    if record.get("phys"):
        conditions.append(f'ATTRS{{phys}}=="{udev_escape(record["phys"])}"')
    elif record.get("uniq"):
        conditions.append(f'ATTRS{{uniq}}=="{udev_escape(record["uniq"])}"')

    return conditions

def make_calibration_rule(matrix, output_name=None, target_record=None):
    record = target_record or {"name": DEVICE_NAME}
    properties = [f'ENV{{LIBINPUT_CALIBRATION_MATRIX}}="{matrix}"']
    if output_name:
        properties.append(f'ENV{{WL_OUTPUT}}="{udev_escape(output_name)}"')

    return ", ".join([*make_udev_conditions(record), *properties])

def make_ignore_rule(record):
    return ", ".join([
        *make_udev_conditions(record),
        'ENV{LIBINPUT_IGNORE_DEVICE}="1"',
    ])

def udev_unescape(value):
    return str(value).replace('\\"', '"').replace("\\\\", "\\")

def udev_rule_condition_value(rule_line, condition):
    match = re.search(
        re.escape(condition) + r'=="((?:\\.|[^"\\])*)"',
        rule_line,
    )
    if not match:
        return None

    return udev_unescape(match.group(1))

def udev_rule_matches_record(rule_line, record):
    rule_name = udev_rule_condition_value(rule_line, "ATTRS{name}")
    record_name = record.get("name", DEVICE_NAME)
    if rule_name != record_name:
        return False

    rule_phys = udev_rule_condition_value(rule_line, "ATTRS{phys}")
    rule_uniq = udev_rule_condition_value(rule_line, "ATTRS{uniq}")
    record_phys = record.get("phys") or ""
    record_uniq = record.get("uniq") or ""

    if rule_phys is None and rule_uniq is None:
        return True
    if rule_phys is not None and record_phys and rule_phys == record_phys:
        return True
    if rule_uniq is not None and record_uniq and rule_uniq == record_uniq:
        return True

    return not record_phys and not record_uniq

def udev_rule_conflicts_with_records(rule_line, records):
    if (
        "ENV{LIBINPUT_CALIBRATION_MATRIX}" not in rule_line
        and "ENV{LIBINPUT_IGNORE_DEVICE}" not in rule_line
    ):
        return False

    return any(udev_rule_matches_record(rule_line, record) for record in records)

def user_home_dir():
    sudo_user = os.environ.get("SUDO_USER")
    if hasattr(os, "geteuid") and os.geteuid() == 0 and sudo_user:
        try:
            return pwd.getpwnam(sudo_user).pw_dir
        except KeyError:
            pass

    return os.path.expanduser("~")

def labwc_rc_file():
    configured = os.environ.get("TOUCHSCREEN_LABWC_RC")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))

    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return os.path.join(os.path.abspath(os.path.expanduser(config_home)), "labwc", "rc.xml")

    return os.path.join(user_home_dir(), ".config", "labwc", "rc.xml")

def xml_local_name(element):
    return element.tag.rsplit("}", 1)[-1]

def xml_child_tag(root, name):
    if root.tag.startswith("{"):
        namespace = root.tag[1:].split("}", 1)[0]
        return f"{{{namespace}}}{name}"

    return name

def xml_direct_child(parent, name):
    for child in list(parent):
        if xml_local_name(child) == name:
            return child

    return None

def xml_direct_child_with_attr(parent, name, attr_name, attr_value):
    for child in list(parent):
        if xml_local_name(child) == name and child.get(attr_name) == attr_value:
            return child

    return None

def load_labwc_config(path):
    if os.path.exists(path):
        return ET.parse(path)

    root = ET.Element(f"{{{LABWC_XML_NS}}}openbox_config")
    return ET.ElementTree(root)

def labwc_config_tag(root, name):
    return xml_child_tag(root, name)

def set_labwc_libinput_calibration(root, device_name, matrix):
    libinput = xml_direct_child(root, "libinput")
    if libinput is None:
        libinput = ET.SubElement(root, labwc_config_tag(root, "libinput"))

    device = xml_direct_child_with_attr(libinput, "device", "category", device_name)
    if device is None:
        device = ET.SubElement(libinput, labwc_config_tag(root, "device"))
        device.set("category", device_name)

    calibration = xml_direct_child(device, "calibrationMatrix")
    if calibration is None:
        calibration = ET.SubElement(device, labwc_config_tag(root, "calibrationMatrix"))

    calibration.text = matrix

def clear_labwc_libinput_calibration(root, device_name):
    cleared = False

    for libinput in root.iter():
        if xml_local_name(libinput) != "libinput":
            continue

        for device in list(libinput):
            if xml_local_name(device) != "device":
                continue
            if device.get("category") != device_name:
                continue

            for calibration in list(device):
                if xml_local_name(calibration) == "calibrationMatrix":
                    device.remove(calibration)
                    cleared = True

    return cleared

def update_labwc_touch_mapping(device_name, output_name, calibration_matrix=None):
    if not output_name:
        return False, "Labwc touch mapping skipped because the selected output is unknown."

    path = labwc_rc_file()
    tree = load_labwc_config(path)
    root = tree.getroot()
    if root is None:
        root = ET.Element(f"{{{LABWC_XML_NS}}}openbox_config")
        tree._setroot(root)

    updated = False

    for element in root.iter():
        if xml_local_name(element) != "touch":
            continue
        if element.get("deviceName") != device_name:
            continue

        element.set("mapToOutput", output_name)
        if element.get("mouseEmulation") is None:
            element.set("mouseEmulation", "yes")
        updated = True

    if not updated:
        touch = ET.SubElement(root, xml_child_tag(root, "touch"))
        touch.set("deviceName", device_name)
        touch.set("mapToOutput", output_name)
        touch.set("mouseEmulation", "yes")

    wrote_calibration = False
    cleared_calibration = False
    if LABWC_WRITE_CALIBRATION_MATRIX and calibration_matrix:
        set_labwc_libinput_calibration(root, device_name, calibration_matrix)
        wrote_calibration = True
    else:
        cleared_calibration = clear_labwc_libinput_calibration(root, device_name)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    ET.register_namespace("", LABWC_XML_NS)
    if hasattr(ET, "indent"):
        ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)

    if wrote_calibration:
        matrix_message = " with calibrationMatrix"
    elif cleared_calibration:
        matrix_message = " without calibrationMatrix; removed stale Labwc calibrationMatrix"
    else:
        matrix_message = " without calibrationMatrix"
    return True, f"Labwc touch mapping{matrix_message} saved: {device_name} -> {output_name} in {path}"

def reload_labwc_config():
    labwc = shutil.which("labwc")
    if labwc is None:
        return False, "labwc command was not found"

    completed = subprocess.run(
        [labwc, "--reconfigure"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if completed.returncode == 0:
        return True, "Labwc configuration reloaded"

    detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
    return False, f"Labwc reconfigure failed: {detail}"

def kanshi_config_files():
    configured = os.environ.get("TOUCHSCREEN_KANSHI_CONFIG")
    if configured:
        return [os.path.abspath(os.path.expanduser(configured))]

    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        user_config = os.path.join(
            os.path.abspath(os.path.expanduser(config_home)),
            "kanshi",
            "config",
        )
    else:
        user_config = os.path.join(user_home_dir(), ".config", "kanshi", "config")

    return [
        user_config,
        "/etc/xdg/labwc-greeter/config.kanshi",
    ]

def parse_kanshi_output_transform(line, output_name):
    match = re.search(
        r"\boutput\s+"
        + re.escape(output_name)
        + r"\b.*\btransform\s+([A-Za-z0-9_-]+)",
        line,
    )
    if match:
        return match.group(1).lower()

    return None

def transform_from_kanshi(output_name):
    if not output_name:
        return None

    for path in kanshi_config_files():
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue

        for line in lines:
            transform = parse_kanshi_output_transform(line, output_name)
            if transform:
                return transform, path

    return None

def output_transform_for_display(output_name):
    if OUTPUT_TRANSFORM and OUTPUT_TRANSFORM != "auto":
        return OUTPUT_TRANSFORM, "TOUCHSCREEN_OUTPUT_TRANSFORM"

    detected = transform_from_kanshi(output_name)
    if detected:
        return detected

    return "normal", "default"

def normalize_output_transform(transform):
    normalized = (transform or "normal").strip().lower().replace("_", "-")
    normalized = normalized.replace("rotate-", "")

    aliases = {
        "": "normal",
        "0": "normal",
        "none": "normal",
        "identity": "normal",
        "normal": "normal",
        "right": "90",
        "cw": "90",
        "clockwise": "90",
        "90": "90",
        "inverted": "180",
        "upside-down": "180",
        "180": "180",
        "left": "270",
        "ccw": "270",
        "counterclockwise": "270",
        "counter-clockwise": "270",
        "270": "270",
        "flip": "flipped",
        "mirror": "flipped",
        "mirrored": "flipped",
        "flipped": "flipped",
        "flipped-0": "flipped",
        "flip-90": "flipped-90",
        "flipped-90": "flipped-90",
        "flip-180": "flipped-180",
        "flipped-180": "flipped-180",
        "flip-270": "flipped-270",
        "flipped-270": "flipped-270",
    }

    return aliases.get(normalized, normalized)

def compose_affine(outer, inner):
    oa, ob, oc, od, oe, of = outer
    ia, ib, ic, id_, ie, if_ = inner

    return [
        oa * ia + ob * id_,
        oa * ib + ob * ie,
        oa * ic + ob * if_ + oc,
        od * ia + oe * id_,
        od * ib + oe * ie,
        od * ic + oe * if_ + of,
    ]

def inverse_output_transform_matrix(transform):
    normalized = normalize_output_transform(transform)
    inverse_matrices = {
        "normal": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        "90": [0.0, 1.0, 0.0, -1.0, 0.0, 1.0],
        "180": [-1.0, 0.0, 1.0, 0.0, -1.0, 1.0],
        "270": [0.0, -1.0, 1.0, 1.0, 0.0, 0.0],
        "flipped": [-1.0, 0.0, 1.0, 0.0, 1.0, 0.0],
        "flipped-90": [0.0, -1.0, 1.0, -1.0, 0.0, 1.0],
        "flipped-180": [1.0, 0.0, 0.0, 0.0, -1.0, 1.0],
        "flipped-270": [0.0, 1.0, 0.0, 1.0, 0.0, 0.0],
    }

    return inverse_matrices.get(normalized), normalized

def compose_after_transform(values, transform):
    correction = OUTPUT_TRANSFORM_CORRECTION
    if correction in ("", "auto"):
        correction = "inverse"

    inverse_matrix, normalized = inverse_output_transform_matrix(transform)

    if inverse_matrix is None:
        print(f"Output transform {transform!r} is not handled; using unrotated touch matrix.")
        return values, normalized

    if normalized == "normal":
        return values, "normal"

    if correction in ("none", "off", "solved", "calibrated"):
        return values, f"{normalized} (solved matrix)"

    if correction in ("inverse", "pre-rotate", "pre_rotate"):
        return compose_affine(inverse_matrix, values), f"inverse {normalized}"

    print(
        f"Ignoring unknown TOUCHSCREEN_OUTPUT_TRANSFORM_CORRECTION={OUTPUT_TRANSFORM_CORRECTION!r}; "
        "using solved touch matrix."
    )
    return values, normalized

def persistent_matrix_choice(local_matrix, desktop_matrix, output_name, labwc_mapping_ok):
    if MATRIX_MODE in ("auto", "") and labwc_mapping_ok:
        return local_matrix, None, "local matrix with Labwc mapToOutput"

    if MATRIX_MODE in ("local", "output", "compositor", "labwc"):
        return local_matrix, None, "local matrix with compositor output mapping"

    if MATRIX_MODE in ("wl_output", "wl-output"):
        return local_matrix, output_name, "local output matrix with WL_OUTPUT"

    if MATRIX_MODE not in ("desktop", "full_desktop", "full-desktop"):
        print(f"Ignoring unknown TOUCHSCREEN_MATRIX_MODE={MATRIX_MODE!r}; using desktop matrix.")

    return desktop_matrix, None, "desktop matrix"

class TouchReader(threading.Thread):
    def __init__(self, devpath):
        super().__init__(daemon=True)
        self.dev = InputDevice(devpath)
        self.device_record = record_for_event(devpath) or record_from_evdev(self.dev)
        self.running = True
        self.abs_x = None
        self.abs_y = None
        self.touching = False
        self.current_points = []
        self.samples = []
        self.lock = threading.Lock()
        self.x_range = self.get_abs_range((ecodes.ABS_X, ecodes.ABS_MT_POSITION_X))
        self.y_range = self.get_abs_range((ecodes.ABS_Y, ecodes.ABS_MT_POSITION_Y))
        self.grabbed = False
        self.closed = False
        print(f"Touch hardware X range: {self.x_range}")
        print(f"Touch hardware Y range: {self.y_range}")
        self.grab_device()

    def begin_touch(self):
        if self.touching:
            return

        self.touching = True
        self.current_points = []
        print("Touch down")

    def finish_touch(self):
        if not self.touching and not self.current_points:
            return

        self.touching = False
        if self.current_points:
            pts = np.array(self.current_points, dtype=float)
            mean = pts.mean(axis=0)
            with self.lock:
                self.samples.append((mean[0], mean[1]))
                sample_number = len(self.samples)
            print(f"Captured sample #{sample_number}: x={mean[0]:.1f}, y={mean[1]:.1f}")
        self.current_points = []

    def grab_device(self):
        try:
            self.dev.grab()
            self.grabbed = True
            print("Touch device grabbed exclusively for calibration.")
        except Exception as exc:
            print(f"Could not grab touch device exclusively: {exc}")

    def get_abs_range(self, codes):
        for code in codes:
            try:
                info = self.dev.absinfo(code)
            except Exception:
                continue

            if info.max != info.min:
                return float(info.min), float(info.max)

        return None

    def normalize_axis(self, value, axis_range):
        if axis_range is None:
            raise RuntimeError("Touch device did not report an absolute input range.")

        min_value, max_value = axis_range
        return (float(value) - min_value) / (max_value - min_value)

    def normalize_samples(self, samples):
        return [
            (
                self.normalize_axis(x, self.x_range),
                self.normalize_axis(y, self.y_range),
            )
            for x, y in samples
        ]

    def sample_count(self):
        with self.lock:
            return len(self.samples)

    def samples_snapshot(self):
        with self.lock:
            return list(self.samples)

    def clear_samples(self):
        with self.lock:
            self.samples.clear()

    def run(self):
        print(f"Reading from {self.dev.path}: {self.dev.name}")
        touch_key_codes = {ecodes.BTN_TOUCH}
        btn_tool_finger = getattr(ecodes, "BTN_TOOL_FINGER", None)
        if btn_tool_finger is not None:
            touch_key_codes.add(btn_tool_finger)
        tracking_code = getattr(ecodes, "ABS_MT_TRACKING_ID", None)

        try:
            for event in self.dev.read_loop():
                if not self.running:
                    break

                if event.type == ecodes.EV_ABS:
                    if event.code in (ecodes.ABS_X, ecodes.ABS_MT_POSITION_X):
                        self.abs_x = event.value
                    elif event.code in (ecodes.ABS_Y, ecodes.ABS_MT_POSITION_Y):
                        self.abs_y = event.value
                    elif tracking_code is not None and event.code == tracking_code:
                        if event.value >= 0:
                            self.begin_touch()
                        else:
                            self.finish_touch()

                elif event.type == ecodes.EV_KEY and event.code in touch_key_codes:
                    if event.value:
                        self.begin_touch()
                    else:
                        self.finish_touch()

                elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                    if self.touching and self.abs_x is not None and self.abs_y is not None:
                        self.current_points.append((self.abs_x, self.abs_y))
        except OSError as exc:
            if self.running:
                print(f"Touch reader stopped after input device error: {exc}")

    def stop(self):
        self.running = False
        if self.closed:
            return
        if self.grabbed:
            try:
                self.dev.ungrab()
            except Exception:
                pass
            self.grabbed = False
        try:
            self.dev.close()
        except Exception:
            pass
        self.closed = True

def solve_affine(raw_pts, target_pts):
    A = []
    B = []
    for (xr, yr), (xt, yt) in zip(raw_pts, target_pts):
        A.append([xr, yr, 1, 0, 0, 0])
        A.append([0, 0, 0, xr, yr, 1])
        B.append(xt)
        B.append(yt)
    A = np.array(A, dtype=float)
    B = np.array(B, dtype=float)
    sol, *_ = np.linalg.lstsq(A, B, rcond=None)
    return sol

def get_desktop_sizes():
    if hasattr(pygame.display, "get_desktop_sizes"):
        sizes = pygame.display.get_desktop_sizes()
        if sizes:
            return sizes

    info = pygame.display.Info()
    return [(info.current_w, info.current_h)]

def parse_xrandr_listmonitors(output):
    rects = []
    pattern = re.compile(r"\s(?P<width>\d+)/\d+x(?P<height>\d+)/\d+(?P<x>[+-]\d+)(?P<y>[+-]\d+)")

    for line in output.splitlines():
        match = pattern.search(line)
        if not match:
            continue

        rects.append((
            int(match.group("x")),
            int(match.group("y")),
            int(match.group("width")),
            int(match.group("height")),
        ))

    return rects

def parse_xrandr_query(output):
    rects = []
    pattern = re.compile(r"\bconnected(?:\s+primary)?\s+(?P<width>\d+)x(?P<height>\d+)(?P<x>[+-]\d+)(?P<y>[+-]\d+)")

    for line in output.splitlines():
        match = pattern.search(line)
        if not match:
            continue

        rects.append((
            int(match.group("x")),
            int(match.group("y")),
            int(match.group("width")),
            int(match.group("height")),
        ))

    return rects

def parse_xrandr_listmonitor_infos(output):
    infos = []
    pattern = re.compile(r"\s(?P<width>\d+)/\d+x(?P<height>\d+)/\d+(?P<x>[+-]\d+)(?P<y>[+-]\d+)")

    for line in output.splitlines():
        match = pattern.search(line)
        if not match:
            continue

        tokens = line.split()
        if not tokens:
            continue

        infos.append({
            "name": tokens[-1],
            "rect": (
                int(match.group("x")),
                int(match.group("y")),
                int(match.group("width")),
                int(match.group("height")),
            ),
        })

    return infos

def parse_xrandr_query_infos(output):
    infos = []
    pattern = re.compile(r"\bconnected(?:\s+primary)?\s+(?P<width>\d+)x(?P<height>\d+)(?P<x>[+-]\d+)(?P<y>[+-]\d+)")

    for line in output.splitlines():
        match = pattern.search(line)
        if not match:
            continue

        tokens = line.split()
        if not tokens:
            continue

        infos.append({
            "name": tokens[0],
            "rect": (
                int(match.group("x")),
                int(match.group("y")),
                int(match.group("width")),
                int(match.group("height")),
            ),
        })

    return infos

def xrandr_monitor_infos():
    xrandr = shutil.which("xrandr")
    if xrandr is None:
        return []

    commands = [
        ([xrandr, "--listmonitors"], parse_xrandr_listmonitor_infos),
        ([xrandr, "--query"], parse_xrandr_query_infos),
    ]

    for command, parser in commands:
        try:
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
        except Exception:
            continue

        if completed.returncode != 0:
            continue

        infos = parser(completed.stdout)
        if infos:
            return infos

    return []

def parse_wlr_randr_infos(output):
    infos = []
    current = None
    in_modes = False

    for line in output.splitlines():
        if line and not line.startswith(" "):
            if current is not None and current.get("rect") is not None:
                infos.append(current)

            current = {"name": line.split()[0], "position": None, "size": None, "rect": None}
            in_modes = False
            continue

        if current is None:
            continue

        stripped = line.strip()
        if stripped == "Modes:":
            in_modes = True
            continue

        if stripped.startswith("Position:"):
            numbers = re.findall(r"-?\d+", stripped)
            if len(numbers) >= 2:
                current["position"] = (int(numbers[0]), int(numbers[1]))
            continue

        if in_modes and "(current)" in stripped:
            match = re.search(r"(?P<width>\d+)x(?P<height>\d+)", stripped)
            if match:
                current["size"] = (int(match.group("width")), int(match.group("height")))

        if current.get("position") is not None and current.get("size") is not None:
            x, y = current["position"]
            width, height = current["size"]
            current["rect"] = (x, y, width, height)

    if current is not None and current.get("rect") is not None:
        infos.append(current)

    return infos

def wayland_monitor_infos():
    wlr_randr = shutil.which("wlr-randr")
    if wlr_randr is None:
        return []

    try:
        completed = subprocess.run(
            [wlr_randr],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except Exception:
        return []

    if completed.returncode != 0:
        return []

    return parse_wlr_randr_infos(completed.stdout)

def xrandr_monitor_rects():
    xrandr = shutil.which("xrandr")
    if xrandr is None:
        return []

    commands = [
        ([xrandr, "--listmonitors"], parse_xrandr_listmonitors),
        ([xrandr, "--query"], parse_xrandr_query),
    ]

    for command, parser in commands:
        try:
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
        except Exception:
            continue

        if completed.returncode != 0:
            continue

        rects = parser(completed.stdout)
        if rects:
            return rects

    return []

def fallback_display_rects(desktop_sizes):
    rects = []

    if DESKTOP_LAYOUT == "vertical":
        y = 0
        for width, height in desktop_sizes:
            rects.append((0, y, width, height))
            y += height
    else:
        if DESKTOP_LAYOUT != "horizontal":
            print(f"Ignoring unknown TOUCHSCREEN_DESKTOP_LAYOUT={DESKTOP_LAYOUT!r}.")
        x = 0
        for width, height in desktop_sizes:
            rects.append((x, 0, width, height))
            x += width

    return rects

def get_display_rects(desktop_sizes):
    rects = xrandr_monitor_rects()
    if len(rects) == len(desktop_sizes):
        return rects

    if rects:
        print(f"Ignoring monitor geometry from xrandr because it found {len(rects)} displays.")

    return fallback_display_rects(desktop_sizes)

def display_output_names(display_rects):
    sources = [wayland_monitor_infos(), xrandr_monitor_infos()]
    names = [None] * len(display_rects)

    for infos in sources:
        if not infos:
            continue

        for index, rect in enumerate(display_rects):
            for info in infos:
                if info["rect"] == rect:
                    names[index] = info["name"]
                    break

        if all(name is not None for name in names):
            return names

        if len(infos) == len(display_rects):
            for index, name in enumerate(names):
                if name is None:
                    names[index] = infos[index]["name"]
            return names

    return names

def preferred_display_index(output_names):
    if not PREFERRED_OUTPUT_NAME:
        return 0

    for index, output_name in enumerate(output_names):
        if output_name == PREFERRED_OUTPUT_NAME:
            return index

    print(
        f"Preferred touchscreen output {PREFERRED_OUTPUT_NAME!r} was not detected; "
        "starting with display 1."
    )
    return 0

def desktop_bounds(display_rects):
    min_x = min(rect[0] for rect in display_rects)
    min_y = min(rect[1] for rect in display_rects)
    max_x = max(rect[0] + rect[2] for rect in display_rects)
    max_y = max(rect[1] + rect[3] for rect in display_rects)
    return min_x, min_y, max_x - min_x, max_y - min_y

def calibration_targets_for_display(screen_targets, display_rects, display_index):
    x, y, width, height = display_rects[display_index]
    min_x, min_y, total_width, total_height = desktop_bounds(display_rects)

    return [
        (
            (x - min_x + target_x * width) / total_width,
            (y - min_y + target_y * height) / total_height,
        )
        for target_x, target_y in screen_targets
    ]

def open_touchscreen_screen(display_index: int, display_rect) -> pygame.Surface:
    x, y, width, height = display_rect
    os.environ["SDL_VIDEO_WINDOW_POS"] = f"{x},{y}"

    try:
        screen = pygame.display.set_mode((width, height), pygame.NOFRAME, display=display_index)
    except TypeError:
        screen = pygame.display.set_mode((width, height), pygame.NOFRAME)
    pygame.display.set_caption(f"{SETUP_TITLE} - Display {display_index + 1}")

    try:
        from pygame._sdl2.video import Window
        window = Window.from_display_module()
        window.borderless = True
        window.position = (int(x), int(y))
        window.focus()
    except Exception as exc:
        print(f"Could not force window position with SDL2 API: {exc}")

    return screen

def make_font(size, bold=False):
    font = pygame.font.Font(None, size)
    font.set_bold(bold)
    return font

def draw_screen_frame(screen):
    width, height = screen.get_size()
    border_width = max(3, min(width, height) // 120)
    corner_size = max(18, min(width, height) // 18)

    pygame.draw.rect(screen, (255, 255, 255), (0, 0, width, height), border_width)
    pygame.draw.rect(screen, (255, 255, 255), (0, 0, corner_size, corner_size))
    pygame.draw.rect(screen, (255, 255, 255), (width - corner_size, 0, corner_size, corner_size))
    pygame.draw.rect(screen, (255, 255, 255), (0, height - corner_size, corner_size, corner_size))
    pygame.draw.rect(
        screen,
        (255, 255, 255),
        (width - corner_size, height - corner_size, corner_size, corner_size),
    )

def wrap_text(text, font, max_width):
    words = text.split()
    if not words:
        return [""]

    lines = []
    current = words[0]

    for word in words[1:]:
        candidate = f"{current} {word}"
        if font.size(candidate)[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word

    lines.append(current)
    return lines

def draw_text_block(screen, lines, font, color, center_x, y, max_width, line_gap):
    for line in lines:
        for wrapped_line in wrap_text(line, font, max_width):
            rendered = font.render(wrapped_line, True, color)
            rect = rendered.get_rect(center=(center_x, y))
            screen.blit(rendered, rect)
            y += font.get_linesize() + line_gap
    return y

def draw_left_labels(screen, labels, font, color, x, y):
    for label in labels:
        rendered = font.render(label, True, color)
        screen.blit(rendered, (x, y))
        y += font.get_linesize() + 4

def draw_centered_lines(screen, lines, font, small_font):
    screen.fill((0, 0, 0))
    width, height = screen.get_size()
    draw_screen_frame(screen)

    safe_margin = max(24, min(width, height) // 14)
    max_width = max(120, width - safe_margin * 2)
    y = safe_margin

    for text, color, use_large_font in lines:
        active_font = font if use_large_font else small_font
        y = draw_text_block(
            screen,
            [text],
            active_font,
            color,
            width // 2,
            y,
            max_width,
            3,
        )
        y += 8 if use_large_font else 3

    pygame.display.flip()

def draw_setup_screen(screen, display_index, display_count, font, small_font):
    screen.fill((0, 0, 0))
    width, height = screen.get_size()
    draw_screen_frame(screen)

    display_text = f"Display {display_index + 1} of {display_count}"
    if display_count > 1:
        next_text = "Press ENTER if this is not your touchscreen."
    else:
        next_text = "Only one display was detected."

    safe_margin = max(24, min(width, height) // 14)
    max_width = max(120, width - safe_margin * 2)
    center_x = width // 2
    y = safe_margin

    y = draw_text_block(
        screen,
        [SETUP_TITLE],
        font,
        (255, 255, 255),
        center_x,
        y,
        max_width,
        4,
    )
    y = draw_text_block(
        screen,
        [display_text],
        small_font,
        (210, 210, 210),
        center_x,
        y + 8,
        max_width,
        2,
    )

    tap_font = make_font(max(34, min(72, height // 7)), bold=True)
    tap_y = max(y + tap_font.get_linesize(), height // 2 - tap_font.get_linesize() // 2)
    draw_text_block(
        screen,
        ["TAP THIS SCREEN"],
        tap_font,
        (255, 80, 80),
        center_x,
        tap_y,
        max_width,
        4,
    )

    bottom_y = max(tap_y + tap_font.get_linesize() + 28, height - safe_margin - small_font.get_linesize() * 3)
    bottom_y = min(bottom_y, height - safe_margin - small_font.get_linesize() * 4)
    draw_text_block(
        screen,
        [
            "If this is the touchscreen, tap anywhere or press SPACE.",
            next_text,
            "Press ESC to cancel.",
        ],
        small_font,
        (255, 255, 255),
        center_x,
        bottom_y,
        max_width,
        2,
    )

    label_font = make_font(max(22, min(34, height // 14)), bold=True)
    label_x = safe_margin + max(18, min(width, height) // 18)
    draw_left_labels(
        screen,
        ["TAP = SELECT", "SPACE = SELECT"],
        label_font,
        (255, 80, 80),
        label_x,
        safe_margin,
    )
    draw_left_labels(
        screen,
        ["ENTER = NEXT", "ESC = CANCEL"],
        label_font,
        (255, 255, 255),
        label_x,
        max(safe_margin, height - safe_margin - label_font.get_linesize() * 2 - 8),
    )

    pygame.display.flip()

def draw_starting_calibration(screen, font, small_font):
    draw_centered_lines(
        screen,
        [
            (SETUP_TITLE, (255, 255, 255), True),
            ("Touchscreen selected.", (255, 255, 255), False),
            ("Calibration will begin now.", (210, 210, 210), False),
        ],
        font,
        small_font,
    )

def draw_target_pixel(target, width, height):
    safe_margin = TARGET_ARM_LENGTH + TARGET_LINE_WIDTH + TARGET_EDGE_PADDING
    raw_x = int(target[0] * width)
    raw_y = int(target[1] * height)
    return (
        min(max(raw_x, safe_margin), max(safe_margin, width - safe_margin)),
        min(max(raw_y, safe_margin), max(safe_margin, height - safe_margin)),
    )

def calibration_targets_for_screen(width, height):
    targets = []
    x_denominator = max(width - 1, 1)
    y_denominator = max(height - 1, 1)

    for target in DRAW_TARGETS:
        target_x, target_y = draw_target_pixel(target, width, height)
        normalized_x = target_x / x_denominator
        normalized_y = target_y / y_denominator

        targets.append((normalized_x, normalized_y))

    return targets

def select_touchscreen_display(
    reader,
    desktop_sizes,
    display_rects,
    clock,
    initial_display_index=0,
) -> tuple[int | None, pygame.Surface | None]:
    display_count = len(desktop_sizes)
    display_index = min(max(int(initial_display_index), 0), display_count - 1)

    while True:
        screen = open_touchscreen_screen(display_index, display_rects[display_index])
        pygame.event.set_grab(True)
        pygame.mouse.set_visible(False)

        font = make_font(44, bold=True)
        small_font = make_font(30)
        reader.clear_samples()
        draw_setup_screen(screen, display_index, display_count, font, small_font)
        print(
            f"Showing setup prompt on display {display_index + 1}/{display_count}, "
            f"size={desktop_sizes[display_index]}, rect={display_rects[display_index]}"
        )

        while True:
            selected_by_keyboard = False
            next_display = False

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return None, None
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        return None, None
                    if event.key in (pygame.K_SPACE, pygame.K_s):
                        selected_by_keyboard = True
                        break
                    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        next_display = True
                        break

            if selected_by_keyboard:
                print(f"Touchscreen selected by keyboard: display {display_index + 1}/{display_count}")
                reader.clear_samples()
                draw_starting_calibration(screen, font, small_font)
                pygame.time.wait(700)
                reader.clear_samples()
                return display_index, screen

            if next_display:
                display_index = (display_index + 1) % display_count
                break

            if reader.sample_count() > 0:
                print(f"Touchscreen selected by touch: display {display_index + 1}/{display_count}")
                reader.clear_samples()
                draw_starting_calibration(screen, font, small_font)
                pygame.time.wait(700)
                reader.clear_samples()
                return display_index, screen

            clock.tick(60)

def is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0

def run_privileged(args, input_text=None, quiet_stdout=False):
    if is_root():
        command = args
    else:
        sudo = shutil.which("sudo")
        if sudo is None:
            raise RuntimeError(
                "Need root permission to update udev rules, but sudo was not found."
            )
        command = [sudo, "-n", *args]

    completed = subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.DEVNULL if quiet_stdout else subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        if not is_root() and "password" in stderr.lower():
            raise RuntimeError(
                "Need root permission to update udev rules. Run 'sudo -v' first, "
                "or run this script with sudo."
            )
        detail = f": {stderr}" if stderr else ""
        raise RuntimeError(f"Command failed: {' '.join(args)}{detail}")

    return "" if quiet_stdout else completed.stdout

def read_rules_file():
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return f.readlines()
    except FileNotFoundError:
        return ["# Touchscreen calibration history\n"]
    except PermissionError:
        return run_privileged(["cat", RULES_FILE]).splitlines(keepends=True)

def write_rules_file(lines):
    text = "".join(lines)
    try:
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            f.write(text)
    except PermissionError:
        run_privileged(["tee", RULES_FILE], input_text=text, quiet_stdout=True)

def write_rule(matrix, output_name=None, target_record=None, ignored_records=None):
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ignored_records = ignored_records or []
    replacement_records = [target_record or {"name": DEVICE_NAME}, *ignored_records]
    rule_line = make_calibration_rule(matrix, output_name, target_record)
    ignored_rule_lines = [make_ignore_rule(record) for record in ignored_records]

    lines = []

    # Keep other touchscreen profiles active; retire only rules this calibration replaces.
    for line in read_rules_file():
        stripped = line.strip()

        if stripped == "":
            lines.append(line)
            continue

        if stripped.startswith("#"):
            lines.append(line)
        elif udev_rule_conflicts_with_records(stripped, replacement_records):
            lines.append("# " + line)
        else:
            lines.append(line)

    # Add new calibration with timestamp
    lines.append(f"\n# Calibration {timestamp}\n")
    if target_record:
        target_event = event_path_for_record(target_record) or "unknown event"
        target_phys = target_record.get("phys") or "unknown phys"
        lines.append(f"# Target touchscreen: {target_event}, phys={target_phys}\n")
    lines.append(rule_line + "\n")

    if ignored_rule_lines:
        lines.append("# Ignore duplicate mouse-like interfaces from the same touchscreen USB device.\n")
        for record, ignored_rule_line in zip(ignored_records, ignored_rule_lines):
            duplicate_event = event_path_for_record(record) or "unknown event"
            duplicate_phys = record.get("phys") or "unknown phys"
            lines.append(f"# Duplicate interface: {duplicate_event}, phys={duplicate_phys}\n")
            lines.append(ignored_rule_line + "\n")

    write_rules_file(lines)

    return rule_line, ignored_rule_lines

def reload_udev():
    run_privileged(["udevadm", "control", "--reload-rules"])
    run_privileged(["udevadm", "trigger"])

def xinput_device_ids():
    xinput = shutil.which("xinput")
    if xinput is None:
        return []

    completed = subprocess.run(
        [xinput, "list", "--id-only", DEVICE_NAME],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if completed.returncode != 0:
        return []

    return [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    ]

def apply_live_xinput_matrix(matrix_values, desktop_matrix_values, output_name=None):
    xinput = shutil.which("xinput")
    if xinput is None:
        return False, "xinput was not found"

    device_ids = xinput_device_ids()
    if not device_ids:
        return False, f"xinput did not find device {DEVICE_NAME!r}"

    matrix_9 = [*matrix_values, 0.0, 0.0, 1.0]
    matrix_args = [f"{value:.6f}" for value in matrix_9]
    desktop_matrix_9 = [*desktop_matrix_values, 0.0, 0.0, 1.0]
    desktop_matrix_args = [f"{value:.6f}" for value in desktop_matrix_9]
    identity_args = [
        "1.000000", "0.000000", "0.000000",
        "0.000000", "1.000000", "0.000000",
        "0.000000", "0.000000", "1.000000",
    ]
    failures = []
    applied = []

    def set_ctm(device_id, args):
        return subprocess.run(
            [xinput, "set-prop", device_id, "Coordinate Transformation Matrix", *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    for device_id in device_ids:
        if output_name:
            map_result = subprocess.run(
                [xinput, "map-to-output", device_id, output_name],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            if map_result.returncode != 0:
                ctm_result = set_ctm(device_id, desktop_matrix_args)
                if ctm_result.returncode == 0:
                    applied.append(device_id)
                    continue

                failures.append(
                    ctm_result.stderr.strip()
                    or map_result.stderr.strip()
                    or f"xinput map-to-output failed for id {device_id}"
                )
                continue

        libinput_result = subprocess.run(
            [xinput, "set-prop", device_id, "libinput Calibration Matrix", *matrix_args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        if libinput_result.returncode == 0:
            applied.append(device_id)
            if not output_name:
                set_ctm(device_id, identity_args)
            continue

        ctm_result = set_ctm(device_id, desktop_matrix_args)
        if ctm_result.returncode == 0:
            applied.append(device_id)
            continue

        failures.append(
            ctm_result.stderr.strip()
            or libinput_result.stderr.strip()
            or f"xinput set-prop failed for id {device_id}"
        )

    if not applied:
        return False, "; ".join(failures)

    if failures:
        return True, f"applied to id(s): {', '.join(applied)}; skipped: {'; '.join(failures)}"

    return True, f"applied live xinput matrix to id(s): {', '.join(applied)}"

def main():
    print_touchscreen_candidates(DEVICE_NAME)
    event_dev = select_touch_event_device(EVENT_DEV, DEVICE_NAME)
    print(f"Auto-selected calibration input: {event_dev}")
    reader = TouchReader(event_dev)
    duplicate_records = duplicate_records_for_target(reader.device_record)

    print(f"Calibration input device: {reader.dev.path}")
    print(f"Calibration input phys: {reader.device_record.get('phys') or 'unknown'}")
    if duplicate_records:
        duplicate_summary = [
            f"{event_path_for_record(record) or 'unknown'} ({record.get('phys') or 'unknown phys'})"
            for record in duplicate_records
        ]
        print("Duplicate mouse-like interfaces to ignore: " + ", ".join(duplicate_summary))

    reader.start()

    pygame.init()
    pygame.font.init()

    desktop_sizes = get_desktop_sizes()
    display_rects = get_display_rects(desktop_sizes)
    output_names = display_output_names(display_rects)
    clock = pygame.time.Clock()
    initial_display_index = preferred_display_index(output_names)
    print(f"pygame displays: {desktop_sizes}")
    print(f"desktop monitor rects: {display_rects}")
    print(f"display output names: {output_names}")
    print(f"preferred touchscreen output: {PREFERRED_OUTPUT_NAME or 'none'}")

    display_index, screen = select_touchscreen_display(
        reader,
        desktop_sizes,
        display_rects,
        clock,
        initial_display_index,
    )
    if display_index is None:
        reader.stop()
        pygame.quit()
        return
    if screen is None:
        reader.stop()
        pygame.quit()
        return

    selected_display_index = display_index
    display_size = display_rects[selected_display_index][2:4]
    selected_output_name = output_names[selected_display_index] or PREFERRED_OUTPUT_NAME or None

    width, height = screen.get_size()
    calibration_targets = calibration_targets_for_screen(width, height)
    desktop_targets = calibration_targets_for_display(
        calibration_targets,
        display_rects,
        selected_display_index,
    )
    font = make_font(40, bold=True)
    small_font = make_font(28)
    print(f"using touchscreen display index: {selected_display_index}, size: {display_size}")
    print(f"selected output name: {selected_output_name}")
    print(f"pygame screen size: {width} x {height}")
    print(
        "calibration target margins: "
        f"top/left/right={TARGET_MARGIN:.3f}, "
        f"bottom draw={BOTTOM_DRAW_TARGET_MARGIN:.3f}"
    )
    target_1_x, target_1_y = draw_target_pixel(DRAW_TARGETS[0], width, height)
    bottom_draw_x, bottom_draw_y = draw_target_pixel(DRAW_TARGETS[2], width, height)
    bottom_calibration_pixel_y = int(round(calibration_targets[2][1] * max(height - 1, 1)))
    print(
        f"target 1 pixel: x={target_1_x}, y={target_1_y}; "
        f"calibration normalized: x={calibration_targets[0][0]:.6f}, "
        f"y={calibration_targets[0][1]:.6f}"
    )
    print(
        f"bottom drawn target pixel y={bottom_draw_y}, "
        f"calibration y={bottom_calibration_pixel_y} of {height - 1}"
    )
    print(
        "target 1 desktop normalized: "
        f"x={desktop_targets[0][0]:.6f}, y={desktop_targets[0][1]:.6f}"
    )

    index = 0
    seen_samples = 0
    finished = False
    reader.clear_samples()

    def draw_target(i):
        screen.fill((0, 0, 0))
        title = font.render(f"Touch target {i+1}/{len(DRAW_TARGETS)}", True, (255, 255, 255))
        hint = small_font.render("Touch the center of the red cross, then lift your finger", True, (210, 210, 210))
        screen.blit(title, (40, 30))
        screen.blit(hint, (40, 75))

        tx, ty = draw_target_pixel(DRAW_TARGETS[i], width, height)

        pygame.draw.circle(screen, (255, 0, 0), (tx, ty), TARGET_RADIUS, TARGET_LINE_WIDTH)
        pygame.draw.line(
            screen,
            (255, 0, 0),
            (tx - TARGET_ARM_LENGTH, ty),
            (tx + TARGET_ARM_LENGTH, ty),
            TARGET_LINE_WIDTH,
        )
        pygame.draw.line(
            screen,
            (255, 0, 0),
            (tx, ty - TARGET_ARM_LENGTH),
            (tx, ty + TARGET_ARM_LENGTH),
            TARGET_LINE_WIDTH,
        )

        pygame.display.flip()

    def draw_message(lines):
        screen.fill((0, 0, 0))
        margin = max(18, min(width, height) // 28)
        max_width = max(160, width - margin * 2)
        available_height = max(120, height - margin * 2)

        def layout_rows(active_font):
            rows = []
            for line in lines:
                if line == "":
                    rows.append("")
                else:
                    rows.extend(wrap_text(line, active_font, max_width))
            return rows

        active_font = small_font
        rows = layout_rows(active_font)
        line_gap = 4
        blank_height = max(5, active_font.get_linesize() // 2)

        for font_size in range(28, 13, -1):
            candidate_font = make_font(font_size)
            candidate_rows = layout_rows(candidate_font)
            candidate_gap = max(1, font_size // 8)
            candidate_blank_height = max(4, candidate_font.get_linesize() // 2)
            total_height = 0

            for row in candidate_rows:
                if row == "":
                    total_height += candidate_blank_height
                else:
                    total_height += candidate_font.get_linesize() + candidate_gap

            if total_height <= available_height:
                active_font = candidate_font
                rows = candidate_rows
                line_gap = candidate_gap
                blank_height = candidate_blank_height
                break

        y = margin
        for row in rows:
            if row == "":
                y += blank_height
                continue

            rendered = active_font.render(row, True, (255, 255, 255))
            screen.blit(rendered, (margin, y))
            y += active_font.get_linesize() + line_gap
        pygame.display.flip()

    draw_target(index)

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False
            elif event.type == pygame.QUIT:
                running = False

        current = reader.sample_count()
        if not finished and current > seen_samples:
            seen_samples = current
            index = current
            print(f"Advancing to target index {index}")

            if index < len(DRAW_TARGETS):
                draw_target(index)
            else:
                raw = np.array(reader.samples_snapshot()[:len(calibration_targets)], dtype=float)
                raw_norm = reader.normalize_samples(raw)
                a, b, c, d, e, f = solve_affine(raw_norm, calibration_targets)
                da, db, dc, dd, de, df = solve_affine(raw_norm, desktop_targets)

                vals = [a, b, c, d, e, f]
                desktop_vals = [da, db, dc, dd, de, df]
                matrix = " ".join(f"{v:.6f}" for v in vals)
                desktop_matrix = " ".join(f"{v:.6f}" for v in desktop_vals)
                selected_transform, transform_source = output_transform_for_display(selected_output_name)
                transformed_vals, applied_transform = compose_after_transform(
                    vals,
                    selected_transform,
                )
                transformed_matrix = " ".join(f"{v:.6f}" for v in transformed_vals)
                ignored_duplicate_records, duplicate_ignore_message = duplicate_ignore_records_for_transform(
                    duplicate_records,
                    selected_transform,
                )
                try:
                    labwc_mapping_ok, labwc_message = update_labwc_touch_mapping(
                        DEVICE_NAME,
                        selected_output_name,
                        transformed_matrix,
                    )
                except Exception as exc:
                    labwc_mapping_ok = False
                    labwc_message = f"Labwc touch mapping update failed: {exc}"

                saved_matrix, saved_output_name, saved_matrix_mode = persistent_matrix_choice(
                    transformed_matrix,
                    desktop_matrix,
                    selected_output_name,
                    labwc_mapping_ok,
                )
                saved_vals = desktop_vals if saved_matrix == desktop_matrix else transformed_vals
                labwc_output_status = selected_output_name if labwc_mapping_ok else "not used"

                print("\nLocal output calibration matrix:")
                print(matrix)
                print(
                    f"\nSelected output transform: {selected_transform} "
                    f"(source: {transform_source})"
                )
                print("\nPersisted local touch matrix:")
                print(transformed_matrix)
                print("\nDesktop calibration matrix:")
                print(desktop_matrix)
                print(f"\nPersisted mapping mode: {saved_matrix_mode}")
                print(f"Applied output transform correction: {applied_transform}")
                print(f"Duplicate-interface policy: {duplicate_ignore_message}")
                print(f"\n{labwc_message}")
                print("\nRaw samples:")
                print(raw)
                print("\nDevice-normalized samples:")
                print(np.array(raw_norm, dtype=float))

                try:
                    rule, ignored_rules = write_rule(
                        saved_matrix,
                        saved_output_name,
                        reader.device_record,
                        ignored_duplicate_records,
                    )
                    if RELOAD_UDEV_AFTER_SAVE:
                        reload_udev()
                        udev_reload_message = "udev rules reloaded"
                    else:
                        udev_reload_message = (
                            "udev reload skipped by TOUCHSCREEN_RELOAD_UDEV=0; "
                            "saved rule will apply after the input device is re-added or the system reboots"
                        )

                    if APPLY_LIVE_XINPUT:
                        live_ok, live_message = apply_live_xinput_matrix(
                            saved_vals,
                            desktop_vals,
                            saved_output_name,
                        )
                    else:
                        live_ok = False
                        live_message = "live xinput update skipped by TOUCHSCREEN_LIVE_XINPUT=0"

                    if labwc_mapping_ok:
                        labwc_reload_ok, labwc_reload_message = reload_labwc_config()
                    else:
                        labwc_reload_ok = False
                        labwc_reload_message = "Labwc reconfigure skipped"

                    if AUTO_USB_REENUMERATE:
                        try:
                            reader.stop()
                            reader.join(timeout=1.0)
                            usb_reenumerate_ok, usb_reenumerate_message, reenum_record, reenum_duplicates = (
                                reenumerate_usb_device_for_record(reader.device_record)
                            )
                        except Exception as exc:
                            usb_reenumerate_ok = False
                            usb_reenumerate_message = f"USB re-enumeration failed: {exc}"
                            reenum_record = None
                            reenum_duplicates = []
                    else:
                        usb_reenumerate_ok = False
                        usb_reenumerate_message = (
                            "USB re-enumeration skipped by TOUCHSCREEN_AUTO_USB_REENUMERATE=0"
                        )
                        reenum_record = None
                        reenum_duplicates = []

                    usb_display_message = usb_reenumerate_message
                    if usb_reenumerate_ok and reenum_record is not None:
                        usb_display_message = (
                            f"USB re-enumerated: {event_path_for_record(reenum_record) or 'unknown event'}"
                        )

                    print("\nSaved rule:")
                    print(rule)
                    if ignored_rules:
                        print("\nSaved duplicate-interface ignore rule(s):")
                        for ignored_rule in ignored_rules:
                            print(ignored_rule)
                    print(f"\n{udev_reload_message}.")
                    print(f"\nLive xinput update: {live_message}")
                    print(f"\nLabwc mapping: {labwc_message}")
                    print(f"Labwc reload: {labwc_reload_message}")
                    print(f"USB re-enumeration: {usb_reenumerate_message}")
                    if reenum_record is not None:
                        print(f"Auto-selected after re-enumeration: {input_record_summary(reenum_record)}")
                    if reenum_duplicates:
                        print("Duplicate interfaces after re-enumeration:")
                        for duplicate_record in reenum_duplicates:
                            print(f"  {input_record_summary(duplicate_record)}")

                    draw_message([
                        "Calibration complete",
                        "",
                        f'LIBINPUT_CALIBRATION_MATRIX="{saved_matrix}"',
                        "",
                        "Saved to:",
                        RULES_FILE,
                        "",
                        f"Mode: {saved_matrix_mode}",
                        f"Output transform: {selected_transform}",
                        f"udev WL_OUTPUT: {saved_output_name or 'not used'}",
                        f"Labwc mapToOutput: {labwc_output_status}",
                        f"Duplicate policy: {duplicate_ignore_message}",
                        f"Ignored duplicate interfaces: {len(ignored_rules)}",
                        "",
                        udev_reload_message,
                        "live xinput updated" if live_ok else "live xinput update skipped",
                        "labwc reloaded" if labwc_reload_ok else "labwc reload skipped",
                        "USB re-enumerated" if usb_reenumerate_ok else "USB re-enumeration skipped/failed",
                        "",
                        usb_display_message,
                        "",
                        "Press ESC to quit"
                    ])
                except Exception as exc:
                    print("\nCalibration was calculated, but saving/reloading failed:")
                    print(exc)

                    draw_message([
                        "Calibration calculated, but save/reload failed",
                        "",
                        f'LIBINPUT_CALIBRATION_MATRIX="{saved_matrix}"',
                        "",
                        str(exc),
                        "",
                        "Try running 'sudo -v' before starting this script,",
                        "or run the script with sudo.",
                        "",
                        "Press ESC to quit"
                    ])
                finished = True

        clock.tick(60)

    reader.stop()
    pygame.quit()

if __name__ == "__main__":
    main()
