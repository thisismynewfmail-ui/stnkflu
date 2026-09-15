"""The local web service behind the interface.

One process, one dataset, one run at a time. The browser talks to it over
plain HTTP for everything that changes state, and over a WebSocket for live
telemetry while a run is going.

Access is local-only unless local-network access is turned on in Settings, and
turning that on generates an access key. The check runs on every request and
every socket, not only at bind time, because this interface can move a mouse,
drive GPIO pins and start programs on the machine hosting it.
"""

import asyncio
import contextlib
import json
import threading
import time
import traceback
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import datasets as dataset_module
from . import settings as settings_module
from .io import registry as device_registry
from .neural import atlas as atlas_module
from .neural.runtime import BrainSession, RuntimeConfig
from .store import Workspace
from .workflow import blocks as block_module
from .workflow import templates
from .workflow.engine import Engine, RunSettings
from .workflow.graph import Workflow

WEB = Path(__file__).with_name("web")
VERSION = "1.0.0"


class Hub:
    """Fans telemetry out to every connected browser."""

    def __init__(self):
        self.clients = set()
        self.pending = deque(maxlen=4096)
        self.latest = {}
        self.lock = threading.Lock()
        self.loop = None

    def publish(self, event):
        kind = event.get("kind")
        with self.lock:
            # Only the newest frame of a high-rate stream is worth delivering.
            if kind in ("neural", "progress"):
                self.latest[kind] = event
            else:
                self.pending.append(event)

    def drain(self):
        with self.lock:
            events = list(self.pending)
            self.pending.clear()
            events.extend(self.latest.values())
            self.latest.clear()
        return events

    async def broadcast(self, event):
        message = json.dumps(event, default=str)
        for client in list(self.clients):
            try:
                await client.send_text(message)
            except Exception:
                self.clients.discard(client)

    async def pump(self):
        while True:
            for event in self.drain():
                await self.broadcast(event)
            await asyncio.sleep(0.05)


class Service:
    """Everything the routes act on."""

    def __init__(self, settings):
        self.settings = settings
        self.workspace = Workspace(settings.workspace)
        self.hub = Hub()
        self.session = None
        self.session_info = {}
        self.engine = None
        self.thread = None
        self.run_project = ""
        self.run_summary = {}
        self.task = {"name": "", "status": "idle", "detail": "", "progress": 0.0}
        self.started_at = time.time()
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ state
    def state(self):
        running = bool(self.thread and self.thread.is_alive())
        return {
            "version": VERSION,
            "settings": self.settings.json(),
            "workspace": self.workspace.summary(),
            "session": self.session_info,
            "session_loaded": self.session is not None,
            "run": {
                "active": running,
                "project": self.run_project,
                "paused": bool(self.engine and self.engine.pause_flag.is_set()),
                **(self.engine.summary() if self.engine else {}),
            },
            "task": dict(self.task),
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "datasets": dataset_module.discover(
                self.settings.workspace,
                [self.settings.dataset_dir] if self.settings.dataset_dir else [],
            ),
            "devices": device_registry.probe_all(),
            "host": device_registry.environment(),
        }

    def set_task(self, name, status, detail="", progress=0.0):
        self.task = {"name": name, "status": status, "detail": detail,
                     "progress": round(float(progress), 4), "at": time.time()}
        self.hub.publish({"kind": "task", **self.task})

    # ---------------------------------------------------------------- session
    def load_session(self, dataset_dir, **overrides):
        root = Path(dataset_dir).resolve()
        if not (root / "graph.npz").exists():
            raise ValueError(
                f"{root} has no compiled graph. Use Housekeeping to download and"
                " prepare a dataset, or build the bench fixture."
            )
        self.set_task("Load connectome", "working", str(root), 0.1)
        report = dataset_module.verify(root)
        config = RuntimeConfig(
            data_dir=str(root),
            compartments=overrides.get("compartments", self.settings.compartments),
            learning=bool(overrides.get("learning", self.settings.learning)),
            default_step_ms=float(overrides.get("step_ms", self.settings.step_ms)),
            teach_pulse_ms=float(overrides.get("teach_pulse_ms", self.settings.teach_pulse_ms)),
            teach_current_mv=float(overrides.get("teach_current_mv", self.settings.teach_current_mv)),
            neural_bin_ms=float(overrides.get("neural_bin_ms", self.settings.neural_bin_ms)),
            lamina_bias=float(overrides.get("lamina_bias", self.settings.lamina_bias)),
            viewport_cells=int(overrides.get("viewport_cells", self.settings.viewport_cells)),
        )
        self.set_task("Load connectome", "working", "Compiling the kernel and indexing cells", 0.4)
        session = BrainSession(config)
        self.session = session
        self.session_info = {
            "dataset": report,
            "catalogue": session.catalogue(),
            "loaded_at": time.time(),
            "synthetic_fixture": session.synthetic_fixture,
        }
        self.settings.dataset_dir = str(root)
        settings_module.save(self.settings)
        self.set_task("Load connectome", "done", f"{session.brain.n:,} cells ready", 1.0)
        self.hub.publish({"kind": "session", "session": self.session_info})
        return self.session_info

    def unload_session(self):
        self.session = None
        self.session_info = {}
        self.set_task("Unload connectome", "done", "Memory released", 1.0)
        return {"unloaded": True}

    # -------------------------------------------------------------------- run
    def start_run(self, project_id, settings_override=None):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("A run is already going. Stop it before starting another.")
            project = self.workspace.get_project(project_id)
            workflow = Workflow.load(project["workflow"])
            merged = {
                "iterations": self.settings.iterations,
                "max_seconds": self.settings.max_seconds,
                "interval_seconds": self.settings.interval_seconds,
                "simulate_devices": self.settings.simulate_devices,
                "stop_on_error": self.settings.stop_on_error,
                "learning": self.settings.learning,
                "telemetry_every": self.settings.telemetry_every,
                **(project.get("run_settings") or {}),
                **(settings_override or {}),
            }
            known = set(RunSettings.__dataclass_fields__)
            run_settings = RunSettings(**{k: v for k, v in merged.items() if k in known})
            run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}"
            output = self.workspace.run_dir(project_id, run_id)
            run_settings.output_dir = str(output)
            engine = Engine(workflow, self.session, run_settings, self.hub.publish, output)
            self.engine = engine
            self.run_project = project_id
            self.run_summary = {}

            def worker():
                try:
                    state = engine.run(run_id)
                    summary = {
                        "run_id": run_id, "state": state.json(),
                        "goal": engine.goal.summary(),
                        "iterations": state.iteration,
                        "neural_steps": engine.step_count,
                        "output_dir": str(output),
                        "synthetic_fixture": bool(self.session and self.session.synthetic_fixture),
                    }
                    self.run_summary = summary
                    self.workspace.record_run(project_id, summary)
                    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
                    if engine.records:
                        with (output / "log.jsonl").open("w") as handle:
                            for record in engine.records:
                                handle.write(json.dumps(record, default=str) + "\n")
                    if self.session is not None:
                        (output / "provenance.json").write_text(
                            json.dumps(self.session.provenance(), indent=2, default=str) + "\n"
                        )
                except Exception as exc:
                    self.hub.publish({
                        "kind": "run_error",
                        "message": f"{type(exc).__name__}: {exc}",
                        "trace": traceback.format_exc(limit=4).splitlines()[-4:],
                    })

            self.thread = threading.Thread(target=worker, name=f"flylab-{run_id}", daemon=True)
            self.thread.start()
            return {"run_id": run_id, "output_dir": str(output),
                    "settings": run_settings.__dict__}

    def stop_run(self):
        if self.engine:
            self.engine.stop()
        return {"stopping": True}

    def pause_run(self, paused=True):
        if not self.engine:
            raise ValueError("No run is going")
        return {"paused": self.engine.pause(paused)}


def build_app(settings=None):
    settings = settings or settings_module.load()
    service = Service(settings)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        service.hub.loop = asyncio.get_running_loop()
        pump = asyncio.create_task(service.hub.pump())
        try:
            yield
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump

    app = FastAPI(title="FLYLAB", version=VERSION, docs_url=None, redoc_url=None,
                  lifespan=lifespan)
    app.state.service = service

    @app.middleware("http")
    async def guard(request: Request, call_next):
        client = request.client.host if request.client else ""
        current = service.settings
        if not settings_module.is_loopback(client):
            if not current.lan_enabled:
                return JSONResponse(
                    {"error": "This FLYLAB is set to local only.",
                     "detail": "It was started with --local-only, or network sharing"
                               " was turned off in Settings. Turn on 'Share on this"
                               " network' on the machine running it, or restart it"
                               " without --local-only."},
                    status_code=403,
                )
            if current.lan_require_key:
                given = (
                    request.query_params.get("key")
                    or request.headers.get("x-flylab-key")
                    or request.cookies.get("flylab_key", "")
                )
                if given != current.access_key:
                    return JSONResponse(
                        {"error": "Access key required.",
                         "detail": "Settings on the host machine shows the key and the"
                                   " full address to use."},
                        status_code=401,
                    )
        response = await call_next(request)
        key = request.query_params.get("key")
        if key and key == current.access_key and request.url.path == "/":
            response.set_cookie("flylab_key", key, max_age=86400, samesite="lax")
        return response

    # ------------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    async def index():
        page = WEB / "index.html"
        if not page.exists():
            return HTMLResponse("<h1>FLYLAB</h1><p>Interface files are missing.</p>", 500)
        return HTMLResponse(page.read_text())

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

    @app.get("/favicon.svg")
    async def favicon():
        path = WEB / "favicon.svg"
        if path.exists():
            return FileResponse(path, media_type="image/svg+xml")
        raise HTTPException(404)

    # -------------------------------------------------------------------- api
    def ok(payload):
        return JSONResponse(payload)

    @app.get("/api/state")
    async def api_state():
        return ok(service.state())

    @app.get("/api/catalogue")
    async def api_catalogue():
        return ok({
            "blocks": block_module.catalogue(),
            "templates": templates.catalogue(),
            "modalities": atlas_module.MODALITIES,
            "fidelity": atlas_module.FIDELITY,
            "dataset_steps": [
                {"key": k, "label": l, "detail": d} for k, l, d in dataset_module.STEPS
            ],
            "run_reasons": {
                "completed": "The configured number of iterations finished.",
                "goal": "A Stop block ran, or the goal was reached.",
                "time": "The run time limit was reached.",
                "stopped": "Someone pressed Stop.",
                "error": "A block failed and the run was set to stop on errors.",
                "no_start": "The workflow has no Start block.",
                "invalid": "The workflow has errors that prevent a run.",
            },
        })

    @app.get("/api/devices")
    async def api_devices(refresh: bool = False):
        return ok({"devices": device_registry.probe_all(refresh),
                   "host": device_registry.environment()})

    @app.post("/api/settings")
    async def api_settings(request: Request):
        body = await request.json()
        current = service.settings
        changed_network = False
        for key, value in body.items():
            if not hasattr(current, key) or key in ("workspace", "version"):
                continue
            if key in ("lan_enabled", "lan_require_key", "port"):
                changed_network = changed_network or getattr(current, key) != value
            if key in ("lan_enabled", "lan_require_key"):
                # Someone set this by hand, so it outlives any later change to
                # the shipped default.
                current.network_chosen = True
            setattr(current, key, value)
        if body.get("regenerate_key"):
            current.access_key = ""
            changed_network = True
        try:
            current.validate()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        settings_module.save(current)
        service.hub.publish({"kind": "settings", "settings": current.json()})
        return ok({
            "settings": current.json(),
            "restart_required": changed_network,
            "note": "In force for every request from now on. The port and the"
                    " listening address itself only change when FLYLAB restarts."
            if changed_network else "",
        })

    # -------------------------------------------------------------- datasets
    @app.get("/api/datasets")
    async def api_datasets():
        return ok({
            "datasets": dataset_module.discover(
                service.settings.workspace,
                [service.settings.dataset_dir] if service.settings.dataset_dir else [],
            ),
            "sources": dataset_module.sources(),
            "steps": [{"key": k, "label": l, "detail": d} for k, l, d in dataset_module.STEPS],
        })

    @app.post("/api/datasets/fixture")
    async def api_fixture(request: Request):
        body = await request.json() if await request.body() else {}
        target = Path(body.get("path") or service.workspace.dataset_dir("bench-fixture"))
        try:
            result = dataset_module.make_fixture(target, int(body.get("seed", 20260915)))
        except Exception as exc:
            raise HTTPException(400, str(exc)) from None
        service.set_task("Bench fixture", "done", str(target), 1.0)
        return ok(result)

    @app.post("/api/datasets/prepare")
    async def api_prepare(request: Request):
        body = await request.json() if await request.body() else {}
        target = Path(body.get("path") or service.workspace.dataset_dir("malecns-v1"))
        reuse = body.get("reuse") or None
        if service.task.get("status") == "working":
            raise HTTPException(409, "Another housekeeping task is already running.")

        def worker():
            total = sum(v["bytes"] for v in dataset_module.sources().values())

            def progress(event):
                read = event.get("read", 0)
                fraction = min(0.9, read / total) if event.get("step") == "download" and total else None
                service.set_task(
                    "Prepare dataset",
                    "working",
                    f"{event.get('step', '')}: {event.get('file', event.get('status', ''))}",
                    fraction if fraction is not None else service.task.get("progress", 0.0),
                )
                service.hub.publish({"kind": "progress", "detail": event})

            try:
                report = dataset_module.prepare(target, progress, reuse)
                service.set_task("Prepare dataset", "done",
                                 f"{report['neurons']:,} cells, {report['directed_edges']:,} connections", 1.0)
                service.hub.publish({"kind": "dataset", "report": report})
            except Exception as exc:
                service.set_task("Prepare dataset", "failed", f"{type(exc).__name__}: {exc}", 0.0)

        threading.Thread(target=worker, name="flylab-prepare", daemon=True).start()
        return ok({"started": True, "path": str(target)})

    @app.post("/api/datasets/verify")
    async def api_verify(request: Request):
        body = await request.json() if await request.body() else {}
        target = body.get("path") or service.settings.dataset_dir
        if not target:
            raise HTTPException(400, "Choose a dataset to verify.")
        try:
            return ok(dataset_module.verify(target))
        except Exception as exc:
            raise HTTPException(400, str(exc)) from None

    # --------------------------------------------------------------- session
    @app.post("/api/session/load")
    async def api_session_load(request: Request):
        body = await request.json() if await request.body() else {}
        target = body.pop("path", None) or service.settings.dataset_dir
        if not target:
            raise HTTPException(400, "Choose a dataset first.")
        try:
            return ok(await asyncio.to_thread(service.load_session, target, **body))
        except Exception as exc:
            service.set_task("Load connectome", "failed", str(exc), 0.0)
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/session/unload")
    async def api_session_unload():
        return ok(service.unload_session())

    @app.get("/api/session")
    async def api_session():
        if service.session is None:
            return ok({"loaded": False})
        return ok({"loaded": True, **service.session_info})

    @app.get("/api/session/layout")
    async def api_layout():
        if service.session is None:
            raise HTTPException(400, "No connectome is loaded.")
        layout = service.session.layout()
        return ok({k: v for k, v in layout.items() if k != "sample"})

    @app.get("/api/session/frame")
    async def api_frame():
        if service.session is None:
            raise HTTPException(400, "No connectome is loaded.")
        return ok(service.session.viewport_frame())

    @app.post("/api/session/probe")
    async def api_probe(request: Request):
        """Drive one channel and read another. The tuning bench."""
        if service.session is None:
            raise HTTPException(400, "No connectome is loaded.")
        if service.thread and service.thread.is_alive():
            raise HTTPException(409, "Stop the current run before probing.")
        body = await request.json()
        session = service.session

        def work():
            session.clear_stimuli()
            driven = []
            for item in body.get("drive", []):
                stimulus = session.add_stimulus(
                    item["channel"], float(item.get("value", 1.0)),
                    side=item.get("side", "both"),
                    curve=item.get("curve", "saturating"),
                    current=item.get("current") or None,
                    low=float(item.get("low", 0.0)), high=float(item.get("high", 1.0)),
                    source="probe",
                )
                driven.append(stimulus.summary())
            for key in body.get("teach", []):
                driven.append(session.add_teaching_pulse(key, source="probe").summary())
            result = session.step(
                float(body.get("duration_ms", 200.0)),
                learning=bool(body.get("learning", False)),
            )
            reads = [session.read_channel(k) for k in body.get("read", [])]
            return {
                "drive": driven,
                "step": result.summary(),
                "read": reads,
                "activity": session.channel_activity(("input", "output", "modulatory")),
                "viewport": session.viewport_frame(),
            }

        try:
            return ok(await asyncio.to_thread(work))
        except Exception as exc:
            raise HTTPException(400, str(exc)) from None

    # -------------------------------------------------------------- projects
    @app.get("/api/projects")
    async def api_projects():
        return ok({"projects": service.workspace.list_projects()})

    @app.post("/api/projects")
    async def api_create_project(request: Request):
        body = await request.json()
        name = str(body.get("name", "")).strip()
        if not name:
            raise HTTPException(400, "Give the project a name.")
        template = body.get("template", "bench")
        if template not in templates.TEMPLATES:
            raise HTTPException(400, f"Unknown template: {template}")
        record = service.workspace.create_project(
            name, template, body.get("description", ""), body.get("tags", [])
        )
        return ok(service.workspace.get_project(record["id"]))

    @app.get("/api/projects/{project_id}")
    async def api_project(project_id: str):
        try:
            return ok(service.workspace.get_project(project_id))
        except KeyError:
            raise HTTPException(404, "No such project") from None

    @app.patch("/api/projects/{project_id}")
    async def api_update_project(project_id: str, request: Request):
        body = await request.json()
        try:
            service.workspace.update_project(project_id, **body)
            return ok(service.workspace.get_project(project_id))
        except KeyError:
            raise HTTPException(404, "No such project") from None
        except Exception as exc:
            raise HTTPException(400, str(exc)) from None

    @app.delete("/api/projects/{project_id}")
    async def api_delete_project(project_id: str):
        try:
            return ok(service.workspace.delete_project(project_id))
        except KeyError:
            raise HTTPException(404, "No such project") from None

    @app.post("/api/projects/{project_id}/duplicate")
    async def api_duplicate(project_id: str, request: Request):
        body = await request.json() if await request.body() else {}
        try:
            return ok(service.workspace.duplicate_project(project_id, body.get("name", "")))
        except KeyError:
            raise HTTPException(404, "No such project") from None

    @app.post("/api/workflow/check")
    async def api_check(request: Request):
        body = await request.json()
        try:
            workflow = Workflow.load(body.get("workflow", body))
        except Exception as exc:
            raise HTTPException(400, f"That workflow could not be read: {exc}") from None
        return ok({"problems": workflow.check(), "nodes": len(workflow.nodes),
                   "wires": len(workflow.wires)})

    @app.post("/api/workflow/template")
    async def api_template(request: Request):
        body = await request.json()
        key = body.get("key", "empty")
        try:
            return ok({"workflow": templates.build(key).json()})
        except KeyError:
            raise HTTPException(404, f"Unknown template: {key}") from None

    # ---------------------------------------------------------------- models
    @app.get("/api/models")
    async def api_models():
        return ok({"models": service.workspace.list_models()})

    @app.post("/api/models")
    async def api_save_model(request: Request):
        body = await request.json() if await request.body() else {}
        if service.session is None:
            raise HTTPException(400, "No connectome is loaded, so there is nothing to save.")
        label = str(body.get("label", "")).strip() or "snapshot"
        folder = Path(service.workspace.root) / "models" / "files"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{int(time.time())}-{label.replace(' ', '-')[:40]}.npz"
        service.session.save(path, label=label, notes=body.get("notes", ""))
        record = service.workspace.register_model(
            path, label, body.get("notes", ""), body.get("tags", []),
            body.get("project_id", ""), service.settings.dataset_dir,
            service.session.provenance(),
        )
        service.hub.publish({"kind": "model", "model": record})
        return ok(record)

    @app.patch("/api/models/{model_id}")
    async def api_update_model(model_id: str, request: Request):
        body = await request.json()
        try:
            return ok(service.workspace.update_model(model_id, **body))
        except KeyError:
            raise HTTPException(404, "No such model") from None

    @app.delete("/api/models/{model_id}")
    async def api_delete_model(model_id: str, remove_file: bool = False):
        try:
            return ok(service.workspace.delete_model(model_id, remove_file))
        except KeyError:
            raise HTTPException(404, "No such model") from None

    @app.post("/api/models/{model_id}/load")
    async def api_load_model(model_id: str):
        if service.session is None:
            raise HTTPException(400, "Load a connectome before restoring a model.")
        if service.thread and service.thread.is_alive():
            raise HTTPException(409, "Stop the current run first.")
        try:
            record = service.workspace.get_model(model_id)
        except KeyError:
            raise HTTPException(404, "No such model") from None
        if not Path(record["path"]).exists():
            raise HTTPException(400, "That model's file is missing from this machine.")
        try:
            sidecar = await asyncio.to_thread(service.session.restore, record["path"])
        except Exception as exc:
            raise HTTPException(400, f"That model does not match the loaded network: {exc}") from None
        service.set_task("Restore model", "done", record["label"], 1.0)
        return ok({"model": record, "checkpoint": sidecar})

    # ------------------------------------------------------------------- run
    @app.post("/api/run/start")
    async def api_run_start(request: Request):
        body = await request.json()
        project_id = body.get("project_id", "")
        if not project_id:
            raise HTTPException(400, "Choose a project to run.")
        if body.get("workflow") is not None:
            service.workspace.update_project(project_id, workflow=body["workflow"])
        try:
            return ok(service.start_run(project_id, body.get("settings")))
        except KeyError:
            raise HTTPException(404, "No such project") from None
        except Exception as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/run/stop")
    async def api_run_stop():
        return ok(service.stop_run())

    @app.post("/api/run/pause")
    async def api_run_pause(request: Request):
        body = await request.json() if await request.body() else {}
        try:
            return ok(service.pause_run(bool(body.get("paused", True))))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/run")
    async def api_run():
        running = bool(service.thread and service.thread.is_alive())
        return ok({
            "active": running,
            "project": service.run_project,
            "summary": service.run_summary,
            **(service.engine.summary() if service.engine else {}),
            "records": (service.engine.records[-200:] if service.engine else []),
        })

    @app.get("/api/preview/{node_id}")
    async def api_preview(node_id: str):
        engine = service.engine
        preview = (engine.previews.get(node_id) if engine else None)
        if preview is None:
            raise HTTPException(404, "No picture has been captured by that block yet.")
        from .io.vision import to_png

        return Response(to_png(preview["frame"]), media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------- websocket
    @app.websocket("/ws")
    async def websocket(socket: WebSocket):
        client = socket.client.host if socket.client else ""
        current = service.settings
        if not settings_module.is_loopback(client):
            if not current.lan_enabled:
                await socket.close(code=4403)
                return
            key = socket.query_params.get("key") or socket.cookies.get("flylab_key", "")
            if current.lan_require_key and key != current.access_key:
                await socket.close(code=4401)
                return
        await socket.accept()
        service.hub.clients.add(socket)
        try:
            await socket.send_text(json.dumps({"kind": "hello", "state": service.state()}, default=str))
            while True:
                text = await socket.receive_text()
                if text == "ping":
                    await socket.send_text(json.dumps({"kind": "pong", "at": time.time()}))
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            service.hub.clients.discard(socket)

    return app
