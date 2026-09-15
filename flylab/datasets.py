"""Getting the connectome onto this machine, and proving it is intact.

Three released files are downloaded, checked against committed SHA-256 locks,
normalised and compiled into the arrays the simulator loads. Every step
reports progress so the Housekeeping page can show what is happening during a
download that takes a while.

A dataset is never trusted because it is present. ``verify`` re-checks the
source checksums, every compiled array, the neurotransmitter annotations and
the neuron ordering before a session is allowed to load it.
"""

import hashlib
import json
import shutil
import time
import urllib.request
from pathlib import Path

import numpy as np

from .neural.common import digest, set_data_root
from .neural.fixture import MARKER, build_fixture, is_fixture

PACKAGE = Path(__file__).with_name("neural")
EXPECTED_NEURONS = 166700
EXPECTED_EDGES = 25582938

STEPS = [
    ("download", "Download released files", "About 1.1 GB from the MaleCNS release."),
    ("checksum", "Check the downloads", "SHA-256 against the locks committed in this repository."),
    ("normalise", "Normalise annotations", "Decide which objects become simulation nodes, and account for every excluded row."),
    ("compile", "Compile the graph", "Build the CSR arrays, retinotopic projection and readout index."),
    ("verify", "Verify everything", "Re-check every compiled array against its lock."),
]


def sources():
    return json.loads((PACKAGE / "sources.lock.json").read_text())


def file_sha(path, progress=None, label=""):
    digest_ = hashlib.sha256()
    size = Path(path).stat().st_size
    read = 0
    with Path(path).open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest_.update(chunk)
            read += len(chunk)
            if progress:
                progress({"step": "checksum", "file": label, "read": read, "total": size})
    return digest_.hexdigest()


def describe(root):
    """What is in a dataset directory, without loading anything heavy."""
    root = Path(root)
    manifest = {}
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except ValueError:
            manifest = {}
    graph = root / "graph.npz"
    present = {
        name: {
            "present": (root / name).exists(),
            "bytes": (root / name).stat().st_size if (root / name).exists() else 0,
            "expected_bytes": info["bytes"],
            "url": info["url"],
        }
        for name, info in sources().items()
    }
    return {
        "root": str(root),
        "exists": root.exists(),
        "fixture": is_fixture(root),
        "ready": graph.exists(),
        "graph_bytes": graph.stat().st_size if graph.exists() else 0,
        "manifest": manifest,
        "sources": present,
        "downloaded_bytes": sum(v["bytes"] for v in present.values()),
        "required_bytes": sum(v["expected_bytes"] for v in present.values()),
    }


def download(root, progress=None, force=False):
    """Fetch the released files, resuming past ones that are already correct."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    lock = sources()
    for name, info in lock.items():
        path = root / name
        if path.exists() and not force:
            if path.stat().st_size == info["bytes"]:
                if progress:
                    progress({"step": "download", "file": name, "status": "already here",
                              "read": info["bytes"], "total": info["bytes"]})
                continue
            path.unlink()
        temporary = path.with_suffix(path.suffix + ".partial")
        started = time.time()

        def hook(blocks, block_size, total, _name=name, _started=started):
            if progress:
                progress({
                    "step": "download", "file": _name,
                    "read": min(blocks * block_size, total if total > 0 else blocks * block_size),
                    "total": total if total > 0 else lock[_name]["bytes"],
                    "seconds": round(time.time() - _started, 1),
                })

        urllib.request.urlretrieve(info["url"], temporary, hook)
        actual = file_sha(temporary, progress, name)
        if actual != info["sha256"]:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(
                f"{name} did not match its checksum. The download was incomplete or the"
                " file upstream has changed; nothing was installed."
            )
        temporary.replace(path)
        if progress:
            progress({"step": "download", "file": name, "status": "verified",
                      "read": info["bytes"], "total": info["bytes"]})
    shutil.copyfile(PACKAGE / "sources.lock.json", root / "source.lock.json")
    return describe(root)


def prepare(root, progress=None, reuse=None):
    """Download if needed, then normalise and compile the graph."""
    root = Path(root)
    set_data_root(root)
    if reuse:
        source = Path(reuse)
        mapping = {
            source / "graph.npz": root / "graph.npz",
            source / "annotations.feather": root / "annotations.feather",
            source / "normalized/neurons.feather": root / "normalized/neurons.feather",
        }
        for origin, target in mapping.items():
            if not origin.exists():
                raise FileNotFoundError(f"Cannot reuse {reuse}: {origin} is missing")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
    else:
        download(root, progress)
        if progress:
            progress({"step": "normalise", "status": "working"})
        from .neural.connectome import import_graph

        import_graph()
        if progress:
            progress({"step": "compile", "status": "working"})
        from .neural.prepare import prepare as compile_graph

        compile_graph()
    if progress:
        progress({"step": "verify", "status": "working"})
    report = verify(root)
    if progress:
        progress({"step": "verify", "status": "done", "report": report})
    return report


def verify(root, strict=None):
    """Re-check a prepared dataset. Returns a report; raises on a mismatch."""
    root = Path(root)
    set_data_root(root)
    graph = root / "graph.npz"
    if not graph.exists():
        raise RuntimeError(f"No compiled graph at {graph}. Run Prepare first.")
    fixture = is_fixture(root)
    if strict is None:
        strict = not fixture
    checks = []
    if strict:
        lock = sources()
        annotations = root / "annotations.feather"
        if not annotations.exists():
            raise RuntimeError("annotations.feather is missing; the dataset is incomplete.")
        if file_sha(annotations) != lock["annotations.feather"]["sha256"]:
            raise RuntimeError("Annotation checksum mismatch; the source file is not the released one.")
        checks.append("source annotations match the committed lock")
        expected = json.loads((PACKAGE / "arrays.lock.json").read_text())
        with np.load(graph, allow_pickle=False) as arrays:
            if set(arrays.files) != set(expected):
                raise RuntimeError("Compiled graph has different fields than the lock expects.")
            for name, sha in expected.items():
                if digest(arrays[name]) != sha:
                    raise RuntimeError(f"Compiled array checksum mismatch: {name}")
            if len(arrays["ids"]) != EXPECTED_NEURONS or len(arrays["post"]) != EXPECTED_EDGES:
                raise RuntimeError("This is not the expected retained graph.")
        checks.append("every compiled array matches the committed lock")
        import pyarrow.feather as feather

        neurons_path = root / "normalized/neurons.feather"
        if not neurons_path.exists():
            raise RuntimeError("Normalized neuron metadata is missing.")
        neurons = feather.read_table(neurons_path).to_pandas()
        values = json.dumps(
            neurons.neurotransmitter.fillna("").astype(str).tolist(), separators=(",", ":")
        ).encode()
        wanted = json.loads((PACKAGE / "neurons.lock.json").read_text())["neurotransmitter_values_sha256"]
        if hashlib.sha256(values).hexdigest() != wanted:
            raise RuntimeError("Normalized transmitter values do not match the lock.")
        with np.load(graph, allow_pickle=False) as arrays:
            if not np.array_equal(neurons.source_id.to_numpy(), arrays["ids"]):
                raise RuntimeError("Normalized neuron order does not match the compiled graph.")
        checks.append("transmitter annotations and neuron ordering match")
    with np.load(graph, allow_pickle=False) as arrays:
        neurons = int(len(arrays["ids"]))
        edges = int(len(arrays["post"]))
        receptors = int(len(arrays["retina"]))
    manifest = describe(root)["manifest"]
    return {
        "root": str(root),
        "release": "synthetic fixture" if fixture else "MaleCNS v1.0",
        "synthetic_fixture": fixture,
        "neurons": neurons,
        "directed_edges": edges,
        "retina_mapped": receptors,
        "checks": checks,
        "arrays_verified": bool(strict),
        "manifest": manifest,
        "warning": manifest.get("warning", ""),
    }


def make_fixture(root, seed=20260915):
    """Write the synthetic bench fixture, replacing anything already there."""
    root = Path(root)
    if root.exists() and any(root.iterdir()) and not is_fixture(root):
        raise RuntimeError(
            f"{root} already holds a dataset that is not a fixture. Choose an"
            " empty directory so a real dataset is never overwritten."
        )
    manifest = build_fixture(root, seed=seed)
    return {**describe(root), "manifest": manifest}


def discover(workspace, extra=()):
    """Find dataset directories the interface can offer."""
    seen = []
    candidates = [Path(p) for p in extra]
    candidates += [Path(workspace) / "datasets" / p.name
                   for p in sorted((Path(workspace) / "datasets").glob("*")) if p.is_dir()]
    candidates += [Path("data"), Path("data-fixture")]
    for path in candidates:
        resolved = path.resolve()
        if any(entry["root"] == str(resolved) for entry in seen):
            continue
        if not resolved.exists():
            continue
        seen.append(describe(resolved))
    return seen
