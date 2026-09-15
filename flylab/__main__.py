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
    serve.add_argument("--lan", action="store_true",
                       help="Share on this network (the default)")
    serve.add_argument("--local-only", action="store_true",
                       help="This machine only: bind loopback and refuse everything else")
    serve.add_argument("--key", action="store_true",
                       help="Require an access key from other devices on the network")
    serve.add_argument("--no-key", action="store_true",
                       help="Require no access key (the default)")
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
    line = "=" * 68
    share = settings.share_url()
    print(line)
    print("  FLYLAB - connectome workflow platform")
    print(line)
    if share:
        print("  Open from any device on this network:")
        print(f"      {share}")
        for url in settings.urls():
            if url != share and "127.0.0.1" not in url:
                print(f"      {url}")
        print(f"  On this machine:  http://127.0.0.1:{settings.port}/")
    else:
        print(f"  Open on this machine only:  http://127.0.0.1:{settings.port}/")
    print(line)
    if settings.lan_enabled:
        print(f"  Listening on every interface ({settings.host}:{settings.port}).")
        if settings.lan_require_key:
            print(f"  An access key is required from other devices: {settings.access_key}")
        else:
            print("  No access key is required. Anyone who can reach this machine on")
            print("  the network can drive its GPIO pins, move its pointer and start")
            print("  programs on it. Use --local-only, or Settings, to close it down.")
    else:
        print("  Local only: nothing outside this machine can connect.")
        print("  Start with --lan, or use Settings, to share it on this network.")
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
