"""Application settings, saved in the workspace.

Network exposure is the only setting here with a consequence outside this
machine, so it is the one stated plainly. FLYLAB listens on every interface by
default and is reachable at this machine's own address, because the usual place
to run it is a headless Raspberry Pi or a workshop machine driven from a laptop
on the same network.

That openness is real: this interface can drive GPIO pins, move the pointer and
start programs on the host, so anyone who can reach the port can do those
things. ``--local-only`` at start-up, or the Settings page, closes it back down
to loopback, and an access key can be required on top. Both the socket binding
and a per-request check follow whichever choice is in force.
"""

import json
import os
import secrets
import socket
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_WORKSPACE = Path(os.environ.get("FLYLAB_HOME", Path.home() / ".flylab"))
LOOPBACK = ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1")

# Bumped when a shipped default changes in a way a saved file should adopt.
# A file written before a bump takes the new default for the fields that bump
# covers, once, unless it records that someone set them by hand.
SETTINGS_VERSION = 2


@dataclass
class AppSettings:
    # --- housekeeping
    version: int = SETTINGS_VERSION
    # --- where things live
    workspace: str = str(DEFAULT_WORKSPACE)
    dataset_dir: str = ""
    # --- network
    port: int = 8765
    lan_enabled: bool = True
    lan_require_key: bool = False
    access_key: str = ""
    network_chosen: bool = False  # set once someone changes the two above
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
        if self.lan_require_key and not self.access_key:
            self.access_key = secrets.token_urlsafe(18)
        return self

    @property
    def host(self):
        """The address the server binds to.

        0.0.0.0 accepts connections on every interface this machine has, which
        is what makes the interface reachable at the machine's own address as
        well as at 127.0.0.1.
        """
        return "0.0.0.0" if self.lan_enabled else "127.0.0.1"

    @property
    def open_to_network(self):
        return bool(self.lan_enabled) and not bool(self.lan_require_key)

    def key_suffix(self):
        return f"?key={self.access_key}" if (self.lan_require_key and self.access_key) else ""

    def urls(self):
        """Addresses this interface answers on, most shareable first."""
        out = []
        if self.lan_enabled:
            suffix = self.key_suffix()
            out += [f"http://{address}:{self.port}/{suffix}" for address in local_addresses()]
        out.append(f"http://127.0.0.1:{self.port}/")
        return out

    def share_url(self):
        """The address to hand to another device, or None when local-only."""
        for url in self.urls():
            if "127.0.0.1" not in url:
                return url
        return None

    def json(self):
        data = asdict(self)
        data["host"] = self.host
        data["urls"] = self.urls()
        data["share_url"] = self.share_url()
        data["open_to_network"] = self.open_to_network
        data["addresses"] = local_addresses()
        # The key is shown deliberately: whoever can already read this response
        # is either on loopback or has presented the key.
        return data


def local_addresses():
    """Every address this machine answers on, the outbound one first.

    The address a router would reach this machine at is the one worth putting
    first, because it is the one to type into a phone or a laptop. It is found
    by asking the kernel which local address it would use to reach an outside
    host. No packet is sent, and the address used for the question is from the
    documentation range, which is never routed anywhere.
    """
    found = []

    def add(address):
        text = str(address).split("%")[0]
        if text and text not in found and not text.startswith("127.") and text != "::1":
            found.append(text)

    for family, probe in ((socket.AF_INET, "192.0.2.1"), (socket.AF_INET6, "2001:db8::1")):
        try:
            sock = socket.socket(family, socket.SOCK_DGRAM)
            try:
                sock.connect((probe, 1))
                add(sock.getsockname()[0])
            finally:
                sock.close()
        except OSError:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            add(info[4][0])
    except OSError:
        pass
    return found


def settings_path(workspace=None):
    return Path(workspace or DEFAULT_WORKSPACE) / "settings.json"


def migrate(values):
    """Bring a settings file written by an older version up to date.

    Version 2 made the interface reachable on the local network by default. A
    file from before that predates the choice, so it adopts the new default -
    unless it records that someone set the network toggles by hand, in which
    case their choice stands.
    """
    stored = int(values.get("version", 1) or 1)
    if stored < 2 and not values.get("network_chosen"):
        values["lan_enabled"] = True
        values["lan_require_key"] = False
    values["version"] = SETTINGS_VERSION
    return values


def load(workspace=None):
    path = settings_path(workspace)
    values = {}
    if path.exists():
        try:
            values = json.loads(path.read_text())
        except (ValueError, OSError):
            values = {}
    values = migrate(values)
    known = {f.name for f in fields(AppSettings)}
    settings = AppSettings(**{k: v for k, v in values.items() if k in known})
    if workspace:
        settings.workspace = str(Path(workspace))
    return settings.validate()


def save(settings, workspace=None):
    settings.validate()
    settings.version = SETTINGS_VERSION
    path = settings_path(workspace or settings.workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(asdict(settings), indent=2) + "\n")
    temporary.replace(path)
    return settings


def is_loopback(address):
    return str(address or "").split("%")[0] in LOOPBACK
