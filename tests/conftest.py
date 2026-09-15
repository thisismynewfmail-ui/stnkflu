"""One synthetic fixture dataset, built once and shared by every test.

Tests never touch the released MaleCNS files. The fixture is a small randomly
wired graph in exactly the on-disk layout the real loader expects, so the same
loader, the same compiled kernel and the same channel resolution are exercised
in a couple of seconds.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def fixture_root(tmp_path_factory):
    from flylab.datasets import make_fixture

    root = tmp_path_factory.mktemp("dataset")
    make_fixture(root, seed=4242)
    os.environ["FLYLAB_DATA"] = str(root)
    return root


@pytest.fixture(scope="session")
def session(fixture_root):
    from flylab.neural.runtime import BrainSession, RuntimeConfig

    return BrainSession(RuntimeConfig(
        data_dir=str(fixture_root), default_step_ms=100.0, teach_pulse_ms=50.0
    ))


@pytest.fixture
def clean_session(session):
    session.reset(keep_memory=False)
    session.clear_stimuli()
    return session


@pytest.fixture
def workspace(tmp_path):
    from flylab.store import Workspace

    return Workspace(tmp_path / "workspace")
