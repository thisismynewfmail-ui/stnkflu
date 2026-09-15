"""The workspace, the settings, and the local web service."""

import json

import pytest
from fastapi.testclient import TestClient

from flylab import datasets, settings as settings_module
from flylab.server import build_app


@pytest.fixture
def client(tmp_path, fixture_root):
    settings = settings_module.load(tmp_path / "home")
    settings.dataset_dir = str(fixture_root)
    settings.open_browser = False
    settings_module.save(settings)
    return TestClient(build_app(settings), client=("127.0.0.1", 40000))


# ------------------------------------------------------------------ settings
def test_sharing_on_the_network_is_the_default():
    settings = settings_module.AppSettings().validate()
    assert settings.lan_enabled is True
    assert settings.lan_require_key is False
    assert settings.open_to_network is True
    # Every interface, which is what makes it reachable at this machine's own
    # address as well as at loopback.
    assert settings.host == "0.0.0.0"
    assert settings.access_key == ""
    assert f"http://127.0.0.1:{settings.port}/" in settings.urls()


def test_the_shareable_address_is_this_machine_not_loopback():
    settings = settings_module.AppSettings().validate()
    share = settings.share_url()
    if not settings_module.local_addresses():
        assert share is None  # a machine with no network attached
        return
    assert share and "127.0.0.1" not in share
    assert share == settings.urls()[0]
    assert share.endswith(f":{settings.port}/")


def test_local_only_hides_every_address_but_loopback():
    settings = settings_module.AppSettings(lan_enabled=False).validate()
    assert settings.host == "127.0.0.1"
    assert settings.urls() == [f"http://127.0.0.1:{settings.port}/"]
    assert settings.share_url() is None
    assert settings.open_to_network is False


def test_asking_for_a_key_generates_one_and_puts_it_in_the_address():
    settings = settings_module.AppSettings(lan_require_key=True).validate()
    assert len(settings.access_key) >= 20
    assert settings.open_to_network is False
    share = settings.share_url()
    if share:
        assert f"key={settings.access_key}" in share


def test_an_older_settings_file_adopts_the_shared_default(tmp_path):
    # A file written before sharing became the default predates the choice.
    path = settings_module.settings_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lan_enabled": False, "lan_require_key": True, "port": 8765}))
    settings = settings_module.load(tmp_path)
    assert settings.lan_enabled is True
    assert settings.lan_require_key is False
    assert settings.version == settings_module.SETTINGS_VERSION


def test_a_deliberate_choice_survives_the_default_changing(tmp_path):
    path = settings_module.settings_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "lan_enabled": False, "lan_require_key": True, "network_chosen": True}))
    settings = settings_module.load(tmp_path)
    assert settings.lan_enabled is False
    assert settings.lan_require_key is True


def test_an_impossible_port_is_refused():
    with pytest.raises(ValueError, match="Port"):
        settings_module.AppSettings(port=70000).validate()


def test_loopback_recognition():
    assert settings_module.is_loopback("127.0.0.1")
    assert settings_module.is_loopback("::1")
    assert not settings_module.is_loopback("192.168.1.30")
    assert not settings_module.is_loopback("")


def test_settings_survive_a_round_trip(tmp_path):
    settings = settings_module.load(tmp_path)
    settings.iterations = 42
    settings.compartments = "mushroom_body_full"
    settings_module.save(settings, tmp_path)
    again = settings_module.load(tmp_path)
    assert again.iterations == 42
    assert again.compartments == "mushroom_body_full"


# ----------------------------------------------------------------- workspace
def test_a_project_holds_a_workflow_and_its_history(workspace):
    record = workspace.create_project("Wall follower", "avoid", "robot", ["robot"])
    project = workspace.get_project(record["id"])
    assert project["name"] == "Wall follower"
    assert len(project["workflow"]["nodes"]) > 5
    workspace.update_project(record["id"], notes="checked the wiring")
    workspace.record_run(record["id"], {"run_id": "r1", "state": {"reason": "completed"}})
    project = workspace.get_project(record["id"])
    assert project["notes"] == "checked the wiring"
    assert len(project["history"]) == 1
    assert workspace.list_projects()[0]["runs"] == 1


def test_duplicating_a_project_keeps_the_workflow_but_not_the_history(workspace):
    first = workspace.create_project("Original", "bench")
    workspace.record_run(first["id"], {"run_id": "r1"})
    copy = workspace.duplicate_project(first["id"], "Copy")
    assert copy["name"] == "Copy"
    assert len(copy["workflow"]["nodes"]) == len(
        workspace.get_project(first["id"])["workflow"]["nodes"])
    assert copy["history"] == []


def test_deleting_a_missing_project_is_a_clear_error(workspace):
    with pytest.raises(KeyError):
        workspace.delete_project("no-such-project")


def test_a_model_records_where_it_came_from(workspace, session, tmp_path):
    path = tmp_path / "snapshot.npz"
    session.save(path, label="baseline", notes="untouched")
    record = workspace.register_model(path, "baseline", "untouched", ["control"],
                                      dataset="fixture")
    assert record["sha256"]
    assert record["synthetic_fixture"] is True
    assert record["memory"]["plastic_edges"] > 0
    workspace.update_model(record["id"], label="renamed")
    assert workspace.get_model(record["id"])["label"] == "renamed"
    workspace.delete_model(record["id"], remove_file=True)
    assert not path.exists()
    assert workspace.list_models() == []


def test_a_malformed_workflow_never_lands_on_disk(workspace):
    record = workspace.create_project("Strict", "empty")
    with pytest.raises(Exception):
        workspace.update_project(record["id"], workflow={"nodes": [{"no": "id"}]})


# ------------------------------------------------------------------- service
def test_state_describes_everything_the_interface_needs(client):
    state = client.get("/api/state").json()
    for key in ("version", "settings", "workspace", "session", "run", "task",
                "datasets", "devices", "host"):
        assert key in state


def test_the_catalogue_is_complete(client):
    catalogue = client.get("/api/catalogue").json()
    assert len(catalogue["blocks"]["blocks"]) >= 60
    assert len(catalogue["templates"]) >= 6
    assert catalogue["modalities"] and catalogue["run_reasons"]


def test_loading_a_dataset_and_reading_the_network(client, fixture_root):
    response = client.post("/api/session/load", json={"path": str(fixture_root)})
    assert response.status_code == 200
    assert response.json()["dataset"]["synthetic_fixture"] is True
    layout = client.get("/api/session/layout").json()
    assert layout["cells_shown"] > 0 and layout["regions"]
    assert "Schematic" in layout["note"]
    frame = client.get("/api/session/frame").json()
    assert "activity" in frame and frame["synthetic_fixture"] is True


def test_probing_drives_and_reads_named_cells(client, fixture_root):
    client.post("/api/session/load", json={"path": str(fixture_root)})
    result = client.post("/api/session/probe", json={
        "drive": [{"channel": "taste.sugar", "value": 1.0}],
        "teach": ["teach.pam11"],
        "read": ["memory.mbon", "motor.descending"],
        "duration_ms": 50.0, "learning": False,
    }).json()
    assert len(result["read"]) == 2
    assert result["step"]["total_spikes"] > 0
    assert any(s["channel"] == "teach.pam11" for s in result["drive"])


def test_probing_an_unknown_channel_is_a_clear_400(client, fixture_root):
    client.post("/api/session/load", json={"path": str(fixture_root)})
    response = client.post("/api/session/probe", json={
        "drive": [{"channel": "nope.nope", "value": 1.0}], "duration_ms": 10.0})
    assert response.status_code == 400


def test_a_project_can_be_created_checked_and_run(client, fixture_root):
    client.post("/api/session/load", json={"path": str(fixture_root)})
    project = client.post("/api/projects", json={"name": "Service run", "template": "bench"}).json()
    workflow = project["workflow"]
    for node in workflow["nodes"]:
        if node["type"] == "brain.step":
            node["params"]["duration"] = 50.0
    check = client.post("/api/workflow/check", json={"workflow": workflow}).json()
    assert not [p for p in check["problems"] if p["severity"] == "error"]
    started = client.post("/api/run/start", json={
        "project_id": project["id"], "workflow": workflow,
        "settings": {"iterations": 2, "simulate_devices": True},
    })
    assert started.status_code == 200
    deadline = 25
    while deadline:
        run = client.get("/api/run").json()
        if not run["active"] and run.get("state", {}).get("iteration"):
            break
        deadline -= 1
        import time

        time.sleep(0.4)
    run = client.get("/api/run").json()
    assert run["active"] is False
    assert run["state"]["iteration"] == 2
    assert run["state"]["status"] == "finished"
    history = client.get(f"/api/projects/{project['id']}").json()["history"]
    assert history and history[-1]["state"]["reason"] == "completed"


def test_an_unknown_template_is_refused(client):
    assert client.post("/api/projects", json={"name": "x", "template": "nope"}).status_code == 400
    assert client.post("/api/projects", json={"name": "  "}).status_code == 400


def test_saving_a_model_needs_a_loaded_network(client):
    assert client.post("/api/models", json={"label": "x"}).status_code == 400


def test_a_model_can_be_saved_listed_and_reloaded(client, fixture_root):
    client.post("/api/session/load", json={"path": str(fixture_root)})
    saved = client.post("/api/models", json={"label": "from service", "notes": "test"}).json()
    assert saved["synthetic_fixture"] is True
    listed = client.get("/api/models").json()["models"]
    assert any(m["id"] == saved["id"] for m in listed)
    assert client.post(f"/api/models/{saved['id']}/load").status_code == 200
    assert client.post("/api/models/not-a-model/load").status_code == 404


def test_settings_can_be_changed_over_the_api(client):
    result = client.post("/api/settings", json={"iterations": 7}).json()
    assert result["settings"]["iterations"] == 7
    assert result["restart_required"] is False
    network = client.post("/api/settings", json={"lan_enabled": False}).json()
    assert network["restart_required"] is True
    assert network["settings"]["host"] == "127.0.0.1"
    # Setting it by hand records the choice, so a later default change leaves
    # it alone.
    assert network["settings"]["network_chosen"] is True
    assert client.post("/api/settings", json={"port": 0}).status_code == 400


def test_a_remote_client_reaches_a_shared_interface(tmp_path, fixture_root):
    settings = settings_module.load(tmp_path / "home-shared")
    settings_module.save(settings)
    assert settings.lan_enabled is True and settings.lan_require_key is False
    remote = TestClient(build_app(settings), client=("10.1.2.3", 5000))
    response = remote.get("/api/state")
    assert response.status_code == 200
    assert response.json()["settings"]["open_to_network"] is True


def test_a_remote_client_is_refused_while_local_only(tmp_path, fixture_root):
    settings = settings_module.load(tmp_path / "home2")
    settings.lan_enabled = False
    settings.network_chosen = True
    settings_module.save(settings)
    remote = TestClient(build_app(settings), client=("10.1.2.3", 5000))
    response = remote.get("/api/state")
    assert response.status_code == 403
    assert "local only" in response.json()["error"].lower()
    # The same build still answers the machine it runs on.
    local = TestClient(build_app(settings), client=("127.0.0.1", 5000))
    assert local.get("/api/state").status_code == 200


def test_a_remote_client_needs_the_key_when_one_is_required(tmp_path):
    settings = settings_module.load(tmp_path / "home3")
    settings.lan_enabled = True
    settings.lan_require_key = True
    settings.network_chosen = True
    settings_module.save(settings.validate())
    app = build_app(settings)
    remote = TestClient(app, client=("10.1.2.3", 5000))
    assert remote.get("/api/state").status_code == 401
    assert remote.get(f"/api/state?key={settings.access_key}").status_code == 200
    assert remote.get("/api/state?key=wrong").status_code == 401
    # A key is never asked of the machine FLYLAB is running on.
    assert TestClient(app, client=("127.0.0.1", 5000)).get("/api/state").status_code == 200


# ------------------------------------------------------------------ datasets
def test_a_fixture_reports_itself_as_synthetic(fixture_root):
    report = datasets.verify(fixture_root)
    assert report["synthetic_fixture"] is True
    assert report["arrays_verified"] is False
    assert "not a connectome" in report["warning"].lower()


def test_a_fixture_never_overwrites_a_real_dataset(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "graph.npz").write_bytes(b"not really a graph")
    with pytest.raises(RuntimeError, match="not a fixture"):
        datasets.make_fixture(real)


def test_verifying_an_empty_directory_says_what_is_missing(tmp_path):
    with pytest.raises(RuntimeError, match="Prepare"):
        datasets.verify(tmp_path)


def test_the_source_lock_names_every_released_file():
    sources = datasets.sources()
    assert set(sources) == {"annotations.feather", "neurotransmitters.feather", "edges.feather"}
    for entry in sources.values():
        assert entry["url"].startswith("https://")
        assert len(entry["sha256"]) == 64
        assert entry["bytes"] > 0
