"""Command line entry point: ``python -m flylab``.

``startup.py`` in the repository root is the friendly front door; this is the
same thing without the dependency checking, for use inside scripts and
service units.
"""

import argparse
import json
import sys
from pathlib import Path

from . import datasets as dataset_module
from . import settings as settings_module


def main(argv=None):
    parser = argparse.ArgumentParser(prog="flylab", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the interface (the default)")
    serve.add_argument("--port", type=int, help="Port to listen on")
    serve.add_argument("--lan", action="store_true", help="Allow other machines on this network to connect")
    serve.add_argument("--local-only", action="store_true", help="Force local-only access for this run")
    serve.add_argument("--no-key", action="store_true", help="Do not require an access key on the local network")
    serve.add_argument("--no-browser", action="store_true", help="Do not open a browser window")
    serve.add_argument("--workspace", type=Path, help="Where projects, models and settings live")
    serve.add_argument("--data", type=Path, help="Dataset directory to load at start-up")

    prepare = sub.add_parser("prepare", help="Download and compile the connectome")
    prepare.add_argument("path", type=Path, nargs="?", help="Where to put it")
    prepare.add_argument("--reuse", type=Path, help="Copy verified files from an existing dataset directory")

    fixture = sub.add_parser("fixture", help="Build the synthetic bench fixture for testing")
    fixture.add_argument("path", type=Path, nargs="?", default=Path("data-fixture"))
    fixture.add_argument("--seed", type=int, default=20260915)

    check = sub.add_parser("verify", help="Re-check a prepared dataset")
    check.add_argument("path", type=Path, nargs="?")

    sub.add_parser("devices", help="Report what hardware this machine can reach")

    args = parser.parse_args(argv)
    command = args.command or "serve"

    if command == "prepare":
        target = args.path or Path(settings_module.load().workspace) / "datasets" / "malecns-v1"

        def progress(event):
            print(json.dumps(event), flush=True)

        print(json.dumps(dataset_module.prepare(target, progress, args.reuse), indent=2))
        return 0

    if command == "fixture":
        print(json.dumps(dataset_module.make_fixture(args.path, args.seed)["manifest"], indent=2))
        return 0

    if command == "verify":
        target = args.path or settings_module.load().dataset_dir
        if not target:
            print("Nothing to verify: no dataset has been chosen yet.", file=sys.stderr)
            return 1
        print(json.dumps(dataset_module.verify(target), indent=2))
        return 0

    if command == "devices":
        from .io.registry import environment

        print(json.dumps(environment(), indent=2))
        return 0

    settings = settings_module.load(args.workspace)
    if args.port:
        settings.port = args.port
    if args.lan:
        settings.lan_enabled = True
    if args.local_only:
        settings.lan_enabled = False
    if args.no_key:
        settings.lan_require_key = False
    if args.no_browser:
        settings.open_browser = False
    if args.data:
        settings.dataset_dir = str(Path(args.data).resolve())
    settings_module.save(settings.validate())
    return run_server(settings)


def run_server(settings):
    import uvicorn

    from .server import build_app

    app = build_app(settings)
    banner(settings)
    if settings.open_browser:
        open_browser_soon(f"http://127.0.0.1:{settings.port}/")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning",
                access_log=False)
    return 0


def banner(settings):
    line = "=" * 64
    print(line)
    print("  FLYLAB - connectome workflow platform")
    print(line)
    for url in settings.urls():
        print(f"  {url}")
    if settings.lan_enabled:
        print("  Reachable from this network. This interface can drive GPIO pins,")
        print("  move the pointer and start programs on this machine.")
        if settings.lan_require_key:
            print(f"  Access key: {settings.access_key}")
        else:
            print("  WARNING: no access key is required.")
    else:
        print("  Local only. Turn on network access in Settings to reach it from")
        print("  another device.")
    print(f"  Workspace: {settings.workspace}")
    print(line, flush=True)


def open_browser_soon(url, delay=1.2):
    import threading
    import webbrowser

    def go():
        import time

        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=go, daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())
