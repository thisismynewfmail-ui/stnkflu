#!/usr/bin/env python3
"""Start FLYLAB.

    python3 startup.py

Checks that the machine can run the platform, reports anything missing in
plain language, and starts the interface.

The interface is shared on your local network by default: it listens on every
interface, and the start-up banner prints the address to open from a phone or
a laptop. That also means anyone who can reach this machine can drive its GPIO
pins, move its pointer and start programs on it. Use --local-only to keep it to
this machine, or --key to require an access key from other devices.

    python3 startup.py --local-only   this machine only
    python3 startup.py --key          require an access key over the network
    python3 startup.py --port 9000    listen somewhere else
    python3 startup.py --check        run the checks and stop
    python3 startup.py --fixture      build the synthetic bench fixture and start
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED = [
    ("numpy", "numpy", "Array maths for the whole simulator"),
    ("pandas", "pandas", "Reading the connectome's annotation tables"),
    ("pyarrow", "pyarrow", "Reading the released Feather files"),
    ("PIL", "Pillow", "Encoding pictures for the interface"),
    ("fastapi", "fastapi", "The local web service"),
    ("uvicorn", "uvicorn", "Serving that web service"),
    ("pydantic", "pydantic", "Checking values coming from the interface"),
]
OPTIONAL = [
    ("mss", "mss", "Screen capture"),
    ("cv2", "opencv-python-headless", "Camera and video files"),
    ("serial", "pyserial", "Serial ports"),
    ("gpiozero", "gpiozero", "Raspberry Pi GPIO pins"),
    ("pynput", "pynput", "Moving the mouse and pressing keys"),
    ("sounddevice", "sounddevice", "Microphone input"),
    ("paho.mqtt.client", "paho-mqtt", "Publishing to an MQTT broker"),
]
BAR = "=" * 68


def importable(name):
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def report(checks):
    print(BAR)
    print("  FLYLAB start-up checks")
    print(BAR)
    ok = True
    version = sys.version_info
    good_python = version >= (3, 11)
    ok = ok and good_python
    print(f"  [{'ok ' if good_python else 'X  '}] Python {version.major}.{version.minor}.{version.micro}"
          f"{'' if good_python else '   needs 3.11 or newer'}")
    compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    print(f"  [{'ok ' if compiler else 'X  '}] C++ compiler"
          f"{'   ' + compiler if compiler else '   needed once, to build the simulation kernel'}")
    ok = ok and bool(compiler)
    print(f"  [ok ] {platform.system()} {platform.machine()}")
    missing = []
    for module, package, why in REQUIRED:
        present = importable(module)
        ok = ok and present
        if not present:
            missing.append(package)
        print(f"  [{'ok ' if present else 'X  '}] {package:<12} {why}")
    print("  " + "-" * 64)
    print("  Optional, for hardware. Everything below can be added later:")
    for module, package, why in OPTIONAL:
        present = importable(module)
        print(f"  [{'ok ' if present else '   '}] {package:<24} {why}")
    print(BAR)
    if missing:
        print("  Missing required packages. Install them with:")
        print(f"    {sys.executable} -m pip install {' '.join(missing)}")
        print("  or install everything at once:")
        print(f"    {sys.executable} -m pip install -r requirements.txt")
        print(BAR)
    if not compiler:
        print("  No C++ compiler found. Install one:")
        print("    Debian/Ubuntu/Raspberry Pi OS:  sudo apt install build-essential")
        print("    Fedora:                         sudo dnf install gcc-c++")
        print("    macOS:                          xcode-select --install")
        print(BAR)
    return ok


def main():
    parser = argparse.ArgumentParser(
        description="Start FLYLAB", formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--port", type=int, help="Port to listen on (default 8765)")
    parser.add_argument("--lan", action="store_true",
                        help="Share on this network (the default)")
    parser.add_argument("--local-only", action="store_true",
                        help="This machine only: bind loopback and refuse everything else")
    parser.add_argument("--key", action="store_true",
                        help="Require an access key from other devices on the network")
    parser.add_argument("--no-key", action="store_true",
                        help="Require no access key (the default)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    parser.add_argument("--workspace", type=Path, help="Where projects, models and settings live")
    parser.add_argument("--data", type=Path, help="Dataset directory to use")
    parser.add_argument("--check", action="store_true", help="Run the checks and stop")
    parser.add_argument("--fixture", action="store_true",
                        help="Build the synthetic bench fixture first, then start")
    parser.add_argument("--install", action="store_true",
                        help="Install the required packages, then start")
    args = parser.parse_args()

    healthy = report([])
    if args.install:
        print("  Installing required packages...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
            check=False,
        )
        healthy = report([])
    if args.check:
        return 0 if healthy else 1
    if not healthy:
        print("  Not starting: fix the items marked X above first.")
        print(f"  Re-run the checks any time with:  {sys.executable} startup.py --check")
        return 1

    sys.path.insert(0, str(ROOT))
    from flylab import datasets as dataset_module
    from flylab import settings as settings_module
    from flylab.__main__ import run_server

    settings = settings_module.load(args.workspace)
    if args.port:
        settings.port = args.port
    # A flag on the command line is a deliberate choice, so it is recorded as
    # one and survives any later change to the shipped default.
    if args.lan:
        settings.lan_enabled = True
        settings.network_chosen = True
    if args.local_only:
        settings.lan_enabled = False
        settings.network_chosen = True
    if args.key:
        settings.lan_require_key = True
        settings.network_chosen = True
    if args.no_key:
        settings.lan_require_key = False
        settings.network_chosen = True
    if args.no_browser:
        settings.open_browser = False
    if args.data:
        settings.dataset_dir = str(Path(args.data).resolve())
    if args.fixture:
        target = Path(settings.workspace) / "datasets" / "bench-fixture"
        print(f"  Building the synthetic bench fixture in {target} ...")
        manifest = dataset_module.make_fixture(target)["manifest"]
        print(f"  Fixture ready: {manifest['neurons']:,} cells, {manifest['edges']:,} connections.")
        print("  This is a randomly wired test graph, not a connectome.")
        settings.dataset_dir = str(target)
    settings_module.save(settings.validate())
    if not settings.dataset_dir:
        print("  No dataset chosen yet. Open Housekeeping in the interface to")
        print("  download the connectome, or start with --fixture to try the")
        print("  software on a small synthetic test graph first.")
        print(BAR)
    os.environ.setdefault("FLYLAB_DATA", settings.dataset_dir or "data")
    return run_server(settings)


if __name__ == "__main__":
    raise SystemExit(main())
