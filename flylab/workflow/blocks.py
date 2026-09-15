"""Every workflow block: what it looks like, and what it does.

A block is a small, explicit step. Sources produce values, encoders turn values
into current on named cells, the network integrates, decoders read named cells
back, logic blocks decide, and output blocks act on the world. Loops, waits and
branches control the order.

The implementations live next to their declarations so the hover text and the
behaviour cannot drift apart.
"""

import ast
import math
import operator
import time

import numpy as np

from ..io import gpio as gpio_io
from ..io import links, vision
from ..neural import atlas as atlas_module
from .schema import BlockType, Param, Port, validate

REGISTRY = {}


def register(block):
    validate(block)
    if block.key in REGISTRY:
        raise ValueError(f"Duplicate block key: {block.key}")
    REGISTRY[block.key] = block
    return block


class Result:
    """What a block hands back to the engine."""

    __slots__ = ("outputs", "flow", "record", "stop")

    def __init__(self, outputs=None, flow="next", record=None, stop=None):
        self.outputs = outputs or {}
        self.flow = flow
        self.record = record or {}
        self.stop = stop


# ------------------------------------------------------------------- helpers
def as_number(value, default=0.0):
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else float(default)
    if isinstance(value, (list, tuple, np.ndarray)):
        flat = np.asarray(value, dtype=float).reshape(-1)
        return float(flat.mean()) if flat.size else float(default)
    if isinstance(value, dict):
        for key in ("value", "hz", "distance_cm", "level", "difference_hz"):
            if key in value and value[key] is not None:
                return as_number(value[key], default)
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "high")
    if isinstance(value, dict):
        for key in ("triggered", "ok", "value", "correct"):
            if key in value:
                return bool(value[key])
    if isinstance(value, (list, tuple, np.ndarray)):
        return bool(np.any(np.asarray(value)))
    return bool(value)


def as_vector(value):
    if isinstance(value, dict):
        for key in ("bands", "values", "vector"):
            if key in value:
                return np.asarray(value[key], dtype=float).reshape(-1)
        return np.asarray(list(value.values()), dtype=float).reshape(-1)
    if value is None:
        return np.zeros(0)
    return np.atleast_1d(np.asarray(value, dtype=float)).reshape(-1)


def as_text(value, default=""):
    if value is None:
        return default
    if isinstance(value, dict):
        for key in ("choice", "label", "text", "line", "status"):
            if key in value and value[key] is not None:
                return str(value[key])
    return str(value)


_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_COMPARE = {
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
    ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne,
}
_FUNCTIONS = {
    "abs": abs, "min": min, "max": max, "round": round,
    "sqrt": math.sqrt, "floor": math.floor, "ceil": math.ceil,
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "atan2": math.atan2,
    "exp": math.exp, "log": lambda x: math.log(x) if x > 0 else 0.0,
    "clamp": lambda x, lo, hi: max(lo, min(hi, x)),
    "sign": lambda x: (x > 0) - (x < 0),
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "true": True, "false": False}


def safe_expression(text, names):
    """Evaluate arithmetic over named inputs. No attributes, calls or imports.

    Only numbers, the four input names, comparisons, boolean operators and a
    fixed list of maths functions are reachable. Anything else is rejected
    before evaluation rather than sandboxed afterwards.
    """
    try:
        tree = ast.parse(str(text), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Cannot read the expression: {exc.msg}") from None

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, bool)):
                return node.value
            raise ValueError("Only numbers and true/false are allowed")
        if isinstance(node, ast.Name):
            key = node.id.lower()
            if key in names:
                return names[key]
            if key in _CONSTANTS:
                return _CONSTANTS[key]
            raise ValueError(f"Unknown name {node.id!r}; available: {sorted(names)}")
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left, right = walk(node.left), walk(node.right)
            if type(node.op) in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
                return 0.0
            return _BINARY[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                return -walk(node.operand)
            if isinstance(node.op, ast.UAdd):
                return walk(node.operand)
            if isinstance(node.op, ast.Not):
                return not walk(node.operand)
        if isinstance(node, ast.BoolOp):
            values = [walk(v) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op = type(node.ops[0])
            if op in _COMPARE:
                return _COMPARE[op](walk(node.left), walk(node.comparators[0]))
        if isinstance(node, ast.IfExp):
            return walk(node.body) if walk(node.test) else walk(node.orelse)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id.lower()
            if name in _FUNCTIONS and not node.keywords:
                return _FUNCTIONS[name](*[walk(a) for a in node.args])
            raise ValueError(f"Function {node.func.id!r} is not allowed here")
        raise ValueError("That expression uses something this block does not allow")

    value = walk(tree)
    return float(value) if isinstance(value, (int, float, bool)) else value


def channel_param(key="channel", label="Cell group", plain="Which real cells this block addresses",
                  tip="Pick a named group of cells from the loaded connectome. The list shows how many cells each group has in your dataset.",
                  role=None, default=""):
    options = [
        (c.key, f"{c.name} - {c.plain}", c.detail)
        for c in atlas_module.CHANNELS
        if role is None or c.role == role
    ]
    return Param(
        key=key, label=label, plain=plain, tip=tip, kind="channel",
        default=default or (options[0][0] if options else ""), options=tuple(options),
    )


SIDES = (
    ("both", "Both sides", "Every cell in the group"),
    ("left", "Left only", "Cells with a left-side soma"),
    ("right", "Right only", "Cells with a right-side soma"),
)
PIN_OPTIONS = tuple(
    (p["bcm"], p["label"], p["default_use"]) for p in gpio_io.pinout()
)


# =========================================================== run order blocks
def _run_start(ctx, node, inputs):
    return Result(record={"iteration": ctx.iteration})


register(BlockType(
    key="flow.start", name="Start", plain="Where the workflow begins",
    summary="Every run enters here. One per workflow. The blocks reached from"
            " Next run once per iteration.",
    category="flow", flow_in=False, run=_run_start, limit=1,
    outputs=(
        Port("iteration", "Iteration", "Which pass through the workflow this is", "number",
             tip="Counts from 1. Use it to change behaviour over a run, or to stop after N passes."),
    ),
    tips=("The run settings decide how many iterations happen and when the run stops.",),
))


def _run_wait(ctx, node, inputs):
    seconds = max(0.0, as_number(inputs.get("seconds", node.params.get("seconds", 0.5))))
    seconds = min(seconds, 3600.0)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ctx.should_stop():
            return Result(flow=None, record={"waited_seconds": seconds, "interrupted": True})
        time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
    return Result(record={"waited_seconds": round(seconds, 3)})


register(BlockType(
    key="flow.wait", name="Wait", plain="Pause before the next block",
    summary="Real time only. Neural time does not advance while waiting - use"
            " 'Let the network run' for that.",
    category="flow", run=_run_wait,
    inputs=(Port("seconds", "Seconds", "How long to pause", "number",
                 tip="Leave unconnected to use the value set on the block."),),
    params=(
        Param("seconds", "Pause", "How long to wait before continuing",
              "Wall-clock seconds. Useful to let a motor finish moving or a"
              " sensor settle before reading it again.",
              kind="number", default=0.5, minimum=0, maximum=3600, step=0.1, unit="s"),
    ),
))


def _run_branch(ctx, node, inputs):
    condition = as_bool(inputs.get("when"))
    return Result(flow="yes" if condition else "no", record={"taken": "yes" if condition else "no"})


register(BlockType(
    key="flow.branch", name="If / then", plain="Do one thing or another",
    summary="Sends the run down one of two paths depending on a yes/no input."
            " This is the 'what to do' block.",
    category="flow", run=_run_branch,
    flow_out=("yes", "no"),
    flow_labels={
        "yes": {"label": "Yes", "plain": "Runs when the condition is true"},
        "no": {"label": "No", "plain": "Runs when the condition is false"},
    },
    inputs=(Port("when", "Condition", "The yes/no value to test", "boolean", required=True,
                 tip="Connect a comparison, a spike gate, or any yes/no output."),),
))


def _run_switch(ctx, node, inputs):
    label = as_text(inputs.get("label"), "").strip().lower()
    routes = [str(node.params.get(f"case{i}", "")).strip().lower() for i in (1, 2, 3, 4)]
    for index, route in enumerate(routes, start=1):
        if route and route == label:
            return Result(flow=f"case{index}", record={"label": label, "matched": route})
    return Result(flow="other", record={"label": label, "matched": None})


register(BlockType(
    key="flow.switch", name="Route by label", plain="Send the run down a path that matches a word",
    summary="Compares a text value against up to four labels and follows the"
            " matching path. Anything unmatched goes to Other.",
    category="flow", run=_run_switch,
    flow_out=("case1", "case2", "case3", "case4", "other"),
    flow_labels={
        "case1": {"label": "Match 1", "plain": "Label 1 matched"},
        "case2": {"label": "Match 2", "plain": "Label 2 matched"},
        "case3": {"label": "Match 3", "plain": "Label 3 matched"},
        "case4": {"label": "Match 4", "plain": "Label 4 matched"},
        "other": {"label": "Other", "plain": "Nothing matched"},
    },
    inputs=(Port("label", "Label", "The text to match", "text", required=True,
                 tip="Connect the Choice block, or any text output."),),
    params=tuple(
        Param(f"case{i}", f"Label {i}", f"Text that follows path {i}",
              "Matching ignores capitals and surrounding spaces. Leave blank to disable this path.",
              kind="text", default="", placeholder="forward")
        for i in (1, 2, 3, 4)
    ),
))


def _run_repeat(ctx, node, inputs):
    times = int(max(1, min(100000, as_number(inputs.get("times", node.params.get("times", 5)), 5))))
    completed = 0
    for index in range(times):
        if ctx.should_stop():
            break
        ctx.set_output(node.id, "index", float(index + 1))
        ctx.set_output(node.id, "progress", float((index + 1) / times))
        outcome = ctx.run_branch(node.id, "body")
        completed += 1
        if outcome in ("stopped", "break"):
            break
    return Result(flow="done", record={"requested": times, "completed": completed})


register(BlockType(
    key="loop.repeat", name="Repeat N times", plain="Do the same thing a fixed number of times",
    summary="Runs everything on Each time, the given number of times, then"
            " continues from Finished.",
    category="flow", run=_run_repeat,
    flow_out=("body", "done"),
    flow_labels={
        "body": {"label": "Each time", "plain": "Runs once per repetition"},
        "done": {"label": "Finished", "plain": "Runs after the last repetition"},
    },
    inputs=(Port("times", "Repetitions", "How many times to repeat", "number",
                 tip="Leave unconnected to use the value set on the block."),),
    outputs=(
        Port("index", "Repetition", "Which repetition is running, counting from 1", "number",
             tip="Use it to vary what happens on each pass, such as stepping a servo."),
        Port("progress", "Progress", "How far through, from 0 to 1", "number",
             tip="Handy for driving a display or a ramp."),
    ),
    params=(
        Param("times", "Repetitions", "How many passes to run",
              "Counted per visit to this block. A Stop block inside the loop ends it early.",
              kind="number", default=5, minimum=1, maximum=100000, step=1),
    ),
))


def _run_until(ctx, node, inputs):
    limit = int(max(1, min(100000, as_number(node.params.get("limit", 100), 100))))
    invert = bool(node.params.get("invert", False))
    completed = 0
    met = False
    for index in range(limit):
        if ctx.should_stop():
            break
        ctx.set_output(node.id, "index", float(index + 1))
        outcome = ctx.run_branch(node.id, "body")
        completed += 1
        if outcome in ("stopped", "break"):
            break
        condition = as_bool(ctx.value(node.id, "until"))
        if invert:
            condition = not condition
        if condition:
            met = True
            break
    return Result(
        flow="done",
        record={"iterations": completed, "condition_met": met, "limit": limit},
    )


register(BlockType(
    key="loop.until", name="Repeat until", plain="Keep going until something becomes true",
    summary="Runs Each time until the condition is met or the safety limit is"
            " reached. The condition is checked after each pass.",
    category="flow", run=_run_until,
    flow_out=("body", "done"),
    flow_labels={
        "body": {"label": "Each time", "plain": "Runs once per pass"},
        "done": {"label": "Finished", "plain": "Runs when the condition is met or the limit is hit"},
    },
    inputs=(Port("until", "Stop when", "The condition that ends the loop", "boolean", required=True,
                 tip="Checked after each pass. Connect a comparison or a goal check."),),
    outputs=(Port("index", "Pass", "Which pass is running, counting from 1", "number",
                  tip="Counts passes through the loop body."),),
    params=(
        Param("limit", "Safety limit", "Most passes to allow",
              "Prevents a workflow from looping forever when the condition never"
              " becomes true. The loop always stops here at the latest.",
              kind="number", default=100, minimum=1, maximum=100000, step=1),
        Param("invert", "Stop when false instead", "Flip the meaning of the condition",
              "With this on, the loop ends when the condition becomes false.",
              kind="bool", default=False, advanced=True),
    ),
))


def _run_every(ctx, node, inputs):
    interval = max(0.0, as_number(node.params.get("interval", 1.0), 1.0))
    state = ctx.state(node.id)
    now = time.monotonic()
    last = state.get("last")
    if last is not None and now - last < interval:
        return Result(flow="skipped", record={"seconds_since": round(now - last, 3), "ran": False})
    state["last"] = now
    return Result(flow="ready", record={"ran": True, "interval": interval})


register(BlockType(
    key="flow.every", name="At most every", plain="Rate limit: only run this often",
    summary="Passes the run along only when enough real time has gone by since"
            " the last time. Otherwise it takes the Skipped path.",
    category="flow", run=_run_every,
    flow_out=("ready", "skipped"),
    flow_labels={
        "ready": {"label": "Ready", "plain": "Enough time has passed"},
        "skipped": {"label": "Too soon", "plain": "Not yet; nothing was done"},
    },
    params=(
        Param("interval", "Minimum gap", "Shortest time between runs",
              "Useful to stop a workflow hammering a motor, a webhook or a"
              " serial device faster than it can cope with.",
              kind="number", default=1.0, minimum=0, maximum=3600, step=0.1, unit="s"),
    ),
))


def _run_stop(ctx, node, inputs):
    reason = str(node.params.get("reason", "")) or "Stop block reached"
    outcome = str(node.params.get("outcome", "finished"))
    return Result(flow=None, stop={"reason": reason, "outcome": outcome},
                  record={"reason": reason, "outcome": outcome})


register(BlockType(
    key="flow.stop", name="Stop the run", plain="End the whole run here",
    summary="Ends the run immediately and records why. Use it for 'task"
            " complete' and for safety conditions alike.",
    category="flow", run=_run_stop, flow_out=(),
    params=(
        Param("outcome", "How it ended", "What to record as the outcome",
              "Shows on the run summary and in the project history.",
              kind="select", default="finished",
              options=(
                  ("finished", "Finished", "Normal completion"),
                  ("succeeded", "Goal reached", "The task was completed successfully"),
                  ("failed", "Failed", "The task could not be completed"),
                  ("aborted", "Aborted", "Stopped for safety or by a guard"),
              )),
        Param("reason", "Reason", "A short note explaining the stop",
              "Free text. It is written into the run log so a later reader knows what happened.",
              kind="text", default="", placeholder="target reached"),
    ),
))


def _run_break(ctx, node, inputs):
    return Result(flow=None, stop={"break": True}, record={"left_loop": True})


register(BlockType(
    key="flow.break", name="Leave the loop", plain="Jump out of the surrounding repeat",
    summary="Ends the nearest enclosing loop and continues from that loop's"
            " Finished path. Outside a loop it simply ends the current chain.",
    category="flow", run=_run_break, flow_out=(),
))


def _run_note(ctx, node, inputs):
    return Result(record={"note": str(node.params.get("text", ""))})


register(BlockType(
    key="flow.note", name="Note", plain="A comment for whoever reads this workflow",
    summary="Does nothing. Records its text in the run log so the log explains"
            " itself later.",
    category="flow", run=_run_note,
    params=(
        Param("text", "Note", "What you want to remember about this part",
              "Written into the run log each time the run passes through.",
              kind="code", default="", placeholder="Why this branch exists..."),
    ),
))


# ============================================================== input sources
def _run_number(ctx, node, inputs):
    value = as_number(node.params.get("value", 0))
    return Result(outputs={"value": value}, record={"value": value})


register(BlockType(
    key="input.number", name="Fixed number", plain="A value you set by hand",
    summary="A constant, or a slider you can move while a run is going.",
    category="source", run=_run_number,
    outputs=(Port("value", "Value", "The number set on this block", "number",
                  tip="Changing the block while a run is going takes effect on the next pass."),),
    params=(
        Param("value", "Value", "The number to send", "Any finite number.",
              kind="number", default=1.0, step=0.01),
    ),
))


def _run_text(ctx, node, inputs):
    text = str(node.params.get("value", ""))
    return Result(outputs={"value": text}, record={"value": text})


register(BlockType(
    key="input.text", name="Fixed text", plain="A word or label you set by hand",
    summary="Useful for naming a trial, a target label or a serial command.",
    category="source", run=_run_text,
    outputs=(Port("value", "Text", "The text set on this block", "text",
                  tip="Feeds Route by label, Send serial line, and the run log."),),
    params=(
        Param("value", "Text", "What to send", "Plain text. No formatting is applied.",
              kind="text", default="", placeholder="forward"),
    ),
))


def _run_random(ctx, node, inputs):
    state = ctx.state(node.id)
    rng = state.get("rng")
    if rng is None:
        seed = node.params.get("seed", 0)
        rng = state["rng"] = np.random.default_rng(int(seed) if seed else None)
    low = as_number(node.params.get("low", 0.0))
    high = as_number(node.params.get("high", 1.0), 1.0)
    if high <= low:
        high = low + 1.0
    value = float(rng.uniform(low, high))
    return Result(outputs={"value": value}, record={"value": round(value, 6)})


register(BlockType(
    key="input.random", name="Random number", plain="Noise, for control experiments",
    summary="A fresh random value each time. The honest way to check whether a"
            " result depends on the network or would have happened anyway.",
    category="source", run=_run_random,
    outputs=(Port("value", "Value", "A new random number each pass", "number",
                  tip="Set a seed to make a control run repeatable."),),
    params=(
        Param("low", "Lowest", "Smallest value it can produce", "Inclusive lower bound.",
              kind="number", default=0.0, step=0.01),
        Param("high", "Highest", "Largest value it can produce", "Exclusive upper bound.",
              kind="number", default=1.0, step=0.01),
        Param("seed", "Seed", "Fix the sequence so a control run repeats exactly",
              "0 means a different sequence every run. Any other number repeats exactly.",
              kind="number", default=0, minimum=0, step=1, advanced=True),
    ),
    tips=("Swap a real sensor for this block to test whether a workflow's"
          " behaviour actually depends on what it is sensing.",),
))


def _run_clock(ctx, node, inputs):
    elapsed = time.monotonic() - ctx.started_at
    return Result(
        outputs={
            "seconds": float(elapsed),
            "iteration": float(ctx.iteration),
            "brain_ms": float(ctx.session.brain.sim_ms) if ctx.session else 0.0,
        },
        record={"seconds": round(elapsed, 3), "iteration": ctx.iteration},
    )


register(BlockType(
    key="input.clock", name="Run clock", plain="How long the run has been going",
    summary="Real seconds since the run started, which iteration this is, and"
            " how much neural time the network has accumulated.",
    category="source", run=_run_clock,
    outputs=(
        Port("seconds", "Real seconds", "Wall-clock time since the run started", "number",
             tip="Compare against a limit to end a run after a set time."),
        Port("iteration", "Iteration", "Which top-level pass this is", "number",
             tip="Counts from 1."),
        Port("brain_ms", "Neural time", "Milliseconds of neural time simulated so far", "number",
             tip="Neural time only advances inside 'Let the network run'."),
    ),
))


def _run_file_value(ctx, node, inputs):
    result = links.read_value(
        node.params.get("path", ""),
        node.params.get("field") or None,
        as_number(node.params.get("fallback", 0.0)),
    )
    return Result(outputs={"value": float(result["value"]), "found": bool(result["ok"]), "details": result},
                  record=result)


register(BlockType(
    key="input.file", name="Read a file", plain="Take a number from a file on disk",
    summary="Reads the last line of a text or CSV file, or a named field from"
            " a JSON file. The simplest way to let another program feed this one.",
    category="source", run=_run_file_value,
    outputs=(
        Port("value", "Value", "The number read from the file", "number",
             tip="Falls back to the value you set if the file is missing or unreadable."),
        Port("found", "Read successfully", "Whether the file gave a usable number", "boolean",
             tip="Branch on this to handle a missing file explicitly."),
        Port("details", "Details", "Path, status and any error text", "record",
             tip="Send it to the run log when you are debugging a data feed."),
    ),
    params=(
        Param("path", "File", "Where the file is", "Absolute path, or relative to where the platform was started.",
              kind="path", default="", placeholder="/home/pi/sensor.txt"),
        Param("field", "JSON field", "Which field to read from a JSON file",
              "Dotted path, for example 'sensor.distance'. Leave blank for plain text or CSV.",
              kind="text", default="", placeholder="sensor.distance"),
        Param("fallback", "If unavailable", "Value to use when the file cannot be read",
              "Keeps a workflow running when a feed drops out, instead of failing the run.",
              kind="number", default=0.0, step=0.01),
    ),
))


def _run_http_value(ctx, node, inputs):
    result = links.http_request(
        node.params.get("url", ""), "GET", None, None,
        as_number(node.params.get("timeout", 5.0), 5.0),
    )
    value = as_number(node.params.get("fallback", 0.0))
    field = str(node.params.get("field", "")).strip()
    if result["ok"]:
        try:
            import json as _json

            data = _json.loads(result["body"])
            for part in (field.split(".") if field else []):
                data = data[int(part)] if isinstance(data, list) else data[part]
            value = as_number(data, value)
        except Exception as exc:
            result["parse_error"] = str(exc)
    return Result(outputs={"value": value, "ok": bool(result["ok"]), "details": result}, record=result)


register(BlockType(
    key="input.http", name="Read a web endpoint", plain="Fetch a number over the network",
    summary="Sends a GET request and pulls one number out of the JSON reply.",
    category="source", run=_run_http_value,
    outputs=(
        Port("value", "Value", "The number from the reply", "number", tip="Falls back if the request fails."),
        Port("ok", "Request worked", "Whether the endpoint answered", "boolean", tip="Branch on this to handle outages."),
        Port("details", "Details", "Status code, timing and reply text", "record", tip="Useful when an endpoint misbehaves."),
    ),
    params=(
        Param("url", "Address", "The http or https address to read",
              "Only http and https are allowed. The reply is read as JSON.",
              kind="text", default="", placeholder="http://192.168.1.50/sensor"),
        Param("field", "JSON field", "Which field holds the number",
              "Dotted path, for example 'data.0.temperature'. Blank uses the whole reply.",
              kind="text", default="", placeholder="data.temperature"),
        Param("timeout", "Timeout", "How long to wait for a reply", "The block gives up after this and uses the fallback.",
              kind="number", default=5.0, minimum=0.1, maximum=60, step=0.5, unit="s"),
        Param("fallback", "If unavailable", "Value to use when the request fails",
              "Keeps the run going instead of failing on a dropped connection.",
              kind="number", default=0.0, step=0.01),
    ),
))


def _serial_link(ctx, node):
    port = str(node.params.get("port", ""))
    baud = int(as_number(node.params.get("baud", 115200), 115200))
    return ctx.device(
        ("serial", port, baud),
        lambda: links.SerialLink(port, baud, as_number(node.params.get("timeout", 1.0), 1.0),
                                 simulate=ctx.simulate_devices),
    )


def _run_serial_read(ctx, node, inputs):
    link = _serial_link(ctx, node)
    result = link.read_number(as_number(node.params.get("timeout", 1.0), 1.0),
                              as_number(node.params.get("fallback", 0.0)))
    return Result(
        outputs={
            "value": float(result["value"]),
            "line": result["line"],
            "received": bool(result["received"]),
            "details": result,
        },
        record=result,
    )


register(BlockType(
    key="input.serial", name="Read serial", plain="Take a reading from an Arduino or sensor board",
    summary="Waits for one line on the serial port and reads the first number"
            " on it. The whole line is available as text too.",
    category="source", run=_run_serial_read, needs=("serial",),
    outputs=(
        Port("value", "Value", "First number on the line", "number", tip="Commas and spaces separate fields; the first field is used."),
        Port("line", "Whole line", "Everything the device sent", "text", tip="Route it through Route by label for command-style devices."),
        Port("received", "Got a line", "Whether anything arrived before the timeout", "boolean", tip="Branch on this to detect a disconnected device."),
        Port("details", "Details", "Port state and raw result", "record", tip="Send to the log when debugging wiring."),
    ),
    params=(
        Param("port", "Port", "Which serial port to open",
              "On Linux usually /dev/ttyUSB0 or /dev/ttyACM0; on Windows COM3.",
              kind="select", default="", options=(("", "Choose a port", ""),)),
        Param("baud", "Speed", "Bits per second, must match the device",
              "9600 and 115200 are the usual Arduino settings. A mismatch produces garbage, not an error.",
              kind="select", default=115200,
              options=tuple((b, f"{b} baud", "") for b in links.BAUD_RATES)),
        Param("timeout", "Timeout", "How long to wait for a line", "The block continues with the fallback if nothing arrives.",
              kind="number", default=1.0, minimum=0.01, maximum=60, step=0.1, unit="s"),
        Param("fallback", "If nothing arrives", "Value to use on timeout", "Keeps the run going when a device goes quiet.",
              kind="number", default=0.0, step=0.01),
    ),
))


def _gpio(ctx, node):
    return ctx.device(("gpio",), lambda: gpio_io.open_controller(simulate=ctx.simulate_devices))


def _run_gpio_read(ctx, node, inputs):
    controller = _gpio(ctx, node)
    pin = gpio_io.check_pin(node.params.get("pin", 4))
    controller.setup_input(pin, str(node.params.get("pull", "none")))
    raw = controller.read(pin)
    value = (not raw) if bool(node.params.get("invert", False)) else raw
    return Result(
        outputs={"value": bool(value), "number": 1.0 if value else 0.0,
                 "details": {"pin": pin, "raw": bool(raw), "simulated": controller.simulated}},
        record={"pin": pin, "value": bool(value), "simulated": controller.simulated},
    )


register(BlockType(
    key="input.gpio", name="Read a pin", plain="Is this Raspberry Pi pin high or low?",
    summary="Reads one GPIO pin as yes/no. Use it for buttons, limit switches,"
            " and the output of any sensor that just closes a contact.",
    category="source", run=_run_gpio_read, needs=("gpio",),
    outputs=(
        Port("value", "High", "True when the pin reads high", "boolean", tip="Feed straight into If / then."),
        Port("number", "As a number", "1 when high, 0 when low", "number", tip="Use it to drive a sensory channel directly."),
        Port("details", "Details", "Pin number and raw reading", "record", tip="Shows whether the reading came from real hardware."),
    ),
    params=(
        Param("pin", "Pin", "Which GPIO pin to read",
              "BCM numbering, the numbers in a Raspberry Pi pinout diagram. The physical header position is shown beside each.",
              kind="pin", default=4, options=PIN_OPTIONS),
        Param("pull", "Resting state", "What the pin does when nothing is connected",
              "Pull-up holds it high until a switch pulls it to ground, which is how most buttons are wired. Pull-down is the opposite.",
              kind="select", default="none",
              options=(("none", "Floating", "No internal resistor"),
                       ("up", "Pull-up", "Idles high; a button to ground reads low"),
                       ("down", "Pull-down", "Idles low; a button to 3.3V reads high"))),
        Param("invert", "Read inverted", "Treat low as 'yes'", "Matches a button wired to ground with a pull-up.",
              kind="bool", default=False),
    ),
    caution="Raspberry Pi pins are 3.3 V. Connecting 5 V to an input can destroy the board.",
))


def _run_ping(ctx, node, inputs):
    controller = _gpio(ctx, node)
    result = controller.ping(
        node.params.get("trigger", 23), node.params.get("echo", 24),
        as_number(node.params.get("timeout", 0.06), 0.06),
        as_number(node.params.get("maximum", 400.0), 400.0),
    )
    distance = result["distance_cm"]
    found = distance is not None
    return Result(
        outputs={
            "distance": float(distance) if found else as_number(node.params.get("fallback", 400.0), 400.0),
            "in_range": found,
            "details": result,
        },
        record=result,
    )


register(BlockType(
    key="input.ping", name="Distance sensor", plain="How far away is the nearest thing? (HC-SR04)",
    summary="Fires an ultrasonic pulse and times the echo. Reports centimetres."
            " This is the standard cheap robot range finder.",
    category="source", run=_run_ping, needs=("gpio",),
    outputs=(
        Port("distance", "Distance", "Centimetres to the nearest surface", "number",
             tip="Feed it into a sensory channel through 'Drive a sense' to let the network feel proximity."),
        Port("in_range", "Got an echo", "False when nothing echoed back", "boolean",
             tip="An open space and a missed reading look the same to the sensor; branch on this."),
        Port("details", "Details", "Echo time and status", "record", tip="Shows why a reading failed."),
    ),
    params=(
        Param("trigger", "Trigger pin", "Pin that sends the pulse", "Connect to the sensor's TRIG pin.",
              kind="pin", default=23, options=PIN_OPTIONS),
        Param("echo", "Echo pin", "Pin that receives the echo",
              "Connect to the sensor's ECHO pin through a voltage divider: the module drives 5 V and the Pi expects 3.3 V.",
              kind="pin", default=24, options=PIN_OPTIONS),
        Param("maximum", "Maximum range", "Ignore echoes beyond this distance",
              "HC-SR04 modules are unreliable past about 400 cm.",
              kind="number", default=400.0, minimum=2, maximum=1000, step=1, unit="cm"),
        Param("timeout", "Echo timeout", "How long to wait for the echo",
              "60 ms covers the full range of the sensor. Shorter is faster but drops far readings.",
              kind="number", default=0.06, minimum=0.005, maximum=1.0, step=0.005, unit="s", advanced=True),
        Param("fallback", "If no echo", "Distance to report when nothing comes back",
              "Usually the maximum range, meaning 'nothing nearby'.",
              kind="number", default=400.0, minimum=0, maximum=1000, step=1, unit="cm"),
    ),
    caution="Wire ECHO through a divider or level shifter. 5 V on a Pi input can destroy the board.",
))


def _run_audio(ctx, node, inputs):
    bands = int(max(1, min(32, as_number(node.params.get("bands", 6), 6))))
    microphone = ctx.device(
        ("audio", bands), lambda: links.Microphone(bands, simulate=ctx.simulate_devices)
    )
    result = microphone.listen(as_number(node.params.get("seconds", 0.1), 0.1))
    return Result(
        outputs={"bands": list(result["bands"]), "level": float(result["level"]), "details": result},
        record=result,
    )


register(BlockType(
    key="input.audio", name="Listen", plain="Sound level and frequency bands from a microphone",
    summary="Records a short buffer and reduces it to band energies, the form"
            " Johnston's organ subgroups are driven with.",
    category="source", run=_run_audio, needs=("audio",),
    outputs=(
        Port("bands", "Bands", "Energy in each frequency band, low to high", "vector",
             tip="Connect to 'Drive a sense across subtypes' with Johnston's organ selected."),
        Port("level", "Loudness", "Overall level of the buffer", "number", tip="A single number for simple thresholds."),
        Port("details", "Details", "Sample rate, duration and whether it was simulated", "record", tip=""),
    ),
    params=(
        Param("bands", "Bands", "How many frequency bands to split the sound into",
              "Six matches the six Johnston's organ subgroups (JO-A to JO-F).",
              kind="number", default=6, minimum=1, maximum=32, step=1),
        Param("seconds", "Buffer", "How much audio to record each time",
              "Longer gives finer frequency resolution but slows each pass.",
              kind="number", default=0.1, minimum=0.01, maximum=5.0, step=0.01, unit="s"),
    ),
))


# ============================================================== vision sources
def _vision_source(ctx, node, kind, options):
    key = ("vision", kind, tuple(sorted((k, str(v)) for k, v in options.items())))
    return ctx.device(key, lambda: vision.open_source(kind, simulate=ctx.simulate_devices, **options))


def _capture(ctx, node, kind, options):
    source = _vision_source(ctx, node, kind, options)
    frame, info = source.read()
    ctx.preview(node.id, frame, info)
    return Result(
        outputs={"frame": frame, "details": info,
                 "brightness": float(np.asarray(frame, dtype=np.float32).mean() / 255)},
        record={k: v for k, v in info.items() if k != "frame"},
    )


def _size(node):
    return (
        int(as_number(node.params.get("width", 320), 320)),
        int(as_number(node.params.get("height", 180), 180)),
    )


SIZE_PARAMS = (
    Param("width", "Capture width", "How wide the captured picture is, in pixels",
          "The eye samples one pixel per receptor at its own column, so a bigger"
          " capture is not more detail to the network - only more work per pass.",
          kind="number", default=320, minimum=16, maximum=1920, step=1, unit="px", advanced=True),
    Param("height", "Capture height", "How tall the captured picture is, in pixels",
          "Same as width: this sets the picture handed to the sampler, not the"
          " resolution the network sees.",
          kind="number", default=180, minimum=16, maximum=1080, step=1, unit="px", advanced=True),
)

VISION_OUTPUTS = (
    Port("frame", "Picture", "The captured RGB frame", "image", required=True,
         tip="Connect to 'Show it to the eyes'."),
    Port("brightness", "Average brightness", "Mean pixel level, 0 to 1", "number",
         tip="A quick check that the capture is not black or blown out."),
    Port("details", "Details", "Source, size and whether it was simulated", "record", tip=""),
)


def _run_screen(ctx, node, inputs):
    region = None
    if bool(node.params.get("use_region", True)):
        region = (
            int(as_number(node.params.get("left", 0))),
            int(as_number(node.params.get("top", 0))),
            max(1, int(as_number(node.params.get("region_width", 640), 640))),
            max(1, int(as_number(node.params.get("region_height", 360), 360))),
        )
    return _capture(ctx, node, "screen",
                    {"region": region, "size": _size(node), "monitor": int(as_number(node.params.get("monitor", 1), 1))})


register(BlockType(
    key="vision.screen", name="Screen region", plain="Look at part of this computer's screen",
    summary="Grabs a fixed rectangle of the desktop every pass. Point it at a"
            " browser window, a game, or an instrument readout.",
    category="vision", run=_run_screen, needs=("screen",),
    outputs=VISION_OUTPUTS,
    params=(
        Param("use_region", "Use a fixed rectangle", "Capture part of the screen instead of all of it",
              "Turn this off to capture the whole monitor. A tight rectangle is"
              " faster and keeps irrelevant screen content out of the input.",
              kind="bool", default=True),
        Param("left", "Left edge", "Distance from the left of the monitor", "In screen pixels.",
              kind="number", default=0, minimum=0, maximum=10000, step=1, unit="px", depends=("use_region", True)),
        Param("top", "Top edge", "Distance from the top of the monitor", "In screen pixels.",
              kind="number", default=0, minimum=0, maximum=10000, step=1, unit="px", depends=("use_region", True)),
        Param("region_width", "Rectangle width", "How wide an area to grab", "In screen pixels.",
              kind="number", default=640, minimum=16, maximum=10000, step=1, unit="px", depends=("use_region", True)),
        Param("region_height", "Rectangle height", "How tall an area to grab", "In screen pixels.",
              kind="number", default=360, minimum=16, maximum=10000, step=1, unit="px", depends=("use_region", True)),
        Param("monitor", "Monitor", "Which display to capture from", "1 is the primary display.",
              kind="number", default=1, minimum=1, maximum=8, step=1, advanced=True),
        *SIZE_PARAMS,
    ),
    tips=("Pair this with the mouse output blocks to let the network work a"
          " window: look, decide, move, click, look again.",),
))


def _run_camera(ctx, node, inputs):
    return _capture(ctx, node, "camera",
                    {"index": int(as_number(node.params.get("index", 0))), "size": _size(node)})


register(BlockType(
    key="vision.camera", name="Camera", plain="Live video from a webcam or robot camera",
    summary="Reads one frame per pass from an attached camera. This is the"
            " input for anything that has to see where it is going.",
    category="vision", run=_run_camera, needs=("camera",),
    outputs=VISION_OUTPUTS,
    params=(
        Param("index", "Camera", "Which camera to open",
              "0 is the first camera the system lists. Try 1, 2 and so on for extra cameras.",
              kind="number", default=0, minimum=0, maximum=16, step=1),
        *SIZE_PARAMS,
    ),
))


def _run_image(ctx, node, inputs):
    return _capture(ctx, node, "image", {
        "path": str(node.params.get("path", "")), "size": _size(node),
        "loop": bool(node.params.get("loop", True)),
    })


register(BlockType(
    key="vision.image", name="Image file", plain="Show a picture, or step through a folder",
    summary="Loads one image per pass. Point it at a folder to run a whole set"
            " of training images in order.",
    category="vision", run=_run_image,
    outputs=VISION_OUTPUTS,
    params=(
        Param("path", "File or folder", "Where the pictures are",
              "A single image file, or a folder. A folder advances one image per pass, in name order.",
              kind="path", default="", placeholder="/home/user/training-images"),
        Param("loop", "Start again at the end", "Wrap around after the last image",
              "With this off, the run stops when the images run out.",
              kind="bool", default=True),
        *SIZE_PARAMS,
    ),
))


def _run_video(ctx, node, inputs):
    return _capture(ctx, node, "video", {
        "path": str(node.params.get("path", "")), "size": _size(node),
        "loop": bool(node.params.get("loop", True)),
    })


register(BlockType(
    key="vision.video", name="Video file", plain="Play a recording into the eyes",
    summary="Reads one video frame per pass. Good for replaying a recorded"
            " scene identically across several runs.",
    category="vision", run=_run_video, needs=("camera",),
    outputs=VISION_OUTPUTS,
    params=(
        Param("path", "Video file", "Where the video is", "Any format your OpenCV build can decode.",
              kind="path", default="", placeholder="/home/user/drive.mp4"),
        Param("loop", "Start again at the end", "Rewind when the video finishes",
              "With this off, the run stops at the end of the video.",
              kind="bool", default=True),
        *SIZE_PARAMS,
    ),
))


def _run_browser(ctx, node, inputs):
    return _capture(ctx, node, "browser", {
        "url": str(node.params.get("url", "about:blank")), "size": _size(node),
        "viewport": (int(as_number(node.params.get("viewport_width", 1280), 1280)),
                     int(as_number(node.params.get("viewport_height", 720), 720))),
        "wait_ms": int(as_number(node.params.get("wait_ms", 800), 800)),
    })


register(BlockType(
    key="vision.browser", name="Web page", plain="Render a web page and look at it",
    summary="Loads a page in a headless browser and screenshots it, so a"
            " workflow can read a page on a machine with no desktop.",
    category="vision", run=_run_browser, needs=("browser",),
    outputs=VISION_OUTPUTS,
    params=(
        Param("url", "Address", "Which page to load", "The page is fetched over the network by a real browser engine.",
              kind="text", default="about:blank", placeholder="https://example.com"),
        Param("viewport_width", "Browser width", "Width of the simulated browser window", "Affects page layout.",
              kind="number", default=1280, minimum=320, maximum=3840, step=1, unit="px"),
        Param("viewport_height", "Browser height", "Height of the simulated browser window", "Affects how much of the page is visible.",
              kind="number", default=720, minimum=240, maximum=2160, step=1, unit="px"),
        Param("wait_ms", "Settle time", "How long to wait after loading before the screenshot",
              "Gives scripts and images time to finish.",
              kind="number", default=800, minimum=0, maximum=15000, step=100, unit="ms"),
        *SIZE_PARAMS,
    ),
    caution="This fetches a page over the network. Point it only at addresses you trust.",
))


def _run_pattern(ctx, node, inputs):
    return _capture(ctx, node, "pattern",
                    {"size": _size(node), "speed": as_number(node.params.get("speed", 0.25), 0.25)})


register(BlockType(
    key="vision.pattern", name="Test pattern", plain="A known moving picture, for checking the eye works",
    summary="Drifting bars and rings with a marked corner. Use it to confirm"
            " the photoreceptor path responds before wiring up real hardware.",
    category="vision", run=_run_pattern,
    outputs=VISION_OUTPUTS,
    params=(
        Param("speed", "Drift speed", "How fast the pattern moves",
              "Cycles per second. Zero freezes it, which is useful for a static check.",
              kind="number", default=0.25, minimum=0, maximum=10, step=0.05, unit="Hz"),
        *SIZE_PARAMS,
    ),
    tips=("A completely still picture gives photoreceptors very little to do."
          " If the network looks quiet, try a moving input first.",),
))


# ============================================================= encoding blocks
def _require_session(ctx):
    if ctx.session is None:
        raise RuntimeError(
            "No connectome is loaded. Open the Dashboard and load a dataset"
            " before running a workflow that touches the network."
        )
    return ctx.session


def _run_show_image(ctx, node, inputs):
    session = _require_session(ctx)
    frame = inputs.get("frame")
    if frame is None:
        raise ValueError("Connect a picture source to 'Show it to the eyes'")
    frame = np.asarray(frame)
    session.set_image(frame)
    receptors = len(session.brain.retina)
    colour = len(getattr(session.brain, "r8", []))
    record = {
        "size": list(frame.shape[1::-1]),
        "luminance_receptors": receptors,
        "colour_receptors": colour,
        "mean_level": round(float(frame.mean() / 255), 4),
    }
    ctx.preview(node.id, frame, {"kind": "eye"})
    return Result(outputs={"frame": frame, "details": record}, record=record)


register(BlockType(
    key="encode.eye", name="Show it to the eyes", plain="Send a picture into the photoreceptors",
    summary="Samples the picture at every mapped receptor's own retinotopic"
            " column: R1-R6 take brightness, R8 takes blue and green. The"
            " network sees a fly's sampling of the image, not the image.",
    category="encode", run=_run_show_image,
    inputs=(Port("frame", "Picture", "The image to look at", "image", required=True,
                 tip="Connect any vision source."),),
    outputs=(
        Port("frame", "Picture", "The same frame, passed through", "image", tip="Chain it to a preview or a log."),
        Port("details", "Details", "How many receptors were driven", "record", tip="Counts come from your loaded dataset."),
    ),
    tips=(
        "Each receptor reads one pixel at its inferred column. Unmapped"
        " receptors get no invented input.",
        "The picture is held until the next 'Let the network run', which is"
        " when it actually reaches the cells.",
    ),
))


def _run_drive_channel(ctx, node, inputs):
    session = _require_session(ctx)
    key = str(node.params.get("channel", ""))
    value = as_number(inputs.get("value", node.params.get("value", 0.0)))
    stimulus = session.add_stimulus(
        key, value,
        side=str(node.params.get("side", "both")),
        curve=str(node.params.get("curve", "saturating")),
        current=as_number(node.params.get("current", 0)) or None,
        low=as_number(node.params.get("low", 0.0)),
        high=as_number(node.params.get("high", 1.0), 1.0),
        source=node.label or node.id,
    )
    record = {**stimulus.summary(), "reading": value,
              "normalised": float(np.clip((value - as_number(node.params.get("low", 0.0)))
                                          / max(1e-9, as_number(node.params.get("high", 1.0), 1.0)
                                                - as_number(node.params.get("low", 0.0))), 0, 1))}
    return Result(outputs={"details": record, "cells": float(len(stimulus.cells))}, record=record)


RANGE_PARAMS = (
    Param("low", "Reading at rest", "The reading that means 'nothing happening'",
          "Sets where the sensor's range starts. A distance sensor might rest at 400 cm and get smaller as something approaches.",
          kind="number", default=0.0, step=0.01),
    Param("high", "Reading at full", "The reading that means 'as strong as it gets'",
          "Readings are clamped to this range, so an out-of-range value drives the channel fully rather than breaking the run.",
          kind="number", default=1.0, step=0.01),
)

DRIVE_PARAMS = (
    Param("side", "Which side", "Drive the left cells, the right cells, or both",
          "Driving one side only is how you give the network something asymmetric to respond to.",
          kind="select", default="both", options=SIDES),
    Param("curve", "Response shape", "How a reading becomes current",
          "Saturating matches the photoreceptor adapter: small changes near zero"
          " matter more than the same change at full scale. Straight is proportional throughout.",
          kind="select", default="saturating",
          options=(("saturating", "Saturating", "Sensitive near zero, flattens at the top"),
                   ("linear", "Straight", "Proportional across the whole range"))),
    Param("current", "Peak current", "Strength at full scale, in millivolt equivalents",
          "0 uses the channel's own default. Larger values drive harder; there"
          " is no calibration that makes any particular number correct.",
          kind="number", default=0, minimum=0, maximum=200, step=1, unit="mV", advanced=True),
)


register(BlockType(
    key="encode.channel", name="Drive a sense", plain="Turn a reading into activity on real sensory cells",
    summary="Takes one number and drives a named group of sensory cells with"
            " it. This is how a distance sensor, a temperature probe or a pin"
            " reading becomes something the network can respond to.",
    category="encode", run=_run_drive_channel,
    inputs=(Port("value", "Reading", "The value to turn into neural drive", "number", required=True,
                 tip="Any number source. It is rescaled using the range set below."),),
    outputs=(
        Port("details", "Details", "Which cells were driven and how hard", "record", tip="Send it to the log to audit a run."),
        Port("cells", "Cells driven", "How many cells this reached", "number", tip="Zero means the channel is absent from your dataset."),
    ),
    params=(channel_param(role="input"), *RANGE_PARAMS, *DRIVE_PARAMS),
    tips=(
        "Pick the sense that matches the physical quantity: distance and touch"
        " onto mechanosensory cells, temperature onto thermosensory cells.",
        "The drive reaches the cells at the next 'Let the network run'.",
    ),
))


def _run_drive_vector(ctx, node, inputs):
    session = _require_session(ctx)
    key = str(node.params.get("channel", ""))
    values = as_vector(inputs.get("values"))
    if not values.size:
        values = np.zeros(1)
    stimulus = session.add_stimulus(
        key, values,
        side=str(node.params.get("side", "both")),
        curve=str(node.params.get("curve", "saturating")),
        current=as_number(node.params.get("current", 0)) or None,
        low=as_number(node.params.get("low", 0.0)),
        high=as_number(node.params.get("high", 1.0), 1.0),
        groups_by_subtype=bool(node.params.get("by_subtype", True)),
        source=node.label or node.id,
    )
    record = {**stimulus.summary(), "values": [round(float(v), 4) for v in values[:32]]}
    return Result(outputs={"details": record, "cells": float(len(stimulus.cells))}, record=record)


register(BlockType(
    key="encode.vector", name="Drive a sense across subtypes",
    plain="Send several readings into one sense at once",
    summary="Spreads a list of values across a channel's named subtypes - for"
            " example six sound bands onto the six Johnston's organ groups, or"
            " a row of sensors across a bristle field.",
    category="encode", run=_run_drive_vector,
    inputs=(Port("values", "Readings", "A list of values, one per subtype", "vector", required=True,
                 tip="Connect the Listen block, a population readout, or any list of numbers."),),
    outputs=(
        Port("details", "Details", "Which cells were driven and how hard", "record", tip=""),
        Port("cells", "Cells driven", "How many cells this reached", "number", tip=""),
    ),
    params=(
        channel_param(role="input"),
        Param("by_subtype", "Match values to subtypes", "Give each named subtype its own value",
              "With this on, value 1 goes to the first subtype by name, value 2"
              " to the second, and so on. With it off the values are spread"
              " evenly across the cells in index order.",
              kind="bool", default=True),
        *RANGE_PARAMS, *DRIVE_PARAMS,
    ),
))


def _run_teach(ctx, node, inputs):
    session = _require_session(ctx)
    if not as_bool(inputs.get("when", True)):
        return Result(outputs={"delivered": False, "details": {}}, record={"delivered": False})
    key = str(node.params.get("channel", "teach.pam11"))
    stimulus = session.add_teaching_pulse(
        key,
        current=as_number(node.params.get("current", 0)) or None,
        source=node.label or node.id,
    )
    record = {**stimulus.summary(), "delivered": True,
              "note": "Engineered current into identified modulatory cells. Not a"
                      " reward or a punishment, and nothing is experienced."}
    return Result(outputs={"delivered": True, "details": record}, record=record)


register(BlockType(
    key="encode.teach", name="Teaching pulse", plain="Send the 'that was right' or 'that was wrong' signal",
    summary="Injects current into identified dopaminergic cells during the next"
            " network step. Paired with recent Kenyon-cell activity, this is"
            " what changes synapses.",
    category="encode", run=_run_teach,
    inputs=(Port("when", "Only when", "Deliver the pulse only if this is true", "boolean",
                 tip="Leave unconnected to deliver every time. Connect a goal check to teach only on the right passes."),),
    outputs=(
        Port("delivered", "Delivered", "Whether a pulse was queued", "boolean", tip="False when the condition blocked it."),
        Port("details", "Details", "Which cells, and how hard", "record", tip=""),
    ),
    params=(
        channel_param(role="modulatory", default="teach.pam11"),
        Param("current", "Pulse strength", "Strength of the injected current",
              "0 uses the run's configured strength. There is no calibration that makes a particular value correct.",
              kind="number", default=0, minimum=0, maximum=200, step=1, unit="mV", advanced=True),
    ),
    tips=(
        "Timing matters: the rule responds to cue activity followed by dopamine,"
        " so the pulse should land in the same step as, or just after, the input.",
        "Reward-linked and punishment-linked are labels from the behavioural"
        " literature about flies. They describe the cells, not this program.",
    ),
    caution="This models neither pleasure nor pain. It is a current injection"
            " into real cell types with published roles in fly learning.",
))


# ================================================================ brain blocks
def _run_step(ctx, node, inputs):
    session = _require_session(ctx)
    duration = as_number(inputs.get("duration", node.params.get("duration", 500.0)), 500.0)
    learning = bool(node.params.get("learning", True)) and not ctx.frozen
    result = session.step(
        duration,
        learning=learning,
        pulse_ms=as_number(node.params.get("pulse", 200.0), 200.0),
    )
    ctx.after_step(result)
    return Result(
        outputs={
            "spikes": result.counts,
            "total": float(result.total_spikes),
            "details": result.summary(),
            "changed_edges": float(result.memory.get("changed_edges", 0)),
        },
        record=result.summary(),
    )


register(BlockType(
    key="brain.step", name="Let the network run", plain="Advance neural time and let the signals propagate",
    summary="Everything queued by the encoder blocks arrives here. The whole"
            " retained graph integrates for the requested neural time, then the"
            " decoder blocks can read the result.",
    category="brain", run=_run_step,
    inputs=(
        Port("duration", "Neural time", "How much neural time to simulate, in milliseconds", "number",
             tip="Leave unconnected to use the value set on the block."),
        Port("stimulus", "Extra drive", "Optional stimulus records, for ordering only", "stimulus", multiple=True,
             tip="Encoder blocks queue their drive directly; connect them here purely to make the order obvious on the canvas."),
    ),
    outputs=(
        Port("spikes", "Spike counts", "What every cell did during this step", "spikes", required=True,
             tip="Connect to any readout block."),
        Port("total", "Total spikes", "Spikes across the whole network", "number", tip="A quick liveness check."),
        Port("changed_edges", "Synapses changed", "How many plastic connections differ from baseline", "number",
             tip="Zero with learning on usually means no dopamine arrived while cue activity was still eligible."),
        Port("details", "Details", "Timing, stimuli delivered and memory state", "record", tip=""),
    ),
    params=(
        Param("duration", "Neural time", "Milliseconds of neural time per pass",
              "This is simulated time, not real time. More gives signals longer"
              " to travel and eligibility traces longer to overlap with dopamine,"
              " at a proportional cost in computation.",
              kind="number", default=500.0, minimum=0.1, maximum=60000, step=10, unit="ms"),
        Param("learning", "Allow synapses to change", "Let the plasticity rule write to memory",
              "Turn it off for a frozen control: identical network, identical"
              " inputs, but nothing is allowed to change.",
              kind="bool", default=True),
        Param("pulse", "Teaching pulse length", "How long a teaching pulse lasts inside this step",
              "Clamped to the step length. The pulse starts at the beginning of the step.",
              kind="number", default=200.0, minimum=0.1, maximum=60000, step=10, unit="ms", advanced=True),
    ),
    tips=(
        "Neural time is decoupled from real time. A 500 ms step takes as long"
        " to compute as it takes; it does not wait for half a second to pass.",
    ),
))


def _run_save(ctx, node, inputs):
    session = _require_session(ctx)
    if not as_bool(inputs.get("when", True)):
        return Result(outputs={"saved": False, "details": {}}, record={"saved": False})
    label = as_text(inputs.get("label"), str(node.params.get("label", "")) or f"auto-{ctx.iteration}")
    record = ctx.save_model(label, str(node.params.get("notes", "")))
    return Result(outputs={"saved": True, "details": record}, record=record)


register(BlockType(
    key="brain.save", name="Save the network", plain="Write everything the network has learned to a file",
    summary="Saves membrane state, eligibility traces and every synaptic"
            " efficacy, with a label you choose. Appears in the Model library.",
    category="brain", run=_run_save,
    inputs=(
        Port("when", "Only when", "Save only if this is true", "boolean",
             tip="Leave unconnected to save on every pass. Connect a goal check to save only successful runs."),
        Port("label", "Label", "Name for this snapshot", "text", tip="Overrides the label set on the block."),
    ),
    outputs=(
        Port("saved", "Saved", "Whether a file was written", "boolean", tip=""),
        Port("details", "Details", "File name, size, checksum and memory state", "record", tip="The checksum is how a later run proves it loaded the same model."),
    ),
    params=(
        Param("label", "Label", "Name this snapshot", "Shown in the Model library. Blank auto-numbers by iteration.",
              kind="text", default="", placeholder="after first success"),
        Param("notes", "Notes", "What was happening when this was saved",
              "Free text stored beside the file. Worth filling in; a folder of unlabelled checkpoints is useless a week later.",
              kind="code", default="", placeholder="10 trials, reward on left turns"),
    ),
))


def _run_plasticity(ctx, node, inputs):
    session = _require_session(ctx)
    frozen = bool(node.params.get("frozen", False))
    if "frozen" in inputs and inputs.get("frozen") is not None:
        frozen = as_bool(inputs.get("frozen"))
    session.brain.weights_frozen = frozen
    ctx.frozen = frozen
    return Result(outputs={"frozen": frozen}, record={"frozen": frozen})


register(BlockType(
    key="brain.plasticity", name="Freeze or unfreeze learning", plain="Lock the synapses, or let them change again",
    summary="While frozen, the network still runs and still responds, but no"
            " synaptic efficacy is allowed to move. This is the control"
            " condition any learning claim has to be compared against.",
    category="brain", run=_run_plasticity,
    inputs=(Port("frozen", "Freeze", "True to lock synapses", "boolean",
                 tip="Leave unconnected to use the setting on the block."),),
    outputs=(Port("frozen", "Now frozen", "The state after this block ran", "boolean", tip=""),),
    params=(
        Param("frozen", "Freeze synapses", "Stop the plasticity rule writing anything",
              "Freezing mid-run is how you test whether behaviour depends on"
              " changes made so far, or on the network's fixed wiring.",
              kind="bool", default=True),
    ),
))


def _run_reset(ctx, node, inputs):
    session = _require_session(ctx)
    if not as_bool(inputs.get("when", True)):
        return Result(outputs={"reset": False}, record={"reset": False})
    keep = bool(node.params.get("keep_memory", True))
    session.reset(keep_memory=keep)
    return Result(outputs={"reset": True}, record={"reset": True, "kept_memory": keep})


register(BlockType(
    key="brain.reset", name="Reset the network", plain="Clear activity between trials",
    summary="Returns membrane voltages, traces and queues to their starting"
            " values. Choose whether learned synaptic changes survive.",
    category="brain", run=_run_reset,
    inputs=(Port("when", "Only when", "Reset only if this is true", "boolean",
                 tip="Leave unconnected to reset every time this block is reached."),),
    outputs=(Port("reset", "Reset", "Whether the reset happened", "boolean", tip=""),),
    params=(
        Param("keep_memory", "Keep what was learned", "Preserve synaptic changes across the reset",
              "On: activity is cleared but memory survives, which is what you"
              " want between trials. Off: the network returns to its baseline"
              " wiring, which is how you start a fresh subject.",
              kind="bool", default=True),
    ),
))


# =============================================================== readout blocks
def _run_read_rate(ctx, node, inputs):
    session = _require_session(ctx)
    key = str(node.params.get("channel", ""))
    side = str(node.params.get("side", "both"))
    details = session.read_channel(key)
    value = {"both": details["hz"], "left": details["left_hz"], "right": details["right_hz"]}[side]
    return Result(
        outputs={"hz": float(value), "spikes": float(details["spikes"]), "details": details},
        record={"channel": key, "side": side, "hz": round(float(value), 3)},
    )


register(BlockType(
    key="decode.rate", name="Read a cell group", plain="How hard is this group of cells firing?",
    summary="Mean firing rate across a named group over the last network step."
            " Mean, not total, so a large population does not outweigh a small"
            " one just by being bigger.",
    category="decode", run=_run_read_rate,
    inputs=(Port("spikes", "Spike counts", "The result of a network step", "spikes",
                 tip="Connect 'Let the network run' so the order is explicit."),),
    outputs=(
        Port("hz", "Rate", "Mean spikes per second", "number", tip="Compare it against a threshold to make a decision."),
        Port("spikes", "Total spikes", "Spikes summed over the group", "number", tip="Use the rate unless you specifically want counts."),
        Port("details", "Details", "Left, right, difference, cells and active cells", "record", tip=""),
    ),
    params=(channel_param(), Param("side", "Which side", "Read the left cells, the right cells, or both",
                                   "Descending and motor groups are usually bilateral; one side at a time is how you get a turn signal.",
                                   kind="select", default="both", options=SIDES)),
))


def _run_read_difference(ctx, node, inputs):
    session = _require_session(ctx)
    key = str(node.params.get("channel", "motor.steering"))
    value = session.read_differential(key)
    scale = as_number(node.params.get("scale", 1.0), 1.0)
    details = session.read_channel(key)
    return Result(
        outputs={"value": float(value * scale), "raw_hz": float(value), "details": details},
        record={"channel": key, "difference_hz": round(float(value), 3), "scaled": round(float(value * scale), 3)},
    )


register(BlockType(
    key="decode.difference", name="Left-right difference", plain="Which way? Turn signal from a bilateral pair",
    summary="Right-side rate minus left-side rate. Positive means the right"
            " cells are more active. For DNa02 this is the fly's steering"
            " signal; for any pair it is a signed two-sided readout.",
    category="decode", run=_run_read_difference,
    inputs=(Port("spikes", "Spike counts", "The result of a network step", "spikes", tip="Connect 'Let the network run'."),),
    outputs=(
        Port("value", "Turn amount", "Scaled right-minus-left value", "number",
             tip="Feed straight into a servo angle, a motor speed, or a mouse movement."),
        Port("raw_hz", "Raw difference", "Unscaled difference in spikes per second", "number", tip="Use this when setting up thresholds."),
        Port("details", "Details", "Both sides, cell counts and window", "record", tip=""),
    ),
    params=(
        channel_param(role="output", default="motor.steering"),
        Param("scale", "Scale", "Multiply the difference by this",
              "Turns spikes per second into whatever unit the thing you are"
              " driving expects - degrees, duty percent, pixels.",
              kind="number", default=1.0, step=0.1),
    ),
    tips=("A persistent bias to one side turns into persistently turning one"
          " way. That is a property of the readout, not evidence of a decision.",),
))


def _run_gate(ctx, node, inputs):
    session = _require_session(ctx)
    key = str(node.params.get("channel", "motor.gate"))
    minimum = int(max(1, as_number(node.params.get("minimum", 1), 1)))
    passed = session.read_gate(key, minimum)
    details = session.read_channel(key)
    return Result(
        outputs={"open": bool(passed), "spikes": float(details["spikes"]), "details": details},
        record={"channel": key, "open": bool(passed), "spikes": details["spikes"], "minimum": minimum},
    )


register(BlockType(
    key="decode.gate", name="Commit gate", plain="Only act if these cells actually fired",
    summary="True when a named group produced at least a set number of spikes."
            " Use it so a workflow does nothing at all unless the network"
            " committed, instead of acting on noise.",
    category="decode", run=_run_gate,
    inputs=(Port("spikes", "Spike counts", "The result of a network step", "spikes", tip="Connect 'Let the network run'."),),
    outputs=(
        Port("open", "Gate open", "True when the group fired enough", "boolean", tip="Feed into If / then, or into an output block's condition."),
        Port("spikes", "Spikes", "How many spikes there were", "number", tip=""),
        Port("details", "Details", "Cells and rates", "record", tip=""),
    ),
    params=(
        channel_param(role="output", default="motor.gate"),
        Param("minimum", "Minimum spikes", "How many spikes count as committed",
              "One spike is the loosest possible gate. Raise it to demand a clearer signal.",
              kind="number", default=1, minimum=1, maximum=10000, step=1),
    ),
))


def _run_choice(ctx, node, inputs):
    session = _require_session(ctx)
    options = {}
    for i in (1, 2, 3, 4):
        label = str(node.params.get(f"label{i}", "")).strip()
        target = str(node.params.get(f"channel{i}", "")).strip()
        if label and target:
            options[label] = target
    if not options:
        raise ValueError("Give at least one label and cell group to choose between")
    result = session.classify(options, as_number(node.params.get("margin", 0.0)))
    return Result(
        outputs={
            "choice": result["choice"] or str(node.params.get("undecided", "none")),
            "decided": not result["undecided"],
            "lead": float(result["lead_hz"]),
            "details": result,
        },
        record=result,
    )


register(BlockType(
    key="decode.choice", name="Choice", plain="Which of several options is the network leaning toward?",
    summary="Compares the firing rates of up to four named cell groups and"
            " reports which is highest, as a label you can route on. The"
            " mapping from cells to labels is yours; the network has no idea"
            " what the words mean.",
    category="decode", run=_run_choice,
    inputs=(Port("spikes", "Spike counts", "The result of a network step", "spikes", tip="Connect 'Let the network run'."),),
    outputs=(
        Port("choice", "Choice", "The winning label", "text", tip="Feed into Route by label to act on it."),
        Port("decided", "Decided", "False when nothing led clearly enough", "boolean", tip="Branch on this to do nothing when the network is undecided."),
        Port("lead", "Lead", "How far ahead the winner was, in spikes per second", "number", tip="A small lead means a near tie."),
        Port("details", "Details", "Every option's score", "record", tip="Log it to see how close each decision was."),
    ),
    params=tuple(
        item for i in (1, 2, 3, 4) for item in (
            Param(f"label{i}", f"Option {i} label", f"What to call option {i}",
                  "The word this block reports when option " + str(i) + " wins. Leave blank to disable it.",
                  kind="text", default=["forward", "left", "right", "stop"][i - 1] if i <= 4 else "",
                  placeholder="forward"),
            channel_param(f"channel{i}", f"Option {i} cells", f"Cells that vote for option {i}",
                          "Add @left or @right after a group key to use one side only.",
                          role="output", default="motor.descending"),
        )
    ) + (
        Param("margin", "Required lead", "How far ahead the winner must be",
              "In spikes per second. Zero means any lead counts, which makes"
              " near-ties look decisive. Raise it to demand a clear winner.",
              kind="number", default=0.0, minimum=0, maximum=1000, step=0.5, unit="Hz"),
        Param("undecided", "When undecided", "The label to report if nothing leads",
              "Route this label somewhere harmless, such as doing nothing.",
              kind="text", default="none", placeholder="none"),
    ),
))


def _run_compass(ctx, node, inputs):
    session = _require_session(ctx)
    key = str(node.params.get("channel", "compass.epg"))
    bump = session.read_bump(key, str(node.params.get("side", "both")))
    return Result(
        outputs={"angle": float(bump["angle_degrees"]), "strength": float(bump["strength"]), "details": bump},
        record={"channel": key, **{k: v for k, v in bump.items() if k != "convention"}},
    )


register(BlockType(
    key="decode.compass", name="Heading", plain="Which way does the network think it is pointing?",
    summary="Finds the peak of the activity bump across a compass population"
            " and reports it as an angle. Cells are placed evenly around a"
            " circle by index, which is a stated convention, not their"
            " anatomical wedge identity.",
    category="decode", run=_run_compass,
    inputs=(Port("spikes", "Spike counts", "The result of a network step", "spikes", tip="Connect 'Let the network run'."),),
    outputs=(
        Port("angle", "Angle", "Bump position, 0 to 360 degrees", "number", tip="Compare with a target angle to steer."),
        Port("strength", "Sharpness", "How focused the bump is, 0 to 1", "number", tip="Near zero means there is no bump, just scattered activity."),
        Port("details", "Details", "Peak rate, cell count and the ordering convention", "record", tip=""),
    ),
    params=(
        channel_param(role="output", default="compass.epg"),
        Param("side", "Which side", "Use one hemisphere or both", "Both is usually right for a compass population.",
              kind="select", default="both", options=SIDES),
    ),
))


def _run_population(ctx, node, inputs):
    session = _require_session(ctx)
    key = str(node.params.get("channel", ""))
    rates = session.read_population(key)
    names = list(rates)
    values = [float(rates[k]) for k in names]
    return Result(
        outputs={"values": values, "names": names, "details": {"channel": key, "rates": rates}},
        record={"channel": key, "subtypes": len(names),
                "rates": {k: round(v, 3) for k, v in list(rates.items())[:24]}},
    )


register(BlockType(
    key="decode.population", name="Read every subtype", plain="One number per named cell type in a group",
    summary="Breaks a channel down by its annotated subtypes and reports each"
            " one's rate. Use it to see which glomerulus, which direction-"
            " selective cell or which output neuron is carrying the signal.",
    category="decode", run=_run_population,
    inputs=(Port("spikes", "Spike counts", "The result of a network step", "spikes", tip="Connect 'Let the network run'."),),
    outputs=(
        Port("values", "Rates", "One rate per subtype, in name order", "vector", tip="Drive another sense with it, or log it."),
        Port("names", "Subtype names", "The annotated type names, in the same order", "record", tip="Keeps the values interpretable."),
        Port("details", "Details", "The full name-to-rate mapping", "record", tip=""),
    ),
    params=(channel_param(),),
))


def _run_memory(ctx, node, inputs):
    session = _require_session(ctx)
    memory = session.brain.memory()
    return Result(
        outputs={
            "changed": float(memory["changed_edges"]),
            "mean": float(memory["mean_efficacy"]),
            "details": memory,
        },
        record=memory,
    )


register(BlockType(
    key="decode.memory", name="Memory state", plain="How much has the network actually changed?",
    summary="Reports how many plastic synapses differ from their starting"
            " efficacy, and by how much on average. The honest answer to"
            " 'is anything happening?'",
    category="decode", run=_run_memory,
    outputs=(
        Port("changed", "Synapses changed", "How many plastic edges differ from baseline", "number",
             tip="Zero means the plasticity rule has written nothing at all."),
        Port("mean", "Mean efficacy", "Average efficacy as a fraction of baseline", "number",
             tip="1.0 is unchanged. Below 1 is net depression, above is net potentiation."),
        Port("details", "Details", "Edge count, bounds and a checksum of the weights", "record",
             tip="The checksum proves two runs did or did not end in the same state."),
    ),
    tips=("Synapses changing is a mechanism check, not evidence that the"
          " network learned anything useful.",),
))


# ================================================================ logic blocks
_COMPARISONS = {
    "gt": (operator.gt, "is greater than"),
    "gte": (operator.ge, "is at least"),
    "lt": (operator.lt, "is less than"),
    "lte": (operator.le, "is at most"),
    "eq": (operator.eq, "equals"),
    "ne": (operator.ne, "does not equal"),
}


def _run_compare(ctx, node, inputs):
    left = as_number(inputs.get("value"))
    right = as_number(inputs.get("against", node.params.get("threshold", 0.0)))
    how = str(node.params.get("test", "gt"))
    check, phrase = _COMPARISONS.get(how, _COMPARISONS["gt"])
    result = bool(check(left, right))
    return Result(
        outputs={"result": result, "value": left},
        record={"value": round(left, 4), "test": phrase, "against": round(right, 4), "result": result},
    )


register(BlockType(
    key="logic.compare", name="Compare", plain="Is this bigger, smaller or equal to that?",
    summary="Turns a number into a yes/no answer so a branch, a gate or a goal"
            " check can use it.",
    category="logic", run=_run_compare,
    inputs=(
        Port("value", "Value", "The number to test", "number", required=True, tip="Any number output."),
        Port("against", "Compare with", "What to compare it against", "number",
             tip="Leave unconnected to use the fixed threshold on the block."),
    ),
    outputs=(
        Port("result", "Result", "The yes/no answer", "boolean", tip="Feed into If / then, Repeat until, or a goal check."),
        Port("value", "Value", "The number that was tested, passed through", "number", tip="Saves re-wiring the source twice."),
    ),
    params=(
        Param("test", "Test", "Which comparison to make", "Applied as: value TEST threshold.",
              kind="select", default="gt",
              options=(("gt", "Greater than", "value > threshold"),
                       ("gte", "At least", "value >= threshold"),
                       ("lt", "Less than", "value < threshold"),
                       ("lte", "At most", "value <= threshold"),
                       ("eq", "Equal to", "value == threshold"),
                       ("ne", "Not equal to", "value != threshold"))),
        Param("threshold", "Threshold", "The fixed value to compare against",
              "Ignored when something is connected to 'Compare with'.",
              kind="number", default=0.0, step=0.1),
    ),
))


_MATH = {
    "add": (operator.add, "+"), "subtract": (operator.sub, "-"),
    "multiply": (operator.mul, "x"), "divide": (lambda a, b: a / b if b else 0.0, "/"),
    "minimum": (min, "min"), "maximum": (max, "max"),
    "difference": (lambda a, b: abs(a - b), "|a-b|"),
}


def _run_math(ctx, node, inputs):
    a = as_number(inputs.get("a", node.params.get("a", 0.0)))
    b = as_number(inputs.get("b", node.params.get("b", 0.0)))
    how = str(node.params.get("operation", "add"))
    function, symbol = _MATH.get(how, _MATH["add"])
    value = float(function(a, b))
    return Result(outputs={"value": value}, record={"a": a, "b": b, "operation": symbol, "value": round(value, 6)})


register(BlockType(
    key="logic.math", name="Arithmetic", plain="Add, subtract, multiply or divide two numbers",
    summary="One operation on two values. Division by zero gives zero rather"
            " than failing the run.",
    category="logic", run=_run_math,
    inputs=(
        Port("a", "First", "The left-hand value", "number", tip="Leave unconnected to use the value on the block."),
        Port("b", "Second", "The right-hand value", "number", tip="Leave unconnected to use the value on the block."),
    ),
    outputs=(Port("value", "Result", "The answer", "number", tip=""),),
    params=(
        Param("operation", "Operation", "What to do with the two values", "Applied as: first OPERATION second.",
              kind="select", default="add",
              options=(("add", "Add", "first + second"), ("subtract", "Subtract", "first - second"),
                       ("multiply", "Multiply", "first x second"), ("divide", "Divide", "first / second, 0 if second is 0"),
                       ("minimum", "Smaller of", "whichever is lower"), ("maximum", "Larger of", "whichever is higher"),
                       ("difference", "Distance apart", "absolute difference"))),
        Param("a", "First value", "Used when nothing is connected", "A fixed number.", kind="number", default=0.0, step=0.1),
        Param("b", "Second value", "Used when nothing is connected", "A fixed number.", kind="number", default=0.0, step=0.1),
    ),
))


def _run_map(ctx, node, inputs):
    value = as_number(inputs.get("value"))
    in_low = as_number(node.params.get("in_low", 0.0))
    in_high = as_number(node.params.get("in_high", 1.0), 1.0)
    out_low = as_number(node.params.get("out_low", 0.0))
    out_high = as_number(node.params.get("out_high", 1.0), 1.0)
    if in_high == in_low:
        scaled = out_low
    else:
        fraction = (value - in_low) / (in_high - in_low)
        if bool(node.params.get("clamp", True)):
            fraction = min(1.0, max(0.0, fraction))
        scaled = out_low + fraction * (out_high - out_low)
    return Result(outputs={"value": float(scaled)},
                  record={"in": round(value, 4), "out": round(float(scaled), 4)})


register(BlockType(
    key="logic.map", name="Rescale", plain="Convert one range of numbers into another",
    summary="Turns spikes per second into degrees, duty percent, pixels or"
            " anything else the thing you are driving expects.",
    category="logic", run=_run_map,
    inputs=(Port("value", "Value", "The number to rescale", "number", required=True, tip=""),),
    outputs=(Port("value", "Rescaled", "The converted number", "number", tip=""),),
    params=(
        Param("in_low", "From: lowest", "Smallest value you expect to receive", "Anything at or below this maps to the output low.",
              kind="number", default=-20.0, step=0.1),
        Param("in_high", "From: highest", "Largest value you expect to receive", "Anything at or above this maps to the output high.",
              kind="number", default=20.0, step=0.1),
        Param("out_low", "To: lowest", "What the low end should become", "For a servo this is often 0 degrees.",
              kind="number", default=0.0, step=0.1),
        Param("out_high", "To: highest", "What the high end should become", "For a servo this is often 180 degrees.",
              kind="number", default=180.0, step=0.1),
        Param("clamp", "Keep inside the range", "Never go past the output limits",
              "Strongly recommended when driving hardware: an unclamped value"
              " can command a servo past its travel or a motor past its rating.",
              kind="bool", default=True),
    ),
))


def _run_smooth(ctx, node, inputs):
    value = as_number(inputs.get("value"))
    state = ctx.state(node.id)
    window = int(max(1, min(512, as_number(node.params.get("window", 5), 5))))
    history = state.setdefault("history", [])
    history.append(value)
    del history[:-window]
    smoothed = float(np.mean(history))
    return Result(
        outputs={"value": smoothed, "samples": float(len(history))},
        record={"raw": round(value, 4), "smoothed": round(smoothed, 4), "samples": len(history)},
    )


register(BlockType(
    key="logic.smooth", name="Smooth", plain="Average out jitter from a noisy reading",
    summary="Rolling mean over the last few values. Useful on distance sensors"
            " and on firing rates, which are noisy by nature.",
    category="logic", run=_run_smooth,
    inputs=(Port("value", "Value", "The noisy number", "number", required=True, tip=""),),
    outputs=(
        Port("value", "Smoothed", "The averaged number", "number", tip=""),
        Port("samples", "Samples held", "How many values are in the average yet", "number",
             tip="Early in a run there are fewer than the window size."),
    ),
    params=(
        Param("window", "Window", "How many recent values to average",
              "Bigger is smoother but slower to react. 1 disables smoothing.",
              kind="number", default=5, minimum=1, maximum=512, step=1),
    ),
))


def _run_deadband(ctx, node, inputs):
    value = as_number(inputs.get("value"))
    width = abs(as_number(node.params.get("width", 1.0), 1.0))
    inside = abs(value) < width
    out = 0.0 if inside else value
    return Result(outputs={"value": float(out), "active": not inside},
                  record={"in": round(value, 4), "out": round(float(out), 4), "inside_deadband": inside})


register(BlockType(
    key="logic.deadband", name="Ignore small values", plain="Treat anything near zero as zero",
    summary="Stops a workflow acting on noise around the resting point. A"
            " steering signal that wanders by a spike or two stays put.",
    category="logic", run=_run_deadband,
    inputs=(Port("value", "Value", "The number to filter", "number", required=True, tip=""),),
    outputs=(
        Port("value", "Filtered", "Zero inside the band, unchanged outside", "number", tip=""),
        Port("active", "Outside the band", "True when the value was big enough to pass", "boolean",
             tip="Use it as a cheap commit condition."),
    ),
    params=(
        Param("width", "Band", "Values smaller than this become zero", "Applies to both positive and negative values.",
              kind="number", default=1.0, minimum=0, step=0.1),
    ),
))


def _run_latch(ctx, node, inputs):
    state = ctx.state(node.id)
    value = inputs.get("value")
    hold = as_bool(inputs.get("hold", False))
    if not hold and value is not None:
        state["held"] = as_number(value)
    held = float(state.get("held", as_number(node.params.get("initial", 0.0))))
    return Result(outputs={"value": held}, record={"value": round(held, 4), "holding": hold})


register(BlockType(
    key="logic.latch", name="Hold a value", plain="Remember the last reading until told otherwise",
    summary="Passes the input through while Hold is off, and freezes the last"
            " value while Hold is on. Also the way to feed a value back into an"
            " earlier block without creating a loop the editor cannot resolve.",
    category="logic", run=_run_latch,
    inputs=(
        Port("value", "Value", "The number to remember", "number", tip=""),
        Port("hold", "Hold", "While true, ignore new values", "boolean", tip="Leave unconnected to always pass through."),
    ),
    outputs=(Port("value", "Held value", "The remembered number", "number", tip=""),),
    params=(
        Param("initial", "Starting value", "What to report before anything has been held",
              "Used on the first pass of a run.", kind="number", default=0.0, step=0.1),
    ),
))


def _run_counter(ctx, node, inputs):
    state = ctx.state(node.id)
    count = float(state.get("count", 0))
    if as_bool(inputs.get("reset", False)):
        count = 0.0
    if as_bool(inputs.get("when", True)):
        count += as_number(node.params.get("step", 1.0), 1.0)
    state["count"] = count
    return Result(outputs={"count": count}, record={"count": count})


register(BlockType(
    key="logic.counter", name="Count", plain="Keep a running tally",
    summary="Adds one each time it runs, or each time its condition is true."
            " Count trials, count successes, count how often a pin went high.",
    category="logic", run=_run_counter,
    inputs=(
        Port("when", "Count when", "Only count if this is true", "boolean", tip="Leave unconnected to count every pass."),
        Port("reset", "Reset", "Set the tally back to zero", "boolean", tip="Applied before counting."),
    ),
    outputs=(Port("count", "Count", "The running total", "number", tip="Compare it against a limit to end a run."),),
    params=(
        Param("step", "Step", "How much to add each time", "Use -1 to count down.",
              kind="number", default=1.0, step=1),
    ),
))


def _run_expression(ctx, node, inputs):
    names = {k: as_number(inputs.get(k, 0.0)) for k in ("a", "b", "c", "d")}
    names["iteration"] = float(ctx.iteration)
    try:
        value = safe_expression(str(node.params.get("expression", "a")), names)
        error = ""
    except ValueError as exc:
        value, error = 0.0, str(exc)
    return Result(
        outputs={"value": float(as_number(value)), "result": as_bool(value), "error": error},
        record={"inputs": {k: round(v, 4) for k, v in names.items()},
                "value": round(as_number(value), 6), "error": error},
    )


register(BlockType(
    key="logic.expression", name="Expression", plain="Write a small formula over up to four inputs",
    summary="Arithmetic, comparisons and a fixed list of maths functions over"
            " a, b, c, d and iteration. No variables, no attributes, no calls"
            " into the rest of the program.",
    category="logic", run=_run_expression,
    inputs=tuple(
        Port(k, k.upper(), f"Value available as '{k}' in the formula", "number",
             tip="Unconnected inputs are zero.")
        for k in ("a", "b", "c", "d")
    ),
    outputs=(
        Port("value", "Result", "The value the formula produced", "number", tip=""),
        Port("result", "As yes/no", "The same result read as true or false", "boolean",
             tip="Anything non-zero is true, so a comparison works directly."),
        Port("error", "Error", "Why the formula could not be read, if it could not", "text",
             tip="Empty when the formula is fine."),
    ),
    params=(
        Param("expression", "Formula", "The formula to evaluate",
              "Available: a b c d iteration pi e; + - * / // % **; < <= > >= == !=;"
              " and or not; abs min max round sqrt floor ceil sin cos tan atan2 exp"
              " log clamp sign. Example: clamp(a * 2 - b, 0, 180)",
              kind="code", default="a", placeholder="clamp(a * 2 - b, 0, 180)"),
    ),
))


def _run_logic_gate(ctx, node, inputs):
    a = as_bool(inputs.get("a", False))
    b = as_bool(inputs.get("b", False))
    how = str(node.params.get("operation", "and"))
    value = {"and": a and b, "or": a or b, "not": not a,
             "xor": bool(a) != bool(b), "nand": not (a and b), "nor": not (a or b)}.get(how, a and b)
    return Result(outputs={"result": bool(value)}, record={"a": a, "b": b, "operation": how, "result": bool(value)})


register(BlockType(
    key="logic.gate", name="Combine yes/no", plain="AND, OR, NOT two conditions together",
    summary="Builds a compound condition from simpler ones, so a branch can"
            " depend on several things at once.",
    category="logic", run=_run_logic_gate,
    inputs=(
        Port("a", "First", "The first condition", "boolean", tip=""),
        Port("b", "Second", "The second condition", "boolean", tip="Ignored by NOT."),
    ),
    outputs=(Port("result", "Result", "The combined answer", "boolean", tip=""),),
    params=(
        Param("operation", "Operation", "How to combine them", "NOT uses only the first input.",
              kind="select", default="and",
              options=(("and", "AND", "true only when both are true"),
                       ("or", "OR", "true when either is true"),
                       ("not", "NOT", "flips the first input"),
                       ("xor", "XOR", "true when exactly one is true"),
                       ("nand", "NAND", "false only when both are true"),
                       ("nor", "NOR", "true only when both are false"))),
    ),
))


def _run_select(ctx, node, inputs):
    condition = as_bool(inputs.get("when"))
    value = as_number(inputs.get("if_true", node.params.get("if_true", 1.0)), 1.0) if condition \
        else as_number(inputs.get("if_false", node.params.get("if_false", 0.0)))
    return Result(outputs={"value": float(value)}, record={"condition": condition, "value": round(float(value), 4)})


register(BlockType(
    key="logic.select", name="Pick one of two", plain="Choose between two numbers based on a condition",
    summary="Like If / then, but for a value rather than for the run order."
            " Keeps a workflow flat when only a number needs to change.",
    category="logic", run=_run_select,
    inputs=(
        Port("when", "Condition", "Which value to pick", "boolean", required=True, tip=""),
        Port("if_true", "When true", "Value to use when the condition holds", "number", tip="Leave unconnected to use the block value."),
        Port("if_false", "When false", "Value to use otherwise", "number", tip="Leave unconnected to use the block value."),
    ),
    outputs=(Port("value", "Value", "The chosen number", "number", tip=""),),
    params=(
        Param("if_true", "When true", "Fixed value for the true case", "Used when nothing is connected.", kind="number", default=1.0, step=0.1),
        Param("if_false", "When false", "Fixed value for the false case", "Used when nothing is connected.", kind="number", default=0.0, step=0.1),
    ),
))


def _run_match(ctx, node, inputs):
    value = as_text(inputs.get("value"), "").strip()
    against = as_text(inputs.get("against"), str(node.params.get("expected", ""))).strip()
    if not bool(node.params.get("case_sensitive", False)):
        value, against = value.lower(), against.lower()
    how = str(node.params.get("test", "equals"))
    result = {
        "equals": value == against,
        "contains": against in value,
        "starts": value.startswith(against),
        "not_equals": value != against,
    }.get(how, value == against)
    return Result(
        outputs={"result": bool(result), "opposite": not result, "value": value},
        record={"value": value, "against": against, "test": how, "result": bool(result)},
    )


register(BlockType(
    key="logic.match", name="Match text", plain="Does this word match what you expected?",
    summary="Compares two pieces of text and answers yes or no. This is how a"
            " Choice becomes a scored outcome: compare what the network chose"
            " against what the task wanted.",
    category="logic", run=_run_match,
    inputs=(
        Port("value", "Text", "The text to check", "text", required=True,
             tip="Connect the Choice block's output, or a serial line."),
        Port("against", "Expected", "What it should be", "text",
             tip="Leave unconnected to use the fixed value on the block."),
    ),
    outputs=(
        Port("result", "Matches", "True when the texts match", "boolean", tip="Wire into the Outcome block's 'counts as right'."),
        Port("opposite", "Does not match", "True when they differ", "boolean",
             tip="Wire into 'counts as wrong' - but combine it with a commit gate so an"
                 " undecided pass is not scored as a mistake."),
        Port("value", "Text", "The text that was checked, passed through", "text", tip=""),
    ),
    params=(
        Param("expected", "Expected", "What to compare against when nothing is connected",
              "The word that counts as the right answer.",
              kind="text", default="", placeholder="left"),
        Param("test", "Test", "How to compare", "Applied as: text TEST expected.",
              kind="select", default="equals",
              options=(("equals", "Is exactly", "The whole text matches"),
                       ("not_equals", "Is not", "The text is anything else"),
                       ("contains", "Contains", "The expected text appears somewhere"),
                       ("starts", "Starts with", "The text begins with the expected text"))),
        Param("case_sensitive", "Match capitals exactly", "Treat Left and left as different",
              "Off by default, which is almost always what you want.",
              kind="bool", default=False, advanced=True),
    ),
))


# =============================================================== output blocks
def _gated(ctx, node, inputs):
    """Every output block honours an optional 'Only when' condition."""
    return as_bool(inputs.get("when", True))


GATE_PORT = Port("when", "Only when", "Act only if this is true", "boolean",
                 tip="Leave unconnected to act every time. Connect a commit gate so"
                     " nothing moves unless the network actually committed.")


def _run_gpio_write(ctx, node, inputs):
    if not _gated(ctx, node, inputs):
        return Result(outputs={"acted": False, "details": {}}, record={"acted": False})
    controller = _gpio(ctx, node)
    pin = gpio_io.check_pin(node.params.get("pin", 17))
    value = inputs.get("value")
    high = as_bool(value) if value is not None else bool(node.params.get("high", True))
    if bool(node.params.get("invert", False)):
        high = not high
    result = controller.write(pin, high)
    return Result(outputs={"acted": True, "details": result}, record=result)


register(BlockType(
    key="output.gpio", name="Set a pin", plain="Drive a Raspberry Pi pin high or low",
    summary="Switches one GPIO pin. This is how a decision becomes a relay"
            " clicking, an LED lighting, or a motor driver enabling.",
    category="output", run=_run_gpio_write, needs=("gpio",),
    inputs=(GATE_PORT, Port("value", "High", "True for high, false for low", "boolean",
                            tip="Leave unconnected to use the fixed setting on the block.")),
    outputs=(
        Port("acted", "Acted", "False when the condition blocked it", "boolean", tip=""),
        Port("details", "Details", "Pin, level and whether it was real hardware", "record", tip=""),
    ),
    params=(
        Param("pin", "Pin", "Which GPIO pin to drive", "BCM numbering; the header position is shown beside each.",
              kind="pin", default=17, options=PIN_OPTIONS),
        Param("high", "Level", "What to set when nothing is connected", "High is 3.3 V; low is ground.",
              kind="bool", default=True),
        Param("invert", "Invert", "Send the opposite level", "For hardware that is active-low, like most relay boards.",
              kind="bool", default=False),
    ),
    caution="Never drive a motor directly from a GPIO pin. Use a driver board;"
            " a pin supplies a few milliamps.",
))


def _run_pwm(ctx, node, inputs):
    if not _gated(ctx, node, inputs):
        return Result(outputs={"acted": False, "details": {}}, record={"acted": False})
    controller = _gpio(ctx, node)
    duty = as_number(inputs.get("duty", node.params.get("duty", 50.0)), 50.0)
    duty = max(0.0, min(100.0, duty))
    result = controller.pwm(
        node.params.get("pin", 18), duty, as_number(node.params.get("frequency", 1000.0), 1000.0)
    )
    return Result(outputs={"acted": True, "duty": duty, "details": result}, record=result)


register(BlockType(
    key="output.pwm", name="Motor speed / brightness", plain="Set how hard a pin drives, from 0 to 100 percent",
    summary="Pulse-width modulation on one pin. Motor speed through a driver"
            " board, LED brightness, or anything that takes a proportional"
            " signal.",
    category="output", run=_run_pwm, needs=("gpio",),
    inputs=(GATE_PORT, Port("duty", "Level", "Percent, 0 to 100", "number",
                            tip="Values outside the range are clamped, not rejected.")),
    outputs=(
        Port("acted", "Acted", "False when the condition blocked it", "boolean", tip=""),
        Port("duty", "Level set", "The percentage actually applied", "number", tip=""),
        Port("details", "Details", "Pin, duty and frequency", "record", tip=""),
    ),
    params=(
        Param("pin", "Pin", "Which pin to drive", "BCM 12, 13, 18 and 19 can use hardware PWM, which is smoother.",
              kind="pin", default=18, options=PIN_OPTIONS),
        Param("duty", "Level", "Percentage when nothing is connected", "0 is off, 100 is fully on.",
              kind="number", default=50.0, minimum=0, maximum=100, step=1, unit="%"),
        Param("frequency", "Frequency", "How fast the pulses repeat",
              "About 1 kHz suits LEDs and small motors. Servos need 50 Hz - use the Servo block instead.",
              kind="number", default=1000.0, minimum=1, maximum=20000, step=10, unit="Hz"),
    ),
    caution="Check the driver board's maximum PWM frequency before raising it.",
))


def _run_servo(ctx, node, inputs):
    if not _gated(ctx, node, inputs):
        return Result(outputs={"acted": False, "details": {}}, record={"acted": False})
    controller = _gpio(ctx, node)
    angle = as_number(inputs.get("angle", node.params.get("angle", 90.0)), 90.0)
    low = as_number(node.params.get("minimum", 0.0))
    high = as_number(node.params.get("maximum", 180.0), 180.0)
    angle = max(min(low, high), min(max(low, high), angle))
    result = controller.servo(node.params.get("pin", 13), angle)
    return Result(outputs={"acted": True, "angle": angle, "details": result},
                  record={**result, "angle": angle})


register(BlockType(
    key="output.servo", name="Servo angle", plain="Point a hobby servo at an angle",
    summary="Sends the standard 50 Hz pulse a hobby servo expects, from 1.0 ms"
            " at 0 degrees to 2.0 ms at 180.",
    category="output", run=_run_servo, needs=("gpio",),
    inputs=(GATE_PORT, Port("angle", "Angle", "Degrees, 0 to 180", "number",
                            tip="Feed a rescaled steering value straight in.")),
    outputs=(
        Port("acted", "Acted", "False when the condition blocked it", "boolean", tip=""),
        Port("angle", "Angle set", "The angle actually commanded", "number", tip="Clamped to the travel limits below."),
        Port("details", "Details", "Pin, pulse width and frequency", "record", tip=""),
    ),
    params=(
        Param("pin", "Pin", "Which pin the servo signal wire is on", "Servo power must come from its own supply, not the Pi's 3.3 V rail.",
              kind="pin", default=13, options=PIN_OPTIONS),
        Param("angle", "Angle", "Angle when nothing is connected", "In degrees.",
              kind="number", default=90.0, minimum=0, maximum=180, step=1, unit="deg"),
        Param("minimum", "Travel limit: low", "Never command below this angle",
              "Protects a mechanism that cannot physically reach 0 degrees.",
              kind="number", default=0.0, minimum=0, maximum=180, step=1, unit="deg"),
        Param("maximum", "Travel limit: high", "Never command above this angle",
              "Protects a mechanism that cannot physically reach 180 degrees.",
              kind="number", default=180.0, minimum=0, maximum=180, step=1, unit="deg"),
    ),
    caution="Set the travel limits before connecting a servo to a real mechanism."
            " A servo will happily stall itself against a hard stop.",
))


def _run_serial_write(ctx, node, inputs):
    if not _gated(ctx, node, inputs):
        return Result(outputs={"acted": False, "details": {}}, record={"acted": False})
    link = _serial_link(ctx, node)
    template = str(node.params.get("message", "{value}"))
    value = inputs.get("value")
    text = template.replace("{value}", as_text(value, "")) if value is not None else template
    text = text.replace("{iteration}", str(ctx.iteration))
    result = link.write(text, raw=bool(node.params.get("raw", False)))
    return Result(outputs={"acted": True, "sent": text, "details": result}, record={**result, "sent": text})


register(BlockType(
    key="output.serial", name="Send serial", plain="Send a command to an Arduino or motor driver",
    summary="Writes one line to the serial port. Put {value} in the message to"
            " insert the connected value.",
    category="output", run=_run_serial_write, needs=("serial",),
    inputs=(GATE_PORT, Port("value", "Value", "Substituted into the message where {value} appears", "any",
                            tip="Any number, text or choice.")),
    outputs=(
        Port("acted", "Acted", "False when the condition blocked it", "boolean", tip=""),
        Port("sent", "Sent", "The exact text that went out", "text", tip="Log it when debugging a protocol."),
        Port("details", "Details", "Port, byte count and whether it was simulated", "record", tip=""),
    ),
    params=(
        Param("port", "Port", "Which serial port to write to", "Must match the device; the Dashboard lists what it can see.",
              kind="select", default="", options=(("", "Choose a port", ""),)),
        Param("baud", "Speed", "Bits per second, must match the device", "A mismatch produces garbage, not an error.",
              kind="select", default=115200, options=tuple((b, f"{b} baud", "") for b in links.BAUD_RATES)),
        Param("message", "Message", "What to send", "Use {value} for the connected value and {iteration} for the pass number.",
              kind="text", default="{value}", placeholder="MOVE {value}"),
        Param("raw", "No newline", "Send without a line ending", "Most Arduino sketches expect a newline; leave this off.",
              kind="bool", default=False, advanced=True),
        Param("timeout", "Timeout", "How long to wait for the write to complete", "Rarely matters for short lines.",
              kind="number", default=1.0, minimum=0.01, maximum=60, step=0.1, unit="s", advanced=True),
    ),
))


def _pointer(ctx):
    return ctx.device(("pointer",), lambda: links.Pointer(simulate=ctx.simulate_devices))


def _run_mouse(ctx, node, inputs):
    if not _gated(ctx, node, inputs):
        return Result(outputs={"acted": False, "details": {}}, record={"acted": False})
    pointer = _pointer(ctx)
    action = str(node.params.get("action", "move"))
    if action == "click":
        result = pointer.click(str(node.params.get("button", "left")),
                               int(as_number(node.params.get("clicks", 1), 1)))
    elif action == "scroll":
        result = pointer.scroll(int(as_number(inputs.get("x", node.params.get("x", 0)))))
    else:
        result = pointer.move_to(
            as_number(inputs.get("x", node.params.get("x", 0.0))),
            as_number(inputs.get("y", node.params.get("y", 0.0))),
            relative=(action == "move_by"),
        )
    return Result(outputs={"acted": True, "details": result}, record=result)


register(BlockType(
    key="output.mouse", name="Mouse", plain="Move the pointer, click or scroll",
    summary="Drives the real cursor on this computer. Paired with the screen"
            " capture block, this is the loop that lets the network use a"
            " window: look, decide, move, click, look again.",
    category="output", run=_run_mouse, needs=("pointer",),
    inputs=(
        GATE_PORT,
        Port("x", "X", "Horizontal position or amount", "number", tip="Screen pixels for a move; steps for a scroll."),
        Port("y", "Y", "Vertical position", "number", tip="Screen pixels, measured from the top."),
    ),
    outputs=(
        Port("acted", "Acted", "False when the condition blocked it", "boolean", tip=""),
        Port("details", "Details", "What the pointer was told to do", "record", tip="Shows whether it was simulated."),
    ),
    params=(
        Param("action", "Action", "What the mouse should do", "Move to a position, move by an offset, click, or scroll.",
              kind="select", default="move",
              options=(("move", "Move to", "Absolute screen position"),
                       ("move_by", "Move by", "Relative to where it is now"),
                       ("click", "Click", "Press and release"),
                       ("scroll", "Scroll", "Wheel steps, from the X input"))),
        Param("x", "X", "Used when nothing is connected", "Screen pixels from the left.",
              kind="number", default=0.0, step=1, unit="px"),
        Param("y", "Y", "Used when nothing is connected", "Screen pixels from the top.",
              kind="number", default=0.0, step=1, unit="px"),
        Param("button", "Button", "Which button to click", "Only used by the Click action.",
              kind="select", default="left",
              options=(("left", "Left", ""), ("right", "Right", ""), ("middle", "Middle", ""))),
        Param("clicks", "Clicks", "How many times to click", "2 makes a double click.",
              kind="number", default=1, minimum=1, maximum=5, step=1),
    ),
    caution="This moves your real cursor. Test with device simulation on before"
            " letting a run drive the machine you are working on.",
))


def _run_keyboard(ctx, node, inputs):
    if not _gated(ctx, node, inputs):
        return Result(outputs={"acted": False, "details": {}}, record={"acted": False})
    pointer = _pointer(ctx)
    mode = str(node.params.get("mode", "key"))
    if mode == "type":
        text = as_text(inputs.get("text"), str(node.params.get("text", "")))
        result = pointer.key("", text=text)
    else:
        result = pointer.key(str(node.params.get("key", "space")))
    return Result(outputs={"acted": True, "details": result}, record=result)


register(BlockType(
    key="output.keyboard", name="Keyboard", plain="Press a key or type some text",
    summary="Sends keystrokes to whatever window has focus on this computer.",
    category="output", run=_run_keyboard, needs=("pointer",),
    inputs=(GATE_PORT, Port("text", "Text", "What to type", "text", tip="Only used in Type mode.")),
    outputs=(
        Port("acted", "Acted", "False when the condition blocked it", "boolean", tip=""),
        Port("details", "Details", "What was sent", "record", tip=""),
    ),
    params=(
        Param("mode", "Mode", "Press one key, or type a string", "Key names follow the usual convention: space, enter, up, left, f1.",
              kind="select", default="key",
              options=(("key", "Press a key", "A single named key"), ("type", "Type text", "A whole string"))),
        Param("key", "Key", "Which key to press", "For example: space, enter, up, down, left, right, a, f5.",
              kind="text", default="space", placeholder="space"),
        Param("text", "Text", "What to type when nothing is connected", "Typed as if at the keyboard.",
              kind="text", default="", placeholder="hello"),
    ),
    caution="Keystrokes go to whatever window has focus, which may not be the"
            " one you meant.",
))


def _run_http_post(ctx, node, inputs):
    if not _gated(ctx, node, inputs):
        return Result(outputs={"acted": False, "ok": False, "details": {}}, record={"acted": False})
    payload = {
        "value": inputs.get("value"),
        "iteration": ctx.iteration,
        "run": ctx.run_id,
        "note": str(node.params.get("note", "")),
    }
    if isinstance(payload["value"], np.ndarray):
        payload["value"] = payload["value"].tolist()
    result = links.http_request(
        str(node.params.get("url", "")), str(node.params.get("method", "POST")), payload, None,
        as_number(node.params.get("timeout", 5.0), 5.0),
    )
    return Result(outputs={"acted": True, "ok": bool(result["ok"]), "details": result}, record=result)


register(BlockType(
    key="output.http", name="Webhook", plain="Send the result to a web address",
    summary="POSTs a small JSON body with the connected value, the iteration"
            " number and the run id. The general way to reach anything with a"
            " REST endpoint.",
    category="output", run=_run_http_post,
    inputs=(GATE_PORT, Port("value", "Value", "What to send", "any", tip="Included in the JSON body as 'value'.")),
    outputs=(
        Port("acted", "Acted", "False when the condition blocked it", "boolean", tip=""),
        Port("ok", "Accepted", "Whether the endpoint answered successfully", "boolean", tip="Branch on this to retry or log a failure."),
        Port("details", "Details", "Status code, timing and reply", "record", tip=""),
    ),
    params=(
        Param("url", "Address", "Where to send it", "Only http and https.", kind="text", default="",
              placeholder="http://192.168.1.50/command"),
        Param("method", "Method", "Which HTTP method to use", "POST is usual for sending a value.",
              kind="select", default="POST", options=(("POST", "POST", ""), ("PUT", "PUT", ""), ("GET", "GET", "No body is sent"))),
        Param("note", "Note", "A label included in the body", "Helps the receiving end tell workflows apart.",
              kind="text", default="", placeholder="robot-1"),
        Param("timeout", "Timeout", "How long to wait for a reply", "The run continues either way.",
              kind="number", default=5.0, minimum=0.1, maximum=60, step=0.5, unit="s"),
    ),
    caution="This sends data off this machine. Check the address before running.",
))


def _run_udp(ctx, node, inputs):
    if not _gated(ctx, node, inputs):
        return Result(outputs={"acted": False, "details": {}}, record={"acted": False})
    template = str(node.params.get("message", "{value}"))
    text = template.replace("{value}", as_text(inputs.get("value"), "")).replace("{iteration}", str(ctx.iteration))
    result = links.udp_send(str(node.params.get("host", "127.0.0.1")),
                            int(as_number(node.params.get("port", 9000), 9000)), text)
    return Result(outputs={"acted": True, "details": result}, record={**result, "message": text})


register(BlockType(
    key="output.udp", name="UDP message", plain="Fire a datagram at a robot or controller",
    summary="Sends a short message with no handshake and no reply. The lowest"
            " latency way to reach a device on the same network.",
    category="output", run=_run_udp,
    inputs=(GATE_PORT, Port("value", "Value", "Substituted into the message", "any", tip="Use {value} in the message.")),
    outputs=(
        Port("acted", "Acted", "False when the condition blocked it", "boolean", tip=""),
        Port("details", "Details", "Host, port and byte count", "record", tip="Delivery is not confirmed; UDP has no acknowledgement."),
    ),
    params=(
        Param("host", "Address", "Where to send it", "An IP address or host name on your network.",
              kind="text", default="127.0.0.1", placeholder="192.168.1.60"),
        Param("port", "Port", "Which UDP port", "Must match what the receiving device listens on.",
              kind="number", default=9000, minimum=1, maximum=65535, step=1),
        Param("message", "Message", "What to send", "Use {value} and {iteration} as placeholders.",
              kind="text", default="{value}", placeholder="SPEED {value}"),
    ),
))


def _run_mqtt(ctx, node, inputs):
    if not _gated(ctx, node, inputs):
        return Result(outputs={"acted": False, "details": {}}, record={"acted": False})
    host = str(node.params.get("host", "localhost"))
    port = int(as_number(node.params.get("port", 1883), 1883))
    client = ctx.device(("mqtt", host, port),
                        lambda: links.MqttLink(host, port, simulate=ctx.simulate_devices))
    value = inputs.get("value")
    if isinstance(value, np.ndarray):
        value = value.tolist()
    result = client.publish(str(node.params.get("topic", "flylab/value")), value)
    return Result(outputs={"acted": True, "details": result}, record=result)


register(BlockType(
    key="output.mqtt", name="MQTT publish", plain="Publish to a message broker",
    summary="Sends the value to an MQTT topic, which is how most home and"
            " robot automation systems expect to be told things.",
    category="output", run=_run_mqtt, needs=("mqtt",),
    inputs=(GATE_PORT, Port("value", "Value", "What to publish", "any", tip="Numbers go as-is; structures go as JSON.")),
    outputs=(
        Port("acted", "Acted", "False when the condition blocked it", "boolean", tip=""),
        Port("details", "Details", "Topic, payload and result", "record", tip=""),
    ),
    params=(
        Param("host", "Broker", "Where the broker is", "Host name or IP address.", kind="text", default="localhost"),
        Param("port", "Port", "Broker port", "1883 is the usual unencrypted port.",
              kind="number", default=1883, minimum=1, maximum=65535, step=1),
        Param("topic", "Topic", "Which topic to publish to", "Slash-separated, for example home/robot/speed.",
              kind="text", default="flylab/value", placeholder="home/robot/speed"),
    ),
))


def _run_log(ctx, node, inputs):
    record = {
        "label": str(node.params.get("label", "")) or node.label or node.id,
        "value": inputs.get("value"),
        "iteration": ctx.iteration,
    }
    if isinstance(record["value"], np.ndarray):
        record["value"] = record["value"].tolist()
    ctx.log(record)
    return Result(outputs={"value": inputs.get("value")}, record=record)


register(BlockType(
    key="output.log", name="Record", plain="Write a value into the run log",
    summary="Adds a labelled entry to the run's log, which is what you read"
            " afterwards to work out what happened.",
    category="output", run=_run_log,
    inputs=(Port("value", "Value", "What to record", "any", tip="Anything: a number, a choice, a details record."),),
    outputs=(Port("value", "Value", "The same value, passed through", "any", tip="Chain it so logging does not break a connection."),),
    params=(
        Param("label", "Label", "What to call this entry", "Shows in the run log and in exported files.",
              kind="text", default="", placeholder="steering"),
    ),
))


def _run_display(ctx, node, inputs):
    value = inputs.get("value")
    if isinstance(value, np.ndarray):
        value = value.tolist()
    panel = {
        "node": node.id,
        "label": str(node.params.get("label", "")) or node.label or "Readout",
        "style": str(node.params.get("style", "number")),
        "value": value if not isinstance(value, (dict, list)) else value,
        "number": as_number(value),
        "minimum": as_number(node.params.get("minimum", 0.0)),
        "maximum": as_number(node.params.get("maximum", 100.0), 100.0),
        "unit": str(node.params.get("unit", "")),
    }
    ctx.display(panel)
    return Result(outputs={"value": inputs.get("value")}, record={"displayed": panel["label"]})


register(BlockType(
    key="output.display", name="Show on the dashboard", plain="Put a live readout on screen",
    summary="Adds a gauge, bar or number to the run view so you can watch a"
            " value change while the workflow is going.",
    category="output", run=_run_display,
    inputs=(Port("value", "Value", "What to show", "any", tip="Numbers get a gauge; text and records are shown as-is."),),
    outputs=(Port("value", "Value", "The same value, passed through", "any", tip=""),),
    params=(
        Param("label", "Label", "What to call this readout", "Shown above the value.",
              kind="text", default="", placeholder="Steering"),
        Param("style", "Style", "How to draw it", "Pick whichever makes the value easiest to read at a glance.",
              kind="select", default="number",
              options=(("number", "Number", "Plain value"), ("gauge", "Gauge", "Dial between the limits"),
                       ("bar", "Bar", "Horizontal bar"), ("meter", "Centre meter", "Signed value either side of zero"),
                       ("text", "Text", "Words rather than numbers"))),
        Param("minimum", "Low limit", "Value at the bottom of the scale", "Only used by gauge, bar and meter.",
              kind="number", default=0.0, step=1),
        Param("maximum", "High limit", "Value at the top of the scale", "Only used by gauge, bar and meter.",
              kind="number", default=100.0, step=1),
        Param("unit", "Unit", "What the number is measured in", "Shown after the value, for example Hz, cm, deg.",
              kind="text", default="", placeholder="Hz"),
    ),
))


def _run_file_out(ctx, node, inputs):
    if not _gated(ctx, node, inputs):
        return Result(outputs={"acted": False, "details": {}}, record={"acted": False})
    value = inputs.get("value")
    if isinstance(value, np.ndarray):
        value = value.tolist()
    row = {"time": time.time(), "iteration": ctx.iteration, "run": ctx.run_id,
           "label": str(node.params.get("label", "")) or node.id, "value": value}
    result = links.append_record(ctx.resolve_path(node.params.get("path", "run-output.jsonl")),
                                row, str(node.params.get("format", "jsonl")))
    return Result(outputs={"acted": True, "details": result}, record=result)


register(BlockType(
    key="output.file", name="Append to a file", plain="Write each result to a log file on disk",
    summary="Adds one row per pass to a JSONL or CSV file, for analysis"
            " somewhere else afterwards.",
    category="output", run=_run_file_out,
    inputs=(GATE_PORT, Port("value", "Value", "What to write", "any", tip="Stored in the 'value' column.")),
    outputs=(
        Port("acted", "Acted", "False when the condition blocked it", "boolean", tip=""),
        Port("details", "Details", "Path, format and file size", "record", tip=""),
    ),
    params=(
        Param("path", "File", "Where to write", "Relative paths land in the run's own folder.",
              kind="path", default="run-output.jsonl", placeholder="results.csv"),
        Param("format", "Format", "How to write each row", "JSONL keeps structure; CSV is flat and opens in a spreadsheet.",
              kind="select", default="jsonl",
              options=(("jsonl", "JSONL", "One JSON object per line"), ("csv", "CSV", "Comma separated"))),
        Param("label", "Label", "A name stored with each row", "Lets one file hold several streams.",
              kind="text", default="", placeholder="steering"),
    ),
))


def _run_command(ctx, node, inputs):
    if not _gated(ctx, node, inputs):
        return Result(outputs={"acted": False, "ok": False, "details": {}}, record={"acted": False})
    parts = [p for p in str(node.params.get("command", "")).split() if p]
    value = inputs.get("value")
    parts = [p.replace("{value}", as_text(value, "")) for p in parts]
    result = links.run_command(parts, as_number(node.params.get("timeout", 10.0), 10.0),
                               allow=bool(node.params.get("allow", False)))
    return Result(outputs={"acted": True, "ok": bool(result["ok"]), "details": result}, record=result)


register(BlockType(
    key="output.command", name="Run a program", plain="Start a local program with the result",
    summary="Runs a command on this machine. Off by default: it has to be"
            " enabled on the block before it will do anything.",
    category="output", run=_run_command,
    inputs=(GATE_PORT, Port("value", "Value", "Substituted where {value} appears", "any", tip="")),
    outputs=(
        Port("acted", "Acted", "False when the condition blocked it", "boolean", tip=""),
        Port("ok", "Succeeded", "True when the program exited cleanly", "boolean", tip=""),
        Port("details", "Details", "Exit code and output", "record", tip="Output is truncated to a few kilobytes."),
    ),
    params=(
        Param("allow", "Allow shell commands", "Required before this block will run anything",
              "Off by default. A workflow that can run programs can do anything"
              " your user account can do; turn this on deliberately.",
              kind="bool", default=False),
        Param("command", "Command", "The program and its arguments",
              "Split on spaces and run directly - not through a shell, so pipes"
              " and redirection do not work. Use {value} for the connected value.",
              kind="text", default="", placeholder="/home/pi/move.sh {value}"),
        Param("timeout", "Timeout", "How long to let it run", "The program is killed after this.",
              kind="number", default=10.0, minimum=0.1, maximum=600, step=1, unit="s"),
    ),
    caution="This runs programs on the machine hosting the platform, with your"
            " permissions. Leave it off unless you specifically need it.",
))


# ====================================================== goal and training blocks
def _run_goal_define(ctx, node, inputs):
    ctx.goal.define(
        str(node.params.get("goal", "")),
        str(node.params.get("success", "")),
        str(node.params.get("failure", "")),
    )
    return Result(outputs={"goal": ctx.goal.goal}, record=ctx.goal.summary())


register(BlockType(
    key="goal.define", name="Goal", plain="State what this workflow is trying to achieve",
    summary="Records the task in words, along with what counts as right and"
            " what counts as wrong. Put it near Start; it is the first thing"
            " anyone reading the run log will want.",
    category="goal", run=_run_goal_define,
    outputs=(Port("goal", "Goal", "The goal text, passed on", "text", tip="Log it or show it on the dashboard."),),
    params=(
        Param("goal", "Goal", "What the workflow is for, in one sentence",
              "Written into the run log and shown on the run view. Be concrete:"
              " 'keep the robot within 30 cm of the wall' beats 'navigate'.",
              kind="code", default="", placeholder="Turn toward the brighter side of the image"),
        Param("success", "Counts as right", "What a correct outcome looks like",
              "Describe it in words here; wire the actual test into the Outcome block.",
              kind="code", default="", placeholder="Steering has the same sign as the brighter side"),
        Param("failure", "Counts as wrong", "What an incorrect outcome looks like",
              "The complement of success is often not the same as failure - a"
              " pass where nothing happened is usually neither.",
              kind="code", default="", placeholder="Steering points the other way"),
    ),
))


def _run_outcome(ctx, node, inputs):
    correct = as_bool(inputs.get("correct", False))
    incorrect = as_bool(inputs.get("incorrect", False))
    if correct and incorrect:
        outcome = str(node.params.get("conflict", "neutral"))
    elif correct:
        outcome = "correct"
    elif incorrect:
        outcome = "incorrect"
    else:
        outcome = "neutral"
    entry = ctx.goal.record(outcome, ctx.iteration, {"node": node.id})
    summary = ctx.goal.summary()
    reached = False
    target = as_number(node.params.get("stop_after", 0))
    if target and summary["streak"] >= target:
        reached = ctx.goal.mark_reached()
    return Result(
        flow={"correct": "correct", "incorrect": "incorrect", "neutral": "neutral"}[outcome],
        outputs={
            "outcome": outcome,
            "was_correct": outcome == "correct",
            "was_incorrect": outcome == "incorrect",
            "accuracy": float(summary["accuracy"]),
            "streak": float(summary["streak"]),
            "goal_reached": reached or summary["goal_reached"],
            "details": summary,
        },
        record={**entry, "accuracy": summary["accuracy"], "streak": summary["streak"]},
    )


register(BlockType(
    key="goal.outcome", name="Outcome", plain="Decide whether this pass went right or wrong",
    summary="Takes two conditions - one for right, one for wrong - scores the"
            " pass, and sends the run down the matching path. Anything that is"
            " neither counts as neutral and is not scored.",
    category="goal", run=_run_outcome,
    flow_out=("correct", "incorrect", "neutral"),
    flow_labels={
        "correct": {"label": "Right", "plain": "Runs when the pass was correct"},
        "incorrect": {"label": "Wrong", "plain": "Runs when the pass was incorrect"},
        "neutral": {"label": "Neither", "plain": "Runs when neither condition held"},
    },
    inputs=(
        Port("correct", "Counts as right", "The condition for a correct outcome", "boolean", required=True,
             tip="Usually a comparison between what the network did and what the task wanted."),
        Port("incorrect", "Counts as wrong", "The condition for an incorrect outcome", "boolean",
             tip="Leave unconnected if anything that is not right should count as neutral rather than wrong."),
    ),
    outputs=(
        Port("outcome", "Outcome", "right, wrong or neither, as text", "text", tip="Route it, log it, or show it."),
        Port("was_correct", "Was right", "True on a correct pass", "boolean", tip="Wire into a teaching pulse's condition."),
        Port("was_incorrect", "Was wrong", "True on an incorrect pass", "boolean", tip="Wire into the punishment pulse's condition."),
        Port("accuracy", "Accuracy", "Correct divided by scored attempts so far", "number", tip="Neutral passes are not counted."),
        Port("streak", "Streak", "How many right in a row", "number", tip="Use it to end a run once the task is reliably done."),
        Port("goal_reached", "Goal reached", "True once the streak target is met", "boolean", tip="Wire into Stop the run."),
        Port("details", "Details", "The full scoreboard", "record", tip=""),
    ),
    params=(
        Param("stop_after", "Streak to count as done", "How many right in a row means the task is achieved",
              "0 means never declare the goal reached; the run ends on its own limits instead.",
              kind="number", default=0, minimum=0, maximum=100000, step=1),
        Param("conflict", "If both are true", "What to record when both conditions hold at once",
              "Usually a sign the two rules overlap. Neutral is the safe choice.",
              kind="select", default="neutral",
              options=(("neutral", "Neither", "Do not score the pass"),
                       ("correct", "Right", "Prefer the success rule"),
                       ("incorrect", "Wrong", "Prefer the failure rule"))),
    ),
    tips=("A rising accuracy on its own is not evidence of learning. Run the"
          " same workflow with synapses frozen and compare.",),
))


def _run_score(ctx, node, inputs):
    summary = ctx.goal.summary()
    return Result(
        outputs={
            "accuracy": float(summary["accuracy"]),
            "recent": float(summary["recent_accuracy"]),
            "attempts": float(summary["attempts"]),
            "correct": float(summary["correct"]),
            "streak": float(summary["streak"]),
            "details": summary,
        },
        record=summary,
    )


register(BlockType(
    key="goal.score", name="Scoreboard", plain="How is it doing so far?",
    summary="Reads the running tally without changing it. Put it wherever you"
            " want to act on progress: show it, log it, or stop on it.",
    category="goal", run=_run_score,
    outputs=(
        Port("accuracy", "Accuracy", "Correct divided by all scored attempts", "number", tip="Over the whole run."),
        Port("recent", "Recent accuracy", "Accuracy over the last twenty scored attempts", "number",
             tip="Moves faster than the overall figure, which is what you want while tuning."),
        Port("attempts", "Attempts", "How many passes were scored", "number", tip="Neutral passes are excluded."),
        Port("correct", "Correct", "How many went right", "number", tip=""),
        Port("streak", "Streak", "How many right in a row", "number", tip=""),
        Port("details", "Details", "The full scoreboard, including the goal text", "record", tip=""),
    ),
))


def _run_trial(ctx, node, inputs):
    session = ctx.session
    trial = ctx.goal.begin_trial()
    reset = bool(node.params.get("reset", True))
    if reset and session is not None:
        session.reset(keep_memory=bool(node.params.get("keep_memory", True)))
    return Result(
        outputs={"trial": float(trial)},
        record={"trial": trial, "reset_activity": reset,
                "kept_memory": bool(node.params.get("keep_memory", True))},
    )


register(BlockType(
    key="train.trial", name="Start a trial", plain="Mark the beginning of one attempt",
    summary="Numbers the attempts and, by default, clears leftover activity so"
            " each one starts from the same place. Learned changes survive"
            " unless you say otherwise.",
    category="goal", run=_run_trial,
    outputs=(Port("trial", "Trial", "Which attempt this is, counting from 1", "number",
                  tip="Use it to change the stimulus per trial, or to end after N trials."),),
    params=(
        Param("reset", "Clear activity first", "Return voltages and traces to their starting values",
              "Without this, activity from the previous attempt carries over and"
              " the attempts are not independent.",
              kind="bool", default=True),
        Param("keep_memory", "Keep what was learned", "Preserve synaptic changes across the reset",
              "On for training across trials. Off to start each trial from the"
              " untouched baseline network.",
              kind="bool", default=True),
    ),
))


def _run_teach_outcome(ctx, node, inputs):
    session = _require_session(ctx)
    outcome = as_text(inputs.get("outcome"), "neutral").strip().lower()
    delivered = None
    if outcome == "correct" and bool(node.params.get("teach_correct", True)):
        delivered = session.add_teaching_pulse(
            str(node.params.get("correct_channel", "teach.pam11")), source=f"{node.id}:correct"
        )
    elif outcome == "incorrect" and bool(node.params.get("teach_incorrect", True)):
        delivered = session.add_teaching_pulse(
            str(node.params.get("incorrect_channel", "teach.ppl101")), source=f"{node.id}:incorrect"
        )
    record = {
        "outcome": outcome,
        "delivered": delivered.summary() if delivered else None,
        "note": "Engineered current into identified dopaminergic cells. Neither"
                " a reward nor a punishment, and nothing is experienced.",
    }
    return Result(outputs={"delivered": delivered is not None, "details": record}, record=record)


register(BlockType(
    key="train.teach", name="Teach from the outcome", plain="Reward-linked or punishment-linked pulse, chosen by how the pass went",
    summary="One block instead of two: sends the pulse to the reward-linked"
            " cells after a correct pass and to the punishment-linked cells"
            " after an incorrect one. The pulse lands on the next network step.",
    category="goal", run=_run_teach_outcome,
    inputs=(Port("outcome", "Outcome", "right, wrong or neither", "text", required=True,
                 tip="Connect the Outcome block's text output."),),
    outputs=(
        Port("delivered", "Delivered", "Whether a pulse was queued", "boolean", tip="False on a neutral pass."),
        Port("details", "Details", "Which cells were targeted", "record", tip=""),
    ),
    params=(
        channel_param("correct_channel", "Cells for 'right'", "Which cells get the correct-pass pulse",
                      "PAM11 is the narrow, validated reward-linked target used"
                      " for every recorded result in this project.",
                      role="modulatory", default="teach.pam11"),
        channel_param("incorrect_channel", "Cells for 'wrong'", "Which cells get the incorrect-pass pulse",
                      "PPL101 is the narrow, validated punishment-linked target"
                      " used for every recorded result in this project.",
                      role="modulatory", default="teach.ppl101"),
        Param("teach_correct", "Teach on right", "Send a pulse after a correct pass", "Turn off to teach only from mistakes.",
              kind="bool", default=True),
        Param("teach_incorrect", "Teach on wrong", "Send a pulse after an incorrect pass", "Turn off to teach only from successes.",
              kind="bool", default=True),
    ),
    tips=(
        "Shuffling which pulse follows which outcome, and comparing, is the"
        " control that separates learning from drift.",
    ),
))


# ==================================================================== registry
def catalogue():
    """Everything the interface needs to draw and describe the palette."""
    from .schema import CATEGORIES, TYPES

    return {
        "categories": [
            {"key": k, **v} for k, v in sorted(CATEGORIES.items(), key=lambda kv: kv[1]["order"])
        ],
        "types": TYPES,
        "blocks": [b.json() for b in REGISTRY.values()],
        "channels": [
            {
                "key": c.key, "name": c.name, "plain": c.plain, "detail": c.detail,
                "role": c.role, "modality": c.modality, "units": c.units,
                "fidelity": c.fidelity, "tags": list(c.tags), "notes": c.notes,
            }
            for c in atlas_module.CHANNELS
        ],
        "pins": gpio_io.pinout(),
        "baud_rates": list(links.BAUD_RATES),
    }


def get(key):
    if key not in REGISTRY:
        raise KeyError(f"Unknown block type: {key}")
    return REGISTRY[key]


# Blocks that only produce outputs when the run order reaches them. Asking for
# their value earlier returns whatever they last produced, which is also what
# lets a workflow feed a result back to an earlier block without deadlocking.
FLOW_ONLY = {
    key for key, block in REGISTRY.items()
    if block.category in ("flow", "encode", "brain", "output")
} | {"goal.define", "goal.outcome", "train.trial", "train.teach", "logic.latch", "logic.counter"}

for _key in FLOW_ONLY:
    import dataclasses as _dataclasses

    REGISTRY[_key] = _dataclasses.replace(REGISTRY[_key], pull=False)
del _key
