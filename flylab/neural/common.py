"""Local verified dataset and build cache; never a dependency on another repo.

The active dataset directory is process-wide state. One process runs one
dataset at a time: the interface picks it before a session loads, and every
loader below reads it through :func:`data_root` so a fixture directory and the
real release can never be mixed inside one run.
"""

import hashlib
import json
import os
from pathlib import Path

DATA = Path(os.environ.get("FLYLAB_DATA", "data")).resolve()
GRAPH = DATA / "graph.npz"
CACHE = Path(os.environ.get("FLYLAB_CACHE", DATA / "cache")).resolve()
OUT = CACHE

_ACTIVE = {"data": DATA, "cache": CACHE}


def set_data_root(path):
    """Point every loader at a dataset directory. Returns the resolved path."""
    root = Path(path).resolve()
    _ACTIVE["data"] = root
    if "FLYLAB_CACHE" not in os.environ:
        _ACTIVE["cache"] = root / "cache"
    return root


def data_root():
    return _ACTIVE["data"]


def cache_root():
    return _ACTIVE["cache"]


def graph_path():
    return _ACTIVE["data"] / "graph.npz"


def digest(array):
    return hashlib.sha256(array.tobytes()).hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def annotations(ids, root=None):
    import pyarrow.feather as f

    root = data_root() if root is None else Path(root)
    return (
        f.read_table(root / "annotations.feather")
        .to_pandas()
        .set_index("bodyId")
        .loc[ids]
    )
