"""Optional hardware and capture backends, probed once and reported honestly.

Nothing in this package is imported at start-up. A machine without a camera,
without a serial port and without GPIO still runs the whole interface; the
blocks that need those things report exactly which driver is missing and how
to install it, instead of failing with an import traceback.

Simulation is always opt-in and always labelled. A simulated device returns
plausible values so a workflow can be built and stepped through on a laptop,
and every value it produces carries ``simulated: true`` all the way into the
run log.
"""

import importlib
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Backend:
    module: str
    label: str
    install: str
    note: str = ""


@dataclass
class Capability:
    key: str
    name: str
    plain: str
    detail: str
    backends: list = field(default_factory=list)
    simulated_note: str = ""
    platform_note: str = ""


CAPABILITIES = [
    Capability(
        key="screen",
        name="Screen capture",
        plain="Take a picture of part of your own screen",
        detail="Grabs a fixed rectangle of the desktop. Use it to let the"
        " network look at a window, a web page, a game or an instrument"
        " readout that is already on screen.",
        backends=[
            Backend("mss", "mss", "pip install mss", "Fast, works on X11, Windows and macOS"),
            Backend("PIL.ImageGrab", "Pillow ImageGrab", "pip install Pillow", "Windows and macOS only"),
        ],
        simulated_note="Simulated screen capture returns a moving test pattern.",
        platform_note="On Linux a running X or XWayland display is required.",
    ),
    Capability(
        key="camera",
        name="Camera",
        plain="Live video from a webcam or robot camera",
        detail="Opens a camera by index and reads frames. This is the input"
        " for a robot that has to see where it is going.",
        backends=[
            Backend("cv2", "OpenCV", "pip install opencv-python-headless", "Also decodes video files"),
        ],
        simulated_note="Simulated camera returns a moving test pattern.",
    ),
    Capability(
        key="gpio",
        name="GPIO pins",
        plain="Raspberry Pi pins: read a switch, drive a motor or LED",
        detail="Digital input and output, PWM and servo control on a"
        " Raspberry Pi or compatible single-board computer.",
        backends=[
            Backend("gpiozero", "gpiozero", "pip install gpiozero lgpio", "Recommended on Raspberry Pi OS Bookworm and later"),
            Backend("lgpio", "lgpio", "pip install lgpio", "Direct character-device access"),
            Backend("RPi.GPIO", "RPi.GPIO", "pip install RPi.GPIO", "Older Raspberry Pi OS releases"),
        ],
        simulated_note="Simulated GPIO keeps pin state in memory and logs every"
        " write, so a workflow can be tested without a board attached.",
        platform_note="Needs a Raspberry Pi or a board exposing /dev/gpiochip*.",
    ),
    Capability(
        key="serial",
        name="Serial port",
        plain="Talk to an Arduino, motor driver or sensor over USB/UART",
        detail="Opens a serial port, writes lines or raw bytes and reads"
        " replies with a timeout.",
        backends=[Backend("serial", "pyserial", "pip install pyserial")],
        simulated_note="Simulated serial echoes what was written and returns it"
        " on the next read.",
    ),
    Capability(
        key="pointer",
        name="Mouse and keyboard",
        plain="Move the pointer, click, and press keys",
        detail="Lets the network drive the computer it is running on: move"
        " the cursor to a position, click, scroll or type.",
        backends=[
            Backend("pynput", "pynput", "pip install pynput"),
            Backend("pyautogui", "PyAutoGUI", "pip install pyautogui"),
        ],
        simulated_note="Simulated pointer records the movements it would have"
        " made without touching the real cursor.",
        platform_note="Requires a desktop session; headless servers cannot do this.",
    ),
    Capability(
        key="audio",
        name="Microphone",
        plain="Listen: sound level and frequency bands",
        detail="Records a short buffer and reduces it to band energies, which"
        " is the form Johnston's organ subgroups are driven with.",
        backends=[
            Backend("sounddevice", "sounddevice", "pip install sounddevice"),
        ],
        simulated_note="Simulated microphone returns shaped noise.",
    ),
    Capability(
        key="mqtt",
        name="MQTT",
        plain="Publish to a message broker for home/robot automation",
        detail="Publishes a value to an MQTT topic.",
        backends=[Backend("paho.mqtt.client", "paho-mqtt", "pip install paho-mqtt")],
        simulated_note="Simulated MQTT records the publish without connecting.",
    ),
    Capability(
        key="browser",
        name="Headless browser",
        plain="Render a web page to an image without a visible window",
        detail="Loads a URL in a headless browser and screenshots it, so a"
        " workflow can look at a page on a machine with no desktop.",
        backends=[
            Backend("playwright.sync_api", "Playwright", "pip install playwright && playwright install chromium"),
        ],
        simulated_note="Simulated browser returns a placeholder image.",
    ),
]

BY_KEY = {c.key: c for c in CAPABILITIES}
_PROBE_CACHE = {}


def _importable(name):
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def probe(key, refresh=False):
    """Return which backend for a capability is actually usable here."""
    if key not in BY_KEY:
        raise KeyError(f"Unknown device capability: {key}")
    if not refresh and key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    capability = BY_KEY[key]
    found = None
    checked = []
    for backend in capability.backends:
        ok = _importable(backend.module)
        checked.append(
            {
                "module": backend.module,
                "label": backend.label,
                "install": backend.install,
                "note": backend.note,
                "importable": ok,
            }
        )
        if ok and found is None:
            found = backend
    extra = {}
    if key == "gpio":
        chips = sorted(str(p) for p in Path("/dev").glob("gpiochip*"))
        extra["gpio_chips"] = chips
        extra["board_detected"] = bool(chips)
        model = Path("/proc/device-tree/model")
        extra["board_model"] = (
            model.read_text(errors="ignore").strip("\x00\n ") if model.exists() else ""
        )
    if key == "serial":
        extra["ports"] = list_serial_ports()
    if key == "screen" or key == "pointer":
        import os

        extra["display"] = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or ""
        extra["headless"] = not extra["display"] and platform.system() == "Linux"
    result = {
        "key": key,
        "name": capability.name,
        "plain": capability.plain,
        "detail": capability.detail,
        "available": found is not None,
        "backend": found.label if found else None,
        "backend_module": found.module if found else None,
        "install_hint": (
            None if found else "; ".join(b.install for b in capability.backends)
        ),
        "backends": checked,
        "simulated_note": capability.simulated_note,
        "platform_note": capability.platform_note,
        "platform": platform.platform(),
        **extra,
    }
    _PROBE_CACHE[key] = result
    return result


def probe_all(refresh=False):
    return [probe(c.key, refresh) for c in CAPABILITIES]


def list_serial_ports():
    try:
        from serial.tools import list_ports

        return [
            {
                "device": p.device,
                "description": p.description,
                "hwid": p.hwid,
            }
            for p in list_ports.comports()
        ]
    except Exception:
        return [
            {"device": str(p), "description": "character device", "hwid": ""}
            for p in sorted(Path("/dev").glob("tty[UA][SC][BM]*"))
        ]


def environment():
    """A snapshot of the host, for the dashboard and for run provenance."""
    import os
    import sys

    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "compiler": shutil.which("c++") or shutil.which("g++") or "",
        "display": os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or "",
        "capabilities": probe_all(),
    }


class DeviceUnavailable(RuntimeError):
    """Raised when a block needs hardware this machine does not have."""

    def __init__(self, key, extra=""):
        info = probe(key)
        message = (
            f"{info['name']} is not available on this machine. "
            f"Install one of: {info['install_hint']}."
            if not info["available"]
            else f"{info['name']} is present but could not be opened."
        )
        super().__init__(f"{message} {extra}".strip())
        self.key = key
        self.info = info
