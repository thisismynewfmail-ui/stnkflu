"""The workflow document: blocks, wires and the checks run before a run starts.

A workflow has two kinds of wire. A run-order wire says which block happens
next. A value wire says where a block's input comes from. Keeping them
separate is what makes loops, branches and waits readable on the canvas
instead of implied by evaluation order.

``check`` never refuses to save a workflow. It returns problems with a
severity so a half-built workflow can sit on the canvas while you think, and
only a run is blocked by an error.
"""

import time
import uuid
from dataclasses import dataclass, field

from . import blocks as block_module

COMPATIBLE = {
    ("number", "boolean"), ("boolean", "number"), ("number", "text"),
    ("boolean", "text"), ("text", "number"), ("record", "text"),
    ("record", "number"), ("vector", "number"), ("number", "vector"),
    ("record", "vector"), ("spikes", "vector"), ("record", "boolean"),
}


def new_id(prefix="n"):
    return f"{prefix}{uuid.uuid4().hex[:10]}"


@dataclass
class Node:
    id: str
    type: str
    label: str = ""
    notes: str = ""
    params: dict = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    collapsed: bool = False
    disabled: bool = False

    def json(self):
        return {
            "id": self.id, "type": self.type, "label": self.label, "notes": self.notes,
            "params": dict(self.params), "x": self.x, "y": self.y,
            "collapsed": self.collapsed, "disabled": self.disabled,
        }


@dataclass
class Wire:
    id: str
    kind: str  # "flow" or "value"
    source: str
    source_port: str
    target: str
    target_port: str = ""

    def json(self):
        return {
            "id": self.id, "kind": self.kind, "source": self.source,
            "source_port": self.source_port, "target": self.target,
            "target_port": self.target_port,
        }


class Workflow:
    def __init__(self, name="Untitled workflow", nodes=None, wires=None, meta=None):
        self.name = name
        self.nodes = {n.id: n for n in (nodes or [])}
        self.wires = {w.id: w for w in (wires or [])}
        self.meta = dict(meta or {})
        self.meta.setdefault("created", time.time())

    # ---------------------------------------------------------------- editing
    def add(self, type_key, x=0.0, y=0.0, label="", params=None, node_id=None):
        block = block_module.get(type_key)
        node = Node(
            id=node_id or new_id(),
            type=type_key,
            label=label,
            params={**block.defaults(), **(params or {})},
            x=float(x), y=float(y),
        )
        self.nodes[node.id] = node
        return node

    def connect(self, source, source_port, target, target_port="", kind=None):
        if source not in self.nodes or target not in self.nodes:
            raise KeyError("Both ends of a wire must be blocks in this workflow")
        kind = kind or ("flow" if not target_port else "value")
        wire = Wire(new_id("w"), kind, source, source_port, target, target_port)
        if kind == "flow":
            for existing in list(self.wires.values()):
                # One run-order wire per outlet: a branch has to be explicit.
                if existing.kind == "flow" and existing.source == source and existing.source_port == source_port:
                    del self.wires[existing.id]
        else:
            port = self._input_port(target, target_port)
            if port is not None and not port.multiple:
                for existing in list(self.wires.values()):
                    if existing.kind == "value" and existing.target == target and existing.target_port == target_port:
                        del self.wires[existing.id]
        self.wires[wire.id] = wire
        return wire

    def remove(self, node_id):
        self.nodes.pop(node_id, None)
        for wire_id, wire in list(self.wires.items()):
            if wire.source == node_id or wire.target == node_id:
                del self.wires[wire_id]

    def disconnect(self, wire_id):
        self.wires.pop(wire_id, None)

    # ---------------------------------------------------------------- lookups
    def _input_port(self, node_id, port_key):
        node = self.nodes.get(node_id)
        if node is None or node.type not in block_module.REGISTRY:
            return None
        for port in block_module.get(node.type).inputs:
            if port.key == port_key:
                return port
        return None

    def _output_port(self, node_id, port_key):
        node = self.nodes.get(node_id)
        if node is None or node.type not in block_module.REGISTRY:
            return None
        for port in block_module.get(node.type).outputs:
            if port.key == port_key:
                return port
        return None

    def flow_target(self, node_id, port):
        for wire in self.wires.values():
            if wire.kind == "flow" and wire.source == node_id and wire.source_port == port:
                target = self.nodes.get(wire.target)
                if target is not None and not target.disabled:
                    return wire.target
                if target is not None and target.disabled:
                    return self.flow_target(wire.target, "next")
        return None

    def value_sources(self, node_id, port):
        return [
            (wire.source, wire.source_port)
            for wire in self.wires.values()
            if wire.kind == "value" and wire.target == node_id and wire.target_port == port
        ]

    def start_node(self):
        for node in self.nodes.values():
            if node.type == "flow.start" and not node.disabled:
                return node
        return None

    # ------------------------------------------------------------- validation
    def check(self):
        """Return problems, each with severity error, warning or note."""
        problems = []

        def add(severity, message, node=None, wire=None, fix=""):
            problems.append(
                {"severity": severity, "message": message, "node": node, "wire": wire, "fix": fix}
            )

        starts = [n for n in self.nodes.values() if n.type == "flow.start"]
        if not starts:
            add("error", "This workflow has no Start block, so a run has nowhere to begin.",
                fix="Add a Start block from the Run order group.")
        elif len(starts) > 1:
            add("error", f"There are {len(starts)} Start blocks; a run can only begin in one place.",
                node=starts[1].id, fix="Delete the extra Start blocks.")
        elif not self.flow_target(starts[0].id, "next"):
            add("warning", "Nothing is connected to Start, so a run would do nothing.",
                node=starts[0].id, fix="Drag from the Start block's Next outlet to the first step.")

        counts = {}
        for node in self.nodes.values():
            if node.type not in block_module.REGISTRY:
                add("error", f"Unknown block type '{node.type}'. It may come from a newer version.",
                    node=node.id, fix="Delete the block, or open this workflow in the version that made it.")
                continue
            block = block_module.get(node.type)
            counts[node.type] = counts.get(node.type, 0) + 1
            if block.limit and counts[node.type] > block.limit:
                add("error", f"Only {block.limit} '{block.name}' block is allowed per workflow.", node=node.id)
            for port in block.inputs:
                if port.required and not self.value_sources(node.id, port.key) and not node.disabled:
                    add("error", f"'{block.name}' needs something connected to {port.label}.",
                        node=node.id, fix=f"{port.tip or port.plain}")
            if block.needs and not node.disabled:
                from ..io.registry import probe

                for capability in block.needs:
                    info = probe(capability)
                    if not info["available"]:
                        add("warning",
                            f"'{block.name}' uses {info['name']}, which is not available on this machine.",
                            node=node.id,
                            fix=f"Install it ({info['install_hint']}), or turn on device simulation in the run settings.")

        for wire in self.wires.values():
            if wire.source not in self.nodes or wire.target not in self.nodes:
                add("error", "A wire points at a block that no longer exists.", wire=wire.id,
                    fix="Delete the wire.")
                continue
            if wire.kind != "value":
                continue
            source = self._output_port(wire.source, wire.source_port)
            target = self._input_port(wire.target, wire.target_port)
            if source is None or target is None:
                add("error", "A value wire points at a port that no longer exists.", wire=wire.id,
                    fix="Delete the wire and reconnect it.")
                continue
            if source.type != target.type and "any" not in (source.type, target.type):
                if (source.type, target.type) in COMPATIBLE:
                    add("note",
                        f"{source.label} ({source.type}) is being read as {target.type}. That"
                        " conversion is automatic but worth knowing about.",
                        wire=wire.id)
                else:
                    add("error",
                        f"{source.label} produces {source.type}, but {target.label} expects"
                        f" {target.type}. These cannot be connected.",
                        wire=wire.id, fix="Insert a block that converts between them.")

        cycle = self._value_cycle()
        if cycle:
            names = " -> ".join(self.describe(n) for n in cycle)
            add("error", f"These value wires form a loop: {names}.", node=cycle[0],
                fix="Insert a 'Hold a value' block to break the loop; it reports the previous"
                    " pass's value instead of asking for this one.")

        reachable = self.reachable()
        for node in self.nodes.values():
            if node.disabled or node.type not in block_module.REGISTRY:
                continue
            block = block_module.get(node.type)
            if node.id in reachable or block.pull:
                continue
            add("warning", f"'{block.name}' is never reached, so it will not run.", node=node.id,
                fix="Connect it into the run order, or delete it.")
        return problems

    def describe(self, node_id):
        node = self.nodes.get(node_id)
        if node is None:
            return node_id
        if node.label:
            return node.label
        if node.type in block_module.REGISTRY:
            return block_module.get(node.type).name
        return node.type

    def reachable(self):
        start = self.start_node()
        if start is None:
            return set()
        seen = set()
        frontier = [start.id]
        while frontier:
            node_id = frontier.pop()
            if node_id in seen or node_id not in self.nodes:
                continue
            seen.add(node_id)
            node = self.nodes[node_id]
            if node.type not in block_module.REGISTRY:
                continue
            for port in block_module.get(node.type).flow_out:
                target = self.flow_target(node_id, port)
                if target:
                    frontier.append(target)
        # A block that feeds a reachable block is itself part of the run.
        changed = True
        while changed:
            changed = False
            for wire in self.wires.values():
                if wire.kind == "value" and wire.target in seen and wire.source not in seen:
                    seen.add(wire.source)
                    changed = True
        return seen

    def _value_cycle(self):
        """Find a cycle among blocks that are evaluated on demand."""
        graph = {}
        for node in self.nodes.values():
            if node.type not in block_module.REGISTRY or not block_module.get(node.type).pull:
                continue
            graph[node.id] = [
                wire.source
                for wire in self.wires.values()
                if wire.kind == "value" and wire.target == node.id
                and wire.source in self.nodes
                and self.nodes[wire.source].type in block_module.REGISTRY
                and block_module.get(self.nodes[wire.source].type).pull
            ]
        colour = {}
        path = []

        def visit(node_id):
            colour[node_id] = 1
            path.append(node_id)
            for parent in graph.get(node_id, ()):
                if colour.get(parent) == 1:
                    return path[path.index(parent):] + [parent]
                if colour.get(parent) is None:
                    found = visit(parent)
                    if found:
                        return found
            path.pop()
            colour[node_id] = 2
            return None

        for node_id in graph:
            if colour.get(node_id) is None:
                found = visit(node_id)
                if found:
                    return found
        return None

    def errors(self):
        return [p for p in self.check() if p["severity"] == "error"]

    # ------------------------------------------------------------------- json
    def json(self):
        return {
            "name": self.name,
            "nodes": [n.json() for n in self.nodes.values()],
            "wires": [w.json() for w in self.wires.values()],
            "meta": dict(self.meta),
        }

    @classmethod
    def load(cls, data):
        nodes = [
            Node(
                id=str(n["id"]), type=str(n["type"]), label=str(n.get("label", "")),
                notes=str(n.get("notes", "")), params=dict(n.get("params", {})),
                x=float(n.get("x", 0)), y=float(n.get("y", 0)),
                collapsed=bool(n.get("collapsed", False)),
                disabled=bool(n.get("disabled", False)),
            )
            for n in data.get("nodes", [])
        ]
        wires = [
            Wire(
                id=str(w.get("id") or new_id("w")), kind=str(w.get("kind", "value")),
                source=str(w["source"]), source_port=str(w.get("source_port", "")),
                target=str(w["target"]), target_port=str(w.get("target_port", "")),
            )
            for w in data.get("wires", [])
        ]
        return cls(str(data.get("name", "Untitled workflow")), nodes, wires, data.get("meta"))
