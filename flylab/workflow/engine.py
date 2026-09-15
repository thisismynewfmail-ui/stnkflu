"""Running a workflow.

The engine walks the run-order wires from Start. When it reaches a block it
resolves that block's value inputs first, pulling from upstream blocks as
needed, then runs it. Loops and branches are handled by the blocks themselves,
which call back into the engine to run their own sub-chains.

A run stops for exactly one of the reasons in ``STOP_REASONS`` and always says
which. Nothing here decides what a workflow should do; it only decides when to
stop trying.
"""

import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import blocks as block_module
from .goals import GoalTracker

STOP_REASONS = (
    "completed",      # the configured number of iterations finished
    "goal",           # a Stop block or a reached goal ended it
    "time",           # the run time limit was hit
    "stopped",        # a person pressed stop
    "error",          # a block raised and the run was set to stop on errors
    "no_start",       # the workflow has no Start block
    "invalid",        # the workflow has errors that prevent a run
)

ZERO = {
    "number": 0.0, "boolean": False, "text": "", "vector": [],
    "image": None, "spikes": None, "record": {}, "any": None, "stimulus": None,
}


@dataclass
class RunSettings:
    iterations: int = 10
    max_seconds: float = 0.0          # 0 = no limit
    interval_seconds: float = 0.0     # minimum gap between iterations
    simulate_devices: bool = False
    stop_on_error: bool = True
    learning: bool = True
    telemetry_every: int = 1
    log_limit: int = 5000
    output_dir: str = ""
    label: str = ""
    notes: str = ""

    def validate(self):
        if self.iterations < 0 or self.iterations > 1_000_000:
            raise ValueError("Iterations must be between 0 (unlimited) and 1,000,000")
        if self.max_seconds < 0 or self.max_seconds > 86400:
            raise ValueError("The time limit must be between 0 (none) and 24 hours")
        if self.interval_seconds < 0 or self.interval_seconds > 3600:
            raise ValueError("The gap between iterations must be between 0 and 3600 seconds")
        if self.telemetry_every < 1:
            raise ValueError("Telemetry interval must be at least 1")
        return self


@dataclass
class RunState:
    run_id: str = ""
    status: str = "idle"  # idle running paused finished failed
    reason: str = ""
    detail: str = ""
    iteration: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    errors: list = field(default_factory=list)

    def json(self):
        return {**asdict(self), "seconds": round((self.finished_at or time.time()) - self.started_at, 2) if self.started_at else 0.0}


class Context:
    """What a block can see and do while it runs."""

    def __init__(self, engine):
        self._engine = engine
        self.session = engine.session
        self.goal = engine.goal
        self.settings = engine.settings
        self.run_id = engine.state.run_id
        self.started_at = engine.state.started_at or time.monotonic()
        self.simulate_devices = engine.settings.simulate_devices
        self.frozen = not engine.settings.learning

    @property
    def iteration(self):
        return self._engine.state.iteration

    def should_stop(self):
        return self._engine.stop_flag.is_set()

    def state(self, node_id):
        return self._engine.node_state.setdefault(node_id, {})

    def device(self, key, factory):
        return self._engine.device(key, factory)

    def value(self, node_id, port):
        return self._engine.resolve(node_id, port)

    def set_output(self, node_id, port, value):
        self._engine.outputs.setdefault(node_id, {})[port] = value

    def run_branch(self, node_id, port):
        return self._engine.run_branch(node_id, port)

    def log(self, record):
        self._engine.log(record)

    def display(self, panel):
        self._engine.display(panel)

    def preview(self, node_id, frame, info=None):
        self._engine.set_preview(node_id, frame, info)

    def after_step(self, result):
        self._engine.after_step(result)

    def save_model(self, label, notes=""):
        return self._engine.save_model(label, notes)

    def resolve_path(self, path):
        return self._engine.resolve_path(path)


class Engine:
    def __init__(self, workflow, session=None, settings=None, on_event=None, output_dir=None):
        self.workflow = workflow
        self.session = session
        self.settings = (settings or RunSettings()).validate()
        self.on_event = on_event or (lambda event: None)
        self.output_dir = Path(output_dir or self.settings.output_dir or "runs/current")
        self.goal = GoalTracker()
        self.state = RunState()
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        self.outputs = {}
        self.node_state = {}
        self.devices = {}
        self.displays = {}
        self.previews = {}
        self.records = []
        self.step_count = 0
        self._epoch = 0
        self._epoch_done = {}
        self._resolving = set()
        self._last_iteration_at = 0.0

    # -------------------------------------------------------------- utilities
    def resolve_path(self, path):
        candidate = Path(str(path))
        return candidate if candidate.is_absolute() else self.output_dir / candidate

    def device(self, key, factory):
        if key not in self.devices:
            self.devices[key] = factory()
        return self.devices[key]

    def close_devices(self):
        for handle in self.devices.values():
            closer = getattr(handle, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        self.devices.clear()

    def log(self, record):
        entry = {"at": time.time(), "iteration": self.state.iteration, **record}
        self.records.append(entry)
        del self.records[: max(0, len(self.records) - self.settings.log_limit)]
        self.emit("log", entry)

    def emit(self, kind, payload):
        try:
            self.on_event({"kind": kind, "run": self.state.run_id, "at": time.time(), **payload})
        except Exception:
            pass

    def display(self, panel):
        self.displays[panel["node"]] = panel
        self.emit("display", {"panels": list(self.displays.values())})

    def set_preview(self, node_id, frame, info=None):
        self.previews[node_id] = {"frame": frame, "info": info or {}, "at": time.time()}

    def after_step(self, result):
        self.step_count += 1
        if self.step_count % max(1, self.settings.telemetry_every) == 0 and self.session is not None:
            self.emit("neural", {
                "viewport": self.session.viewport_frame(),
                "channels": self.session.channel_activity(("input", "output", "modulatory")),
                "step": result.summary(),
            })

    def save_model(self, label, notes=""):
        if self.session is None:
            raise RuntimeError("No connectome is loaded, so there is nothing to save")
        folder = self.output_dir / "models"
        folder.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_ " else "-" for c in str(label))[:60].strip() or "model"
        path = folder / f"{int(time.time())}-{safe.replace(' ', '-')}.npz"
        record = self.session.save(path, label=str(label), notes=str(notes))
        record["path"] = str(path)
        self.emit("model", {"model": record})
        return record

    # ------------------------------------------------------- value resolution
    def resolve(self, node_id, port_key):
        node = self.workflow.nodes.get(node_id)
        if node is None:
            return None
        sources = self.workflow.value_sources(node_id, port_key)
        port = self.workflow._input_port(node_id, port_key)
        if not sources:
            return None
        values = []
        for source_id, source_port in sources:
            values.append(self._output_of(source_id, source_port))
        if port is not None and port.multiple:
            return values
        return values[0]

    def _output_of(self, node_id, port_key):
        node = self.workflow.nodes.get(node_id)
        if node is None or node.type not in block_module.REGISTRY:
            return None
        block = block_module.get(node.type)
        stored = self.outputs.get(node_id, {})
        if not block.pull or node.disabled:
            if port_key in stored:
                return stored[port_key]
            for port in block.outputs:
                if port.key == port_key:
                    return ZERO.get(port.type)
            return None
        if self._epoch_done.get(node_id) != self._epoch and node_id not in self._resolving:
            self._resolving.add(node_id)
            try:
                self._execute(node, record_event=False)
            finally:
                self._resolving.discard(node_id)
        return self.outputs.get(node_id, {}).get(port_key)

    # ------------------------------------------------------------- execution
    def _inputs_for(self, node):
        """Resolve connected inputs only.

        An unconnected port is left out of the dictionary rather than set to
        None, so a block's ``inputs.get(key, its_own_setting)`` falls back to
        the value configured on the block, which is what the interface says
        happens when a port is left empty.
        """
        block = block_module.get(node.type)
        resolved = {}
        for port in block.inputs:
            value = self.resolve(node.id, port.key)
            if value is not None:
                resolved[port.key] = value
        return resolved

    def _execute(self, node, record_event=True):
        block = block_module.get(node.type)
        self._epoch_done[node.id] = self._epoch
        started = time.perf_counter()
        try:
            result = block.run(Context(self), node, self._inputs_for(node))
        except Exception as exc:
            detail = {
                "node": node.id,
                "block": node.type,
                "name": block.name,
                "label": node.label,
                "message": str(exc) or exc.__class__.__name__,
                "type": exc.__class__.__name__,
                "where": traceback.format_exc(limit=3).splitlines()[-3:],
            }
            self.state.errors.append(detail)
            del self.state.errors[:-50]
            self.emit("block_error", {"error": detail})
            if self.settings.stop_on_error:
                raise
            result = block_module.Result(flow="next", record={"error": detail["message"]})
        elapsed = (time.perf_counter() - started) * 1000
        self.outputs.setdefault(node.id, {}).update(result.outputs)
        if record_event:
            self.emit("block", {
                "node": node.id, "block": node.type, "name": block.name,
                "label": node.label, "ms": round(elapsed, 3),
                "record": _plain(result.record), "flow": result.flow if isinstance(result.flow, str) else None,
                "iteration": self.state.iteration,
            })
        return result

    def run_branch(self, node_id, port):
        target = self.workflow.flow_target(node_id, port)
        if not target:
            return "normal"
        return self.run_chain(target)

    def run_chain(self, node_id):
        guard = 0
        while node_id:
            if self.stop_flag.is_set():
                return "stopped"
            while self.pause_flag.is_set() and not self.stop_flag.is_set():
                time.sleep(0.05)
            guard += 1
            if guard > 100000:
                self._request_stop("error", "A run-order chain ran 100,000 blocks without"
                                            " finishing. Check for a loop with no exit.")
                return "stopped"
            node = self.workflow.nodes.get(node_id)
            if node is None:
                return "normal"
            if node.disabled or node.type not in block_module.REGISTRY:
                node_id = self.workflow.flow_target(node_id, "next")
                continue
            self._epoch += 1
            result = self._execute(node)
            if result.stop is not None:
                if result.stop.get("break"):
                    return "break"
                self._request_stop("goal", result.stop.get("reason", "Stop block reached"),
                                   result.stop.get("outcome", "finished"))
                return "stopped"
            if not isinstance(result.flow, str):
                return "normal"
            node_id = self.workflow.flow_target(node.id, result.flow)
        return "normal"

    def _request_stop(self, reason, detail="", outcome=""):
        self.state.reason = reason
        self.state.detail = detail
        if outcome:
            self.state.detail = f"{detail} ({outcome})" if detail else outcome
        self.stop_flag.set()

    def stop(self, detail="Stopped from the interface"):
        self._request_stop("stopped", detail)

    def pause(self, paused=True):
        self.pause_flag.set() if paused else self.pause_flag.clear()
        return self.pause_flag.is_set()

    # -------------------------------------------------------------------- run
    def run(self, run_id=None):
        self.state = RunState(
            run_id=run_id or f"run-{int(time.time())}",
            status="running",
            started_at=time.time(),
        )
        self.stop_flag.clear()
        self.pause_flag.clear()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        problems = self.workflow.check()
        errors = [p for p in problems if p["severity"] == "error"]
        self.emit("check", {"problems": problems})
        if errors:
            self.state.status = "failed"
            self.state.reason = "invalid"
            self.state.detail = errors[0]["message"]
            self.emit("finished", {"state": self.state.json(), "goal": self.goal.summary()})
            return self.state
        start = self.workflow.start_node()
        if start is None:
            self.state.status = "failed"
            self.state.reason = "no_start"
            self.state.detail = "This workflow has no Start block."
            self.emit("finished", {"state": self.state.json(), "goal": self.goal.summary()})
            return self.state
        if self.session is not None:
            self.session.brain.weights_frozen = not self.settings.learning
        deadline = (time.time() + self.settings.max_seconds) if self.settings.max_seconds else None
        self.emit("started", {"state": self.state.json(), "settings": asdict(self.settings)})
        try:
            while not self.stop_flag.is_set():
                if self.settings.iterations and self.state.iteration >= self.settings.iterations:
                    self._request_stop("completed", f"Finished {self.state.iteration} iterations")
                    break
                if deadline and time.time() >= deadline:
                    self._request_stop("time", f"Reached the {self.settings.max_seconds:g} second limit")
                    break
                gap = self.settings.interval_seconds
                if gap and self._last_iteration_at:
                    remaining = gap - (time.monotonic() - self._last_iteration_at)
                    while remaining > 0 and not self.stop_flag.is_set():
                        time.sleep(min(0.05, remaining))
                        remaining = gap - (time.monotonic() - self._last_iteration_at)
                    if self.stop_flag.is_set():
                        break
                self._last_iteration_at = time.monotonic()
                self.state.iteration += 1
                if self.session is not None:
                    self.session.clear_stimuli()
                self.emit("iteration", {"iteration": self.state.iteration,
                                        "goal": self.goal.summary()})
                self.run_chain(start.id)
                if self.goal.reached and not self.stop_flag.is_set():
                    self._request_stop("goal", "The goal was reached")
            self.state.status = "finished"
        except Exception as exc:
            self.state.status = "failed"
            self.state.reason = "error"
            self.state.detail = f"{type(exc).__name__}: {exc}"
        finally:
            self.state.finished_at = time.time()
            self.close_devices()
            if not self.state.reason:
                self.state.reason = "completed"
            summary = {
                "state": self.state.json(),
                "goal": self.goal.summary(),
                "iterations": self.state.iteration,
                "neural_steps": self.step_count,
                "records": len(self.records),
                "errors": list(self.state.errors[-10:]),
                "synthetic_fixture": bool(self.session and self.session.synthetic_fixture),
            }
            self.emit("finished", summary)
        return self.state

    def summary(self):
        return {
            "state": self.state.json(),
            "goal": self.goal.summary(),
            "displays": list(self.displays.values()),
            "neural_steps": self.step_count,
            "records": len(self.records),
            "synthetic_fixture": bool(self.session and self.session.synthetic_fixture),
        }


def _plain(value, depth=0):
    """Make a block's record safe to send over the wire."""
    if depth > 4:
        return "..."
    if isinstance(value, np.ndarray):
        return value.tolist() if value.size <= 64 else {
            "array": list(value.shape), "mean": float(value.mean()), "max": float(value.max())
        }
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, dict):
        return {str(k): _plain(v, depth + 1) for k, v in list(value.items())[:64]}
    if isinstance(value, (list, tuple)):
        return [_plain(v, depth + 1) for v in list(value)[:64]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
