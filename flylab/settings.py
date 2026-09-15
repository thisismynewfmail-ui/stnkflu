"""Application settings, saved in the workspace.

Network exposure is the only setting here with a consequence outside this
machine, so it gets the most care: the interface can drive GPIO pins, move the
mouse and run programs, which means anyone who can reach it can do those
things. Local-only is the default, turning on local-network access generates
an access key, and both the socket binding and a request check enforce the
current choice.
"""

import json
import os
import secrets
import socket
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_WORKSPACE = Path(os.environ.get("FLYLAB_HOME", Path.home() / ".flylab"))
LOOPBACK = ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1")


@dataclass
class AppSettings:
    # --- where things live
    workspace: str = str(DEFAULT_WORKSPACE)
    dataset_dir: str = ""
    # --- network
    port: int = 8765
    lan_enabled: bool = False
    lan_require_key: bool = True
    access_key: str = ""
    open_browser: bool = True
    # --- defaults for new sessions
    compartments: str = "alpha1_gamma1pedc"
    learning: bool = True
    step_ms: float = 500.0
    teach_pulse_ms: float = 200.0
    teach_current_mv: float = 20.0
    neural_bin_ms: float = 10.0
    lamina_bias: float = 12.0
    viewport_cells: int = 3600
    # --- defaults for new runs
    iterations: int = 10
    max_seconds: float = 0.0
    interval_seconds: float = 0.0
    simulate_devices: bool = True
    stop_on_error: bool = True
    telemetry_every: int = 1
    # --- interface
    theme: str = "aperture"
    show_tips: bool = True
    reduce_motion: bool = False
    grid_snap: int = 10
    autosave: bool = True
    recent_projects: list = field(default_factory=list)

    def validate(self):
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        if self.theme not in ("aperture", "night", "paper"):
            raise ValueError("Unknown theme")
        if not 1 <= int(self.grid_snap) <= 100:
            raise ValueError("Grid snap must be between 1 and 100")
        if self.lan_enabled and self.lan_require_key and not self.access_key:
            self.access_key = secrets.token_urlsafe(18)
        return self

    @property
    def host(self):
        return "0.0.0.0" if self.lan_enabled else "127.0.0.1"

    def urls(self):
        out = [f"http://127.0.0.1:{self.port}/"]
        if self.lan_enabled:
            suffix = f"?key={self.access_key}" if (self.lan_require_key and self.access_key) else ""
            for address in local_addresses():
                out.append(f"http://{address}:{self.port}/{suffix}")
        return out

    def json(self):
        data = asdict(self)
        data["host"] = self.host
        data["urls"] = self.urls()
        # The key is shown deliberately: whoever can already read this response
        # is either on loopback or has presented the key.
        return data


def local_addresses():
    """Every address this machine answers on, for the local-network URLs."""
    found = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            address = info[4][0]
            if address not in found and not address.startswith("127."):
                found.append(address)
    except OSError:
        pass
    if not found:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("192.0.2.1", 1))  # documentation address, never routed
            found.append(probe.getsockname()[0])
            probe.close()
        except OSError:
            pass
    return found


def settings_path(workspace=None):
    return Path(workspace or DEFAULT_WORKSPACE) / "settings.json"


def load(workspace=None):
    path = settings_path(workspace)
    values = {}
    if path.exists():
        try:
            values = json.loads(path.read_text())
        except (ValueError, OSError):
            values = {}
    known = {f.name for f in fields(AppSettings)}
    settings = AppSettings(**{k: v for k, v in values.items() if k in known})
    if workspace:
        settings.workspace = str(Path(workspace))
    return settings.validate()


def save(settings, workspace=None):
    settings.validate()
    path = settings_path(workspace or settings.workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(asdict(settings), indent=2) + "\n")
    temporary.replace(path)
    return settings


def is_loopback(address):
    return str(address or "").split("%")[0] in LOOPBACK
