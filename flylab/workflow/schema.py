"""Declarations for workflow blocks: ports, parameters and labelling rules.

Every user-facing string in a block is required to carry three things:

``name``     the technical term, so the label matches the literature;
``plain``    what it literally does, in ordinary words;
``tip``      what happens when you use it, shown on hover.

The registry refuses to load a block that is missing any of them, which is why
the interface can promise a tooltip on every control.
"""

from dataclasses import dataclass, field

# Port value types. The colour is the wire colour the editor draws.
TYPES = {
    "flow": {"label": "Run order", "plain": "Which block runs next", "colour": "#e8eef5"},
    "number": {"label": "Number", "plain": "A single value", "colour": "#ffb02e"},
    "boolean": {"label": "Yes/No", "plain": "True or false", "colour": "#57d98a"},
    "text": {"label": "Text", "plain": "A word or label", "colour": "#4fd1e0"},
    "image": {"label": "Picture", "plain": "An RGB frame", "colour": "#b083f0"},
    "vector": {"label": "List of numbers", "plain": "Several values at once", "colour": "#6ba4ff"},
    "stimulus": {"label": "Neural drive", "plain": "Current queued onto real cells", "colour": "#ff7a45"},
    "spikes": {"label": "Spike counts", "plain": "What the network just did", "colour": "#f2f5f8"},
    "record": {"label": "Details", "plain": "A structured result", "colour": "#9aa7b4"},
    "any": {"label": "Anything", "plain": "Any value", "colour": "#7c8894"},
}

CATEGORIES = {
    "flow": {
        "label": "Run order",
        "plain": "Start, wait, branch, repeat, stop",
        "accent": "#e8eef5",
        "order": 0,
    },
    "source": {
        "label": "Inputs",
        "plain": "Where values come from: sensors, pins, files, the clock",
        "accent": "#ffb02e",
        "order": 1,
    },
    "vision": {
        "label": "Vision inputs",
        "plain": "Pictures: screen, camera, files, web pages",
        "accent": "#b083f0",
        "order": 2,
    },
    "encode": {
        "label": "Into the network",
        "plain": "Turn a value into current on named cells",
        "accent": "#ff7a45",
        "order": 3,
    },
    "brain": {
        "label": "Network",
        "plain": "Run neural time, save and load memory",
        "accent": "#4fd1e0",
        "order": 4,
    },
    "decode": {
        "label": "Out of the network",
        "plain": "Read named cells back as values",
        "accent": "#f2f5f8",
        "order": 5,
    },
    "logic": {
        "label": "Decide",
        "plain": "Compare, combine, smooth, count",
        "accent": "#57d98a",
        "order": 6,
    },
    "output": {
        "label": "Outputs",
        "plain": "Pins, serial, pointer, network, logs",
        "accent": "#ff5d73",
        "order": 7,
    },
    "goal": {
        "label": "Goal and training",
        "plain": "Define success, score it, teach the network",
        "accent": "#ffd23f",
        "order": 8,
    },
}


@dataclass(frozen=True)
class Port:
    key: str
    label: str
    plain: str
    type: str = "number"
    multiple: bool = False
    required: bool = False
    tip: str = ""

    def json(self):
        return {
            "key": self.key,
            "label": self.label,
            "plain": self.plain,
            "type": self.type,
            "type_label": TYPES[self.type]["label"],
            "colour": TYPES[self.type]["colour"],
            "multiple": self.multiple,
            "required": self.required,
            "tip": self.tip or self.plain,
        }


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    plain: str
    tip: str
    kind: str = "number"  # number text select bool channel pin region code vector path
    default: object = 0
    options: tuple = ()
    minimum: float = None
    maximum: float = None
    step: float = None
    unit: str = ""
    placeholder: str = ""
    advanced: bool = False
    depends: tuple = ()  # (param_key, value) - only shown when it matches

    def json(self):
        return {
            "key": self.key,
            "label": self.label,
            "plain": self.plain,
            "tip": self.tip,
            "kind": self.kind,
            "default": self.default,
            "options": [
                {"value": o[0], "label": o[1], "plain": o[2] if len(o) > 2 else ""}
                if isinstance(o, (tuple, list))
                else {"value": o, "label": str(o), "plain": ""}
                for o in self.options
            ],
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "unit": self.unit,
            "placeholder": self.placeholder,
            "advanced": self.advanced,
            "depends": list(self.depends),
        }


@dataclass(frozen=True)
class BlockType:
    key: str
    name: str
    plain: str
    summary: str
    category: str
    run: object = None
    flow_in: bool = True
    flow_out: tuple = ("next",)
    flow_labels: dict = field(default_factory=dict)
    inputs: tuple = ()
    outputs: tuple = ()
    params: tuple = ()
    needs: tuple = ()  # device capability keys
    tips: tuple = ()  # extra hover lines
    caution: str = ""
    limit: int = 0  # 0 = unlimited instances, 1 = exactly one per workflow
    pull: bool = True  # may be evaluated on demand to supply a value

    def json(self):
        category = CATEGORIES[self.category]
        return {
            "key": self.key,
            "name": self.name,
            "plain": self.plain,
            "summary": self.summary,
            "category": self.category,
            "category_label": category["label"],
            "accent": category["accent"],
            "flow_in": self.flow_in,
            "flow_out": [
                {
                    "key": k,
                    "label": self.flow_labels.get(k, {}).get("label", k.replace("_", " ")),
                    "plain": self.flow_labels.get(k, {}).get("plain", ""),
                }
                for k in self.flow_out
            ],
            "inputs": [p.json() for p in self.inputs],
            "outputs": [p.json() for p in self.outputs],
            "params": [p.json() for p in self.params],
            "needs": list(self.needs),
            "tips": list(self.tips),
            "caution": self.caution,
            "limit": self.limit,
            "pull": self.pull,
        }

    def defaults(self):
        return {p.key: p.default for p in self.params}


def validate(block):
    """Enforce the labelling contract before a block reaches the interface."""
    if block.category not in CATEGORIES:
        raise ValueError(f"{block.key}: unknown category {block.category}")
    for text, field_name in ((block.name, "name"), (block.plain, "plain"), (block.summary, "summary")):
        if not text or not str(text).strip():
            raise ValueError(f"{block.key}: {field_name} is required")
    seen = set()
    for port in list(block.inputs) + list(block.outputs):
        if port.type not in TYPES:
            raise ValueError(f"{block.key}.{port.key}: unknown port type {port.type}")
        if not port.label or not port.plain:
            raise ValueError(f"{block.key}.{port.key}: every port needs a label and a plain description")
    for port in block.inputs:
        if port.key in seen:
            raise ValueError(f"{block.key}: duplicate input port {port.key}")
        seen.add(port.key)
    seen = set()
    for port in block.outputs:
        if port.key in seen:
            raise ValueError(f"{block.key}: duplicate output port {port.key}")
        seen.add(port.key)
    for param in block.params:
        if not param.tip:
            raise ValueError(f"{block.key}.{param.key}: every parameter needs a hover tip")
        if param.kind == "select" and not param.options:
            raise ValueError(f"{block.key}.{param.key}: a select needs options")
    if block.run is None:
        raise ValueError(f"{block.key}: no implementation")
    return block
