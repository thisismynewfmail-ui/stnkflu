"""Blocks, the workflow document, and the engine that runs it."""

import numpy as np
import pytest

from flylab.workflow import blocks, templates
from flylab.workflow.engine import Engine, RunSettings
from flylab.workflow.goals import GoalTracker
from flylab.workflow.graph import Workflow


# ------------------------------------------------------------------ catalogue
def test_every_block_keeps_the_labelling_contract():
    for key, block in blocks.REGISTRY.items():
        assert block.name.strip() and block.plain.strip(), key
        assert len(block.summary) > 30, f"{key} needs a real summary"
        assert block.category in blocks.__dict__["validate"].__globals__["CATEGORIES"]
        for port in list(block.inputs) + list(block.outputs):
            assert port.label and port.plain, f"{key}.{port.key}"
        for param in block.params:
            assert param.tip, f"{key}.{param.key} has no hover tip"


def test_the_palette_covers_every_kind_of_work():
    categories = {block.category for block in blocks.REGISTRY.values()}
    assert categories >= {"flow", "source", "vision", "encode", "brain",
                          "decode", "logic", "output", "goal"}
    assert len(blocks.REGISTRY) >= 60
    for expected in ("input.gpio", "input.ping", "input.serial", "input.audio",
                     "vision.screen", "vision.camera", "encode.eye", "encode.channel",
                     "brain.step", "decode.difference", "loop.repeat", "loop.until",
                     "flow.branch", "output.gpio", "output.pwm", "output.servo",
                     "output.serial", "output.mouse", "goal.outcome", "train.teach"):
        assert expected in blocks.REGISTRY


def test_catalogue_serialises_for_the_interface():
    catalogue = blocks.catalogue()
    assert catalogue["blocks"] and catalogue["categories"] and catalogue["pins"]
    for block in catalogue["blocks"]:
        assert isinstance(block["flow_out"], list)
        assert isinstance(block["pull"], bool)


# ------------------------------------------------------------- safe arithmetic
@pytest.mark.parametrize("text,names,expected", [
    ("a + b", {"a": 2, "b": 3}, 5),
    ("clamp(a * 2, 0, 10)", {"a": 8}, 10),
    ("a / b", {"a": 1, "b": 0}, 0),
    ("a > b", {"a": 2, "b": 1}, 1),
    ("-a", {"a": 4}, -4),
])
def test_expression_evaluates_arithmetic(text, names, expected):
    assert blocks.safe_expression(text, names) == pytest.approx(expected)


@pytest.mark.parametrize("text", [
    "__import__('os').system('true')",
    "open('/etc/passwd')",
    "a.__class__",
    "[x for x in range(3)]",
    "lambda: 1",
])
def test_expression_refuses_anything_else(text):
    with pytest.raises(ValueError):
        blocks.safe_expression(text, {"a": 1})


def test_coercion_is_forgiving_but_finite():
    assert blocks.as_number(float("nan"), 7) == 7
    assert blocks.as_number(None, 3) == 3
    assert blocks.as_number({"hz": 2.5}) == 2.5
    assert blocks.as_bool({"triggered": True}) is True
    assert blocks.as_bool("yes") is True
    assert list(blocks.as_vector({"bands": [1, 2]})) == [1, 2]
    assert blocks.as_text({"choice": "left"}) == "left"


# -------------------------------------------------------------------- document
def test_templates_all_build_and_check_clean():
    for entry in templates.catalogue():
        workflow = templates.build(entry["key"])
        errors = [p for p in workflow.check() if p["severity"] == "error"]
        assert not errors, f"{entry['key']}: {errors}"


def test_a_workflow_round_trips_through_json():
    original = templates.build("train")
    copy = Workflow.load(original.json())
    assert len(copy.nodes) == len(original.nodes)
    assert len(copy.wires) == len(original.wires)
    assert copy.json() == original.json()


def test_missing_start_is_an_error():
    workflow = Workflow("no start")
    workflow.add("brain.step")
    messages = [p["message"] for p in workflow.check() if p["severity"] == "error"]
    assert any("no Start block" in m for m in messages)


def test_a_required_input_left_empty_is_an_error():
    workflow = Workflow("incomplete")
    start = workflow.add("flow.start")
    branch = workflow.add("flow.branch")
    workflow.connect(start.id, "next", branch.id)
    messages = [p["message"] for p in workflow.check() if p["severity"] == "error"]
    assert any("Condition" in m for m in messages)


def test_incompatible_value_types_cannot_be_connected():
    workflow = Workflow("mismatch")
    start = workflow.add("flow.start")
    pattern = workflow.add("vision.pattern")
    compare = workflow.add("logic.compare")
    workflow.connect(start.id, "next", compare.id)
    workflow.connect(pattern.id, "frame", compare.id, "value", "value")
    messages = [p["message"] for p in workflow.check() if p["severity"] == "error"]
    assert any("cannot be connected" in m for m in messages)


def test_a_value_loop_is_reported_with_the_way_out():
    workflow = Workflow("loop")
    start = workflow.add("flow.start")
    a = workflow.add("logic.math")
    b = workflow.add("logic.math")
    workflow.connect(start.id, "next", a.id)
    workflow.connect(a.id, "value", b.id, "a", "value")
    workflow.connect(b.id, "value", a.id, "a", "value")
    problems = [p for p in workflow.check() if p["severity"] == "error"]
    assert any("form a loop" in p["message"] for p in problems)
    assert any("Hold a value" in p["fix"] for p in problems)


def test_one_run_order_wire_per_outlet():
    workflow = Workflow("branching")
    start = workflow.add("flow.start")
    first = workflow.add("flow.wait")
    second = workflow.add("flow.wait")
    workflow.connect(start.id, "next", first.id)
    workflow.connect(start.id, "next", second.id)
    assert workflow.flow_target(start.id, "next") == second.id
    assert len([w for w in workflow.wires.values() if w.kind == "flow"]) == 1


def test_only_one_start_block_is_allowed():
    workflow = Workflow("two starts")
    workflow.add("flow.start")
    with_limit = workflow.add("flow.start")
    assert with_limit is not None  # the document permits it
    messages = [p["message"] for p in workflow.check() if p["severity"] == "error"]
    assert any("Start blocks" in m for m in messages)


def test_a_disabled_block_is_stepped_over():
    workflow = Workflow("skip")
    start = workflow.add("flow.start")
    skipped = workflow.add("flow.wait")
    after = workflow.add("output.log")
    workflow.connect(start.id, "next", skipped.id)
    workflow.connect(skipped.id, "next", after.id)
    skipped.disabled = True
    assert workflow.flow_target(start.id, "next") == after.id


# ---------------------------------------------------------------------- engine
def run(workflow, session=None, **settings):
    options = {"iterations": 1, "simulate_devices": True, "telemetry_every": 1}
    options.update(settings)
    engine = Engine(workflow, session, RunSettings(**options), lambda event: None,
                    output_dir=settings.pop("output_dir", None) or "/tmp/flylab-test-run")
    engine.run("test-run")
    return engine


def test_a_run_walks_the_run_order_and_stops_cleanly():
    workflow = Workflow("counting")
    start = workflow.add("flow.start")
    counter = workflow.add("logic.counter", params={"step": 1})
    log = workflow.add("output.log", params={"label": "count"})
    workflow.connect(start.id, "next", counter.id)
    workflow.connect(counter.id, "next", log.id)
    workflow.connect(counter.id, "count", log.id, "value", "value")
    engine = run(workflow, iterations=4)
    assert engine.state.status == "finished"
    assert engine.state.reason == "completed"
    assert engine.state.iteration == 4
    assert engine.outputs[counter.id]["count"] == 4


def test_a_loop_runs_its_body_the_requested_number_of_times():
    workflow = Workflow("loop")
    start = workflow.add("flow.start")
    loop = workflow.add("loop.repeat", params={"times": 5})
    counter = workflow.add("logic.counter")
    workflow.connect(start.id, "next", loop.id)
    workflow.connect(loop.id, "body", counter.id)
    engine = run(workflow, iterations=2)
    assert engine.outputs[counter.id]["count"] == 10


def test_leaving_a_loop_early_ends_only_that_loop():
    workflow = Workflow("break")
    start = workflow.add("flow.start")
    loop = workflow.add("loop.repeat", params={"times": 9})
    counter = workflow.add("logic.counter")
    leave = workflow.add("flow.break")
    after = workflow.add("logic.counter")
    workflow.connect(start.id, "next", loop.id)
    workflow.connect(loop.id, "body", counter.id)
    workflow.connect(counter.id, "next", leave.id)
    workflow.connect(loop.id, "done", after.id)
    engine = run(workflow)
    assert engine.outputs[counter.id]["count"] == 1
    assert engine.outputs[after.id]["count"] == 1


def test_a_branch_follows_only_one_path():
    workflow = Workflow("branch")
    start = workflow.add("flow.start")
    value = workflow.add("input.number", params={"value": 5})
    compare = workflow.add("logic.compare", params={"test": "gt", "threshold": 1})
    branch = workflow.add("flow.branch")
    yes = workflow.add("logic.counter")
    no = workflow.add("logic.counter")
    workflow.connect(start.id, "next", branch.id)
    workflow.connect(value.id, "value", compare.id, "value", "value")
    workflow.connect(compare.id, "result", branch.id, "when", "value")
    workflow.connect(branch.id, "yes", yes.id)
    workflow.connect(branch.id, "no", no.id)
    engine = run(workflow)
    assert engine.outputs[yes.id]["count"] == 1
    assert no.id not in engine.outputs


def test_a_stop_block_ends_the_run_and_records_why():
    workflow = Workflow("stop")
    start = workflow.add("flow.start")
    stop = workflow.add("flow.stop", params={"reason": "target reached", "outcome": "succeeded"})
    workflow.connect(start.id, "next", stop.id)
    engine = run(workflow, iterations=50)
    assert engine.state.iteration == 1
    assert engine.state.reason == "goal"
    assert "target reached" in engine.state.detail


def test_an_unconnected_input_falls_back_to_the_block_setting():
    workflow = Workflow("defaults")
    start = workflow.add("flow.start")
    loop = workflow.add("loop.repeat", params={"times": 3})
    counter = workflow.add("logic.counter")
    workflow.connect(start.id, "next", loop.id)
    workflow.connect(loop.id, "body", counter.id)
    engine = run(workflow)
    assert engine.outputs[counter.id]["count"] == 3


def test_an_output_block_with_no_condition_still_acts():
    workflow = Workflow("ungated")
    start = workflow.add("flow.start")
    pin = workflow.add("output.gpio", params={"pin": 17, "high": True})
    workflow.connect(start.id, "next", pin.id)
    engine = run(workflow)
    assert engine.outputs[pin.id]["acted"] is True
    assert engine.outputs[pin.id]["details"]["simulated"] is True


def test_a_failing_block_stops_the_run_when_asked():
    workflow = Workflow("failure")
    start = workflow.add("flow.start")
    bad = workflow.add("encode.channel", params={"channel": "vision.r1_r6"})
    value = workflow.add("input.number", params={"value": 1})
    workflow.connect(start.id, "next", bad.id)
    workflow.connect(value.id, "value", bad.id, "value", "value")
    engine = run(workflow, stop_on_error=True)
    assert engine.state.status == "failed"
    assert engine.state.reason == "error"
    assert engine.state.errors


def test_a_failing_block_can_be_survived_instead():
    workflow = Workflow("survive")
    start = workflow.add("flow.start")
    bad = workflow.add("encode.channel", params={"channel": "vision.r1_r6"})
    value = workflow.add("input.number", params={"value": 1})
    after = workflow.add("logic.counter")
    workflow.connect(start.id, "next", bad.id)
    workflow.connect(bad.id, "next", after.id)
    workflow.connect(value.id, "value", bad.id, "value", "value")
    engine = run(workflow, stop_on_error=False, iterations=2)
    assert engine.state.status == "finished"
    assert engine.outputs[after.id]["count"] == 2
    assert engine.state.errors


def test_a_workflow_with_errors_never_starts():
    workflow = Workflow("invalid")
    workflow.add("brain.step")
    engine = run(workflow)
    assert engine.state.status == "failed"
    assert engine.state.reason == "invalid"
    assert engine.state.iteration == 0


def test_shell_output_is_off_until_it_is_switched_on():
    workflow = Workflow("shell")
    start = workflow.add("flow.start")
    command = workflow.add("output.command", params={"command": "echo hi", "allow": False})
    workflow.connect(start.id, "next", command.id)
    engine = run(workflow, stop_on_error=True)
    assert engine.state.status == "failed"
    assert "disabled" in engine.state.detail.lower()


# ----------------------------------------------------------------------- goals
def test_scoring_counts_only_decided_passes():
    goal = GoalTracker()
    goal.define("turn left", "chose left", "chose right")
    goal.record("correct", 1)
    goal.record("incorrect", 2)
    goal.record("neutral", 3)
    goal.record("correct", 4)
    summary = goal.summary()
    assert summary["attempts"] == 3
    assert summary["correct"] == 2
    assert summary["neutral"] == 1
    assert goal.accuracy == pytest.approx(2 / 3)
    assert summary["accuracy"] == pytest.approx(2 / 3, abs=1e-4)
    assert summary["streak"] == 1
    assert summary["best_streak"] == 1
    assert "frozen-synapse" in summary["caveat"]


def test_an_undecided_pass_is_neutral_not_wrong():
    goal = GoalTracker()
    goal.record("neutral", 1)
    assert goal.summary()["attempts"] == 0
    assert goal.summary()["incorrect"] == 0
    assert goal.summary()["neutral"] == 1


def test_an_unknown_outcome_is_refused():
    with pytest.raises(ValueError):
        GoalTracker().record("brilliant", 1)


def test_the_outcome_block_routes_and_scores():
    workflow = Workflow("outcome")
    start = workflow.add("flow.start")
    yes = workflow.add("input.number", params={"value": 1})
    right = workflow.add("logic.compare", params={"test": "gt", "threshold": 0})
    outcome = workflow.add("goal.outcome", params={"stop_after": 3})
    tally = workflow.add("logic.counter")
    workflow.connect(start.id, "next", outcome.id)
    workflow.connect(yes.id, "value", right.id, "value", "value")
    workflow.connect(right.id, "result", outcome.id, "correct", "value")
    workflow.connect(outcome.id, "correct", tally.id)
    engine = run(workflow, iterations=10)
    assert engine.goal.summary()["correct"] == 3
    assert engine.goal.reached is True
    assert engine.state.reason == "goal"
    assert engine.outputs[tally.id]["count"] == 3


# ------------------------------------------------------------- with a network
def test_a_whole_workflow_drives_the_network(session):
    workflow = templates.build("bench")
    for node in workflow.nodes.values():
        if node.type == "brain.step":
            node.params["duration"] = 100.0
    engine = run(workflow, session=session, iterations=2)
    assert engine.state.status == "finished"
    assert engine.step_count == 2
    assert len(engine.displays) == 3
    assert engine.outputs, "blocks produced no output"


def test_training_pairs_an_outcome_with_a_teaching_pulse(session):
    session.reset(keep_memory=False)
    workflow = templates.build("train")
    for node in workflow.nodes.values():
        if node.type == "brain.step":
            node.params["duration"] = 100.0
        if node.type == "loop.repeat":
            node.params["times"] = 3
        if node.type == "vision.image":
            node.type = "vision.pattern"
            node.params = {"speed": 0.25, "width": 160, "height": 90}
    engine = run(workflow, session=session, iterations=1)
    assert engine.state.status == "finished"
    summary = engine.goal.summary()
    # Every trial is judged. Whether it is scored right, scored wrong, or left
    # unscored depends on whether the readout committed at all, which on a
    # random fixture graph it often does not - and a tie must not be counted.
    assert summary["attempts"] + summary["neutral"] == 3
    assert summary["trial"] == 3
    assert summary["goal"].startswith("Learn to answer")


def test_the_run_summary_is_marked_synthetic(session):
    workflow = templates.build("bench")
    for node in workflow.nodes.values():
        if node.type == "brain.step":
            node.params["duration"] = 50.0
    engine = run(workflow, session=session, iterations=1)
    assert engine.summary()["synthetic_fixture"] is True
