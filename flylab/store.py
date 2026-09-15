"""Projects, saved models and run history on disk.

A project is a workflow plus the settings it runs with plus the model it
resumes from: everything needed to pick a piece of work back up. A model is a
network checkpoint with a label, notes and the provenance needed to prove what
it came from. Both are plain JSON beside their files, so a workspace stays
readable without this program.
"""

import json
import shutil
import time
import uuid
from pathlib import Path

from .workflow import templates
from .workflow.graph import Workflow


def now():
    return time.time()


def new_id(prefix):
    return f"{prefix}-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"


def slug(text, fallback="item"):
    cleaned = "".join(c if c.isalnum() or c in "-_ " else "-" for c in str(text)).strip()
    return (cleaned.replace(" ", "-")[:48] or fallback).lower()


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return default if default is not None else {}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)
    return path


class Workspace:
    """Everything saved on this machine."""

    def __init__(self, root):
        self.root = Path(root)
        for folder in ("projects", "models", "datasets", "runs"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- projects
    def project_dir(self, project_id):
        return self.root / "projects" / project_id

    def list_projects(self):
        out = []
        for folder in sorted((self.root / "projects").iterdir()):
            if not folder.is_dir():
                continue
            record = read_json(folder / "project.json")
            if not record:
                continue
            record["id"] = folder.name
            record["runs"] = len(read_json(folder / "history.json", []))
            workflow = read_json(folder / "workflow.json")
            record["blocks"] = len(workflow.get("nodes", []))
            out.append(record)
        return sorted(out, key=lambda r: r.get("updated", 0), reverse=True)

    def create_project(self, name, template="bench", description="", tags=()):
        project_id = new_id(slug(name, "project"))
        folder = self.project_dir(project_id)
        folder.mkdir(parents=True, exist_ok=True)
        workflow = templates.build(template)
        workflow.name = name
        record = {
            "id": project_id,
            "name": str(name),
            "description": str(description),
            "template": template,
            "tags": list(tags),
            "created": now(),
            "updated": now(),
            "model_id": "",
            "dataset_dir": "",
            "run_settings": {},
            "notes": "",
        }
        write_json(folder / "project.json", record)
        write_json(folder / "workflow.json", workflow.json())
        write_json(folder / "history.json", [])
        return record

    def get_project(self, project_id):
        folder = self.project_dir(project_id)
        record = read_json(folder / "project.json")
        if not record:
            raise KeyError(f"No project {project_id}")
        record["id"] = project_id
        record["workflow"] = read_json(folder / "workflow.json")
        record["history"] = read_json(folder / "history.json", [])[-50:]
        return record

    def update_project(self, project_id, **changes):
        folder = self.project_dir(project_id)
        record = read_json(folder / "project.json")
        if not record:
            raise KeyError(f"No project {project_id}")
        workflow = changes.pop("workflow", None)
        if workflow is not None:
            # Load and re-serialise so a malformed document never lands on disk.
            write_json(folder / "workflow.json", Workflow.load(workflow).json())
        allowed = {"name", "description", "tags", "model_id", "dataset_dir",
                   "run_settings", "notes", "template"}
        for key, value in changes.items():
            if key in allowed:
                record[key] = value
        record["updated"] = now()
        record["id"] = project_id
        write_json(folder / "project.json", record)
        return record

    def delete_project(self, project_id):
        folder = self.project_dir(project_id)
        if not folder.exists():
            raise KeyError(f"No project {project_id}")
        shutil.rmtree(folder)
        return {"deleted": project_id}

    def duplicate_project(self, project_id, name=""):
        source = self.get_project(project_id)
        record = self.create_project(name or f"{source['name']} copy", "empty",
                                     source.get("description", ""), source.get("tags", []))
        self.update_project(record["id"], workflow=source["workflow"],
                            model_id=source.get("model_id", ""),
                            dataset_dir=source.get("dataset_dir", ""),
                            run_settings=source.get("run_settings", {}),
                            notes=source.get("notes", ""),
                            template=source.get("template", ""))
        return self.get_project(record["id"])

    def record_run(self, project_id, summary):
        folder = self.project_dir(project_id)
        if not folder.exists():
            return summary
        history = read_json(folder / "history.json", [])
        history.append({"at": now(), **summary})
        write_json(folder / "history.json", history[-500:])
        record = read_json(folder / "project.json")
        record["updated"] = now()
        write_json(folder / "project.json", record)
        return summary

    def run_dir(self, project_id, run_id):
        folder = self.project_dir(project_id) / "runs" / run_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # ---------------------------------------------------------------- models
    def list_models(self):
        out = []
        for path in sorted((self.root / "models").glob("*.json")):
            record = read_json(path)
            if not record:
                continue
            record["id"] = path.stem
            checkpoint = Path(record.get("path", ""))
            record["available"] = checkpoint.exists()
            record["bytes"] = checkpoint.stat().st_size if checkpoint.exists() else 0
            out.append(record)
        return sorted(out, key=lambda r: r.get("created", 0), reverse=True)

    def register_model(self, path, label="", notes="", tags=(), project_id="",
                       dataset="", provenance=None, parent=""):
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"No checkpoint at {path}")
        model_id = new_id(slug(label, "model"))
        sidecar = read_json(path.with_suffix(".json"))
        record = {
            "id": model_id,
            "label": str(label) or path.stem,
            "notes": str(notes),
            "tags": list(tags),
            "path": str(path.resolve()),
            "created": now(),
            "project_id": str(project_id),
            "dataset": str(dataset),
            "parent": str(parent),
            "sha256": sidecar.get("sha256", ""),
            "memory": sidecar.get("memory", {}),
            "steps": sidecar.get("steps", 0),
            "brain_ms": sidecar.get("brain_ms", 0),
            "compartments": sidecar.get("compartments", ""),
            "synthetic_fixture": bool(sidecar.get("synthetic_fixture", False)),
            "provenance": provenance or {},
        }
        write_json(self.root / "models" / f"{model_id}.json", record)
        return record

    def get_model(self, model_id):
        record = read_json(self.root / "models" / f"{model_id}.json")
        if not record:
            raise KeyError(f"No model {model_id}")
        record["id"] = model_id
        record["available"] = Path(record.get("path", "")).exists()
        return record

    def update_model(self, model_id, **changes):
        record = self.get_model(model_id)
        for key in ("label", "notes", "tags"):
            if key in changes:
                record[key] = changes[key]
        record["updated"] = now()
        write_json(self.root / "models" / f"{model_id}.json", record)
        return record

    def delete_model(self, model_id, remove_file=False):
        record = self.get_model(model_id)
        (self.root / "models" / f"{model_id}.json").unlink(missing_ok=True)
        if remove_file:
            path = Path(record.get("path", ""))
            path.unlink(missing_ok=True)
            path.with_suffix(".json").unlink(missing_ok=True)
        return {"deleted": model_id, "file_removed": bool(remove_file)}

    # -------------------------------------------------------------- datasets
    def dataset_dir(self, name):
        folder = self.root / "datasets" / slug(name, "dataset")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # ----------------------------------------------------------------- notes
    def summary(self):
        projects = self.list_projects()
        models = self.list_models()
        return {
            "root": str(self.root),
            "projects": len(projects),
            "models": len(models),
            "models_available": sum(1 for m in models if m.get("available")),
            "last_project": projects[0] if projects else None,
            "disk_bytes": sum(
                f.stat().st_size for f in self.root.rglob("*") if f.is_file()
            ),
        }
