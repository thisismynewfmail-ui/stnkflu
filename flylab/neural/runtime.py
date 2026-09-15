"""A general-purpose session around the fly graph.

The earlier build of this project wired one decoder to one market. This module
replaces that with a named-channel interface: anything a workflow can measure
becomes drive on a named group of real cells, and anything a workflow wants to
do reads a named group of real cells back out. The network in between is the
unmodified retained graph.

Nothing here decides what a workflow should do. It provides addressing,
stimulation, integration, readout and provenance; the workflow supplies the
task, and ``docs/model.md`` states what the numbers do and do not mean.
"""

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import atlas as atlas_module
from . import signals
from .circuit import PRESETS, survey
from .common import annotations, data_root, digest, set_data_root
from .fixture import is_fixture

VIEWPORT_SAMPLE = 3600


@dataclass
class RuntimeConfig:
    """Everything that changes what the network does, in one place."""

    data_dir: str = ""
    compartments: str = "alpha1_gamma1pedc"
    learning: bool = True
    eta: float = 0.001
    neural_bin_ms: float = 10.0
    lamina_bias: float = 12.0
    kc_rest_mv: float = -60.0
    adaptation_jump_mv: float = 8.0
    adaptation_tau_ms: float = 200.0
    default_step_ms: float = 500.0
    teach_pulse_ms: float = 200.0
    teach_current_mv: float = 20.0
    viewport_cells: int = VIEWPORT_SAMPLE

    def validate(self):
        if self.compartments not in PRESETS:
            raise ValueError(f"Unknown compartment preset: {self.compartments}")
        if not 0 < self.neural_bin_ms <= 10:
            raise ValueError("Neural bins must be greater than 0 and at most 10 ms")
        for name in ("default_step_ms", "teach_pulse_ms"):
            value = getattr(self, name)
            if value <= 0 or abs(value * 10 - round(value * 10)) > 1e-7:
                raise ValueError(f"{name} must be a positive multiple of 0.1 ms")
        # A pulse longer than the step it lands in is clamped by step(),
        # because each block may ask for a different step duration.
        if self.eta < 0 or not np.isfinite(self.eta):
            raise ValueError("Learning gain must be finite and nonnegative")
        if not 1 <= self.viewport_cells <= 20000:
            raise ValueError("Viewport sample must be between 1 and 20000 cells")
        return self


@dataclass
class Stimulus:
    """One pending current injection, ready to hand to the kernel."""

    channel: str
    cells: np.ndarray
    current: np.ndarray
    label: str = ""
    source: str = ""

    def spec(self):
        return (self.cells, self.current)

    def summary(self):
        current = np.atleast_1d(self.current)
        return {
            "channel": self.channel,
            "label": self.label,
            "source": self.source,
            "cells": int(len(self.cells)),
            "peak_current_mv": float(current.max()) if len(current) else 0.0,
            "mean_current_mv": float(current.mean()) if len(current) else 0.0,
        }


@dataclass
class StepResult:
    duration_ms: float
    seconds: float
    learning: bool
    counts: np.ndarray = field(repr=False, default=None)
    compute_seconds: float = 0.0
    stimuli: list = field(default_factory=list)
    memory: dict = field(default_factory=dict)
    brain_ms: float = 0.0
    total_spikes: int = 0
    synthetic_fixture: bool = False

    def summary(self):
        return {
            "duration_ms": self.duration_ms,
            "seconds": self.seconds,
            "learning": self.learning,
            "compute_seconds": round(self.compute_seconds, 6),
            "brain_ms": self.brain_ms,
            "total_spikes": self.total_spikes,
            "stimuli": [s.summary() for s in self.stimuli],
            "memory": self.memory,
            "synthetic_fixture": self.synthetic_fixture,
        }


class BrainSession:
    """Loaded graph plus channel addressing. One per running workflow."""

    def __init__(self, config=None, brain=None):
        self.config = (config or RuntimeConfig()).validate()
        self.root = (
            set_data_root(self.config.data_dir)
            if self.config.data_dir
            else data_root()
        )
        self.synthetic_fixture = is_fixture(self.root)
        self.opened_at = time.time()
        self._pending = []
        self._last = None
        self._frame = None
        self._layout = None
        if brain is not None:
            self.brain = brain
        else:
            from .visual import VisualMemoryBrain

            self.brain = VisualMemoryBrain(
                path=self.root / "graph.npz",
                eta=self.config.eta,
                compartments=self.config.compartments,
                kc_rest=self.config.kc_rest_mv,
                adaptation_jump=self.config.adaptation_jump_mv,
                adaptation_tau=self.config.adaptation_tau_ms,
            )
        self.brain.weights_frozen = not self.config.learning
        self.annotations = annotations(self.brain.ids)
        self.atlas = atlas_module.Atlas.from_annotations(self.annotations)
        self.manifest = self._read_json(self.root / "manifest.json")
        self.counts = np.zeros(self.brain.n, dtype=np.int32)
        self.window_seconds = 0.0
        self.steps = 0

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _read_json(path):
        path = Path(path)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            return {}

    def channel(self, key):
        return self.atlas[key]

    def cells(self, key, side="both", subtype=None):
        resolved = self.atlas[key]
        if subtype is not None:
            if subtype not in resolved.subtypes:
                raise KeyError(f"{key} has no subtype {subtype!r} in this release")
            index = resolved.subtypes[subtype]
        else:
            index = {
                "both": resolved.index,
                "left": resolved.left,
                "right": resolved.right,
                "middle": resolved.middle,
            }[side]
        return np.asarray(index, dtype=np.int32)

    # ------------------------------------------------------------- stimulation
    def clear_stimuli(self):
        self._pending = []
        self._frame = None

    def add_stimulus(self, key, value, *, side="both", subtype=None, curve="saturating",
                     current=None, low=0.0, high=1.0, groups_by_subtype=False,
                     label="", source=""):
        """Queue drive onto one channel for the next step.

        ``value`` may be a single reading or a vector. ``low``/``high`` give the
        real-world range that maps onto the channel's full drive, so a distance
        sensor reading centimetres and a brightness reading 0-1 both arrive in
        the same normalised form.
        """
        cells = self.cells(key, side=side, subtype=subtype)
        if not len(cells):
            raise ValueError(
                f"Channel {key} matches no cell in this release; it cannot be driven"
            )
        channel = self.atlas[key].channel
        peak = float(channel.default_current if current is None else current)
        if not np.isfinite(peak) or peak <= 0:
            raise ValueError("Stimulus current must be positive and finite")
        unit = signals.normalise(value, low, high)
        groups = None
        if groups_by_subtype:
            groups = [self.atlas[key].subtypes[k] for k in sorted(self.atlas[key].subtypes)]
        levels = signals.spread(unit, cells, groups)
        shaper = signals.CURVES.get(curve)
        if shaper is None:
            raise ValueError(f"Unknown drive curve: {curve}")
        amplitude = shaper(levels, peak=peak)
        stimulus = Stimulus(
            channel=key,
            cells=cells,
            current=np.asarray(amplitude, dtype=np.float32),
            label=label or channel.plain,
            source=source,
        )
        self._pending.append(stimulus)
        return stimulus

    def add_teaching_pulse(self, key, *, current=None, label="", source=""):
        """Queue a full-strength pulse onto a teaching channel.

        This is an engineered current injection into identified modulatory
        cells. It is not a reward, a punishment, or an experience.
        """
        channel = self.atlas[key].channel
        if channel.role != "modulatory":
            raise ValueError(f"{key} is not a teaching channel")
        return self.add_stimulus(
            key,
            1.0,
            curve="linear",
            current=self.config.teach_current_mv if current is None else current,
            label=label or channel.plain,
            source=source,
        )

    def set_image(self, frame):
        """Supply the RGB frame the photoreceptor channels will sample."""
        frame = np.asarray(frame)
        if frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise ValueError("Vision input must be an HxWx3 uint8 RGB array")
        self._frame = frame
        return frame

    # ------------------------------------------------------------- integration
    def step(self, duration_ms=None, *, learning=None, pulse_ms=None):
        """Advance neural time, delivering everything queued since the last step."""
        duration_ms = float(self.config.default_step_ms if duration_ms is None else duration_ms)
        if duration_ms <= 0 or abs(duration_ms * 10 - round(duration_ms * 10)) > 1e-7:
            raise ValueError("Step duration must be a positive multiple of 0.1 ms")
        learning = self.config.learning if learning is None else bool(learning)
        pulse_ms = float(self.config.teach_pulse_ms if pulse_ms is None else pulse_ms)
        pulse_ms = min(pulse_ms, duration_ms)
        brain = self.brain
        sustained = [s for s in self._pending if self.atlas[s.channel].channel.role != "modulatory"]
        pulsed = [s for s in self._pending if self.atlas[s.channel].channel.role == "modulatory"]

        counts = np.zeros(brain.n, dtype=np.int32)
        wall = 0.0
        remaining = round(duration_ms / brain.dt)
        pulse_left = round(pulse_ms / brain.dt) if pulsed else 0
        delivered = 0
        bin_ticks = max(1, round(self.config.neural_bin_ms / brain.dt))
        while remaining:
            ticks = min(remaining, bin_ticks)
            if pulse_left:
                ticks = min(ticks, pulse_left)
            active = [s.spec() for s in sustained]
            if pulse_left:
                active += [s.spec() for s in pulsed]
            if self._frame is not None:
                chunk, elapsed = brain.rgb_step(
                    self._frame,
                    ticks * brain.dt,
                    learning=learning,
                    stimulation=active or None,
                    lamina_bias=self.config.lamina_bias,
                )
            else:
                chunk, elapsed = brain.step(
                    np.zeros(len(brain.retina), dtype=np.float32),
                    ticks * brain.dt,
                    learning=learning,
                    stimulation=active or None,
                    lamina_bias=self.config.lamina_bias,
                )
            counts += chunk
            wall += elapsed
            remaining -= ticks
            if pulse_left:
                delivered += ticks
                pulse_left -= ticks
        brain.counts[:] = counts
        self.counts = counts
        self.window_seconds = duration_ms / 1000
        self.steps += 1
        result = StepResult(
            duration_ms=duration_ms,
            seconds=self.window_seconds,
            learning=learning,
            counts=counts,
            compute_seconds=wall,
            stimuli=list(self._pending),
            memory=brain.memory(),
            brain_ms=float(brain.sim_ms),
            total_spikes=int(counts.sum()),
            synthetic_fixture=self.synthetic_fixture,
        )
        result.memory["teaching_pulse_ms"] = delivered * brain.dt
        self._last = result
        self.clear_stimuli()
        return result

    # ----------------------------------------------------------------- readout
    def _window(self, seconds=None):
        seconds = self.window_seconds if seconds is None else seconds
        if seconds <= 0:
            raise RuntimeError("Read a channel only after a step has run")
        return seconds

    def read_rate(self, key, side="both", subtype=None, seconds=None):
        return signals.rate(self.counts, self.cells(key, side, subtype), self._window(seconds))

    def read_differential(self, key, seconds=None):
        resolved = self.atlas[key]
        return signals.differential(
            self.counts, resolved.left, resolved.right, self._window(seconds)
        )

    def read_gate(self, key, minimum=1):
        return signals.gate(self.counts, self.cells(key), minimum)

    def read_bump(self, key, side="both", seconds=None):
        return signals.bump_angle(self.counts, self.cells(key, side), self._window(seconds))

    def read_population(self, key, seconds=None):
        seconds = self._window(seconds)
        resolved = self.atlas[key]
        return {
            name: signals.rate(self.counts, index, seconds)
            for name, index in sorted(resolved.subtypes.items())
        }

    def read_channel(self, key, seconds=None):
        """Everything a readout block might want from one channel, at once."""
        seconds = self._window(seconds)
        resolved = self.atlas[key]
        return {
            "key": key,
            "name": resolved.channel.name,
            "plain": resolved.channel.plain,
            "units": resolved.channel.units,
            "cells": int(len(resolved.index)),
            "hz": signals.rate(self.counts, resolved.index, seconds),
            "left_hz": signals.rate(self.counts, resolved.left, seconds),
            "right_hz": signals.rate(self.counts, resolved.right, seconds),
            "difference_hz": signals.differential(
                self.counts, resolved.left, resolved.right, seconds
            ),
            "spikes": int(np.asarray(self.counts)[resolved.index].sum()) if len(resolved.index) else 0,
            "active_cells": int(np.count_nonzero(np.asarray(self.counts)[resolved.index])) if len(resolved.index) else 0,
            "window_seconds": seconds,
            "synthetic_fixture": self.synthetic_fixture,
        }

    def classify(self, options, margin=0.0, seconds=None):
        """Winner-take-all across labelled cell groups.

        ``options`` maps a label onto a channel key (optionally ``key@side``).
        The label with the highest mean rate wins, provided it leads the runner
        up by ``margin`` spikes per second.
        """
        seconds = self._window(seconds)
        scores = {}
        for label, target in options.items():
            key, _, side = str(target).partition("@")
            scores[label] = signals.rate(
                self.counts, self.cells(key, side or "both"), seconds
            )
        choice, value, lead = signals.winner(scores, margin)
        return {
            "choice": choice,
            "scores": scores,
            "winner_hz": value,
            "lead_hz": lead,
            "margin_hz": float(margin),
            "undecided": choice is None,
        }

    # --------------------------------------------------------------- telemetry
    def layout(self):
        """A stable schematic map of the network for the viewport.

        Positions are a readable diagram, not anatomical coordinates. Cells are
        grouped by the region their annotations place them in and scattered
        deterministically inside that region so the picture does not jitter
        between frames.
        """
        if self._layout is not None:
            return self._layout
        regions = REGION_LAYOUT
        assignment = np.full(self.brain.n, -1, dtype=np.int32)
        names = list(regions)
        for position, name in enumerate(names):
            for key in regions[name]["channels"]:
                resolved = self.atlas.resolved.get(key)
                if resolved is None:
                    continue
                unclaimed = resolved.index[assignment[resolved.index] < 0]
                assignment[unclaimed] = position
        leftover = np.flatnonzero(assignment < 0)
        assignment[leftover] = names.index("other")
        rng = np.random.default_rng(7)
        x = np.zeros(self.brain.n, dtype=np.float32)
        y = np.zeros(self.brain.n, dtype=np.float32)
        for position, name in enumerate(names):
            index = np.flatnonzero(assignment == position)
            if not len(index):
                continue
            box = regions[name]
            angle = rng.random(len(index)) * 2 * np.pi
            radius = np.sqrt(rng.random(len(index)))
            x[index] = box["x"] + box["rx"] * radius * np.cos(angle)
            y[index] = box["y"] + box["ry"] * radius * np.sin(angle)
        # Stratified, not uniform. A uniform sample of 166,700 cells would draw
        # about two of the 97 mushroom-body output neurons and neither cell of
        # a bilateral steering pair, so the regions that matter most would be
        # the ones you could not see. Every region gets a floor first, then the
        # remainder is shared out in proportion.
        sample = np.arange(self.brain.n)
        budget = self.config.viewport_cells
        if self.brain.n > budget:
            rng = np.random.default_rng(11)
            members = [np.flatnonzero(assignment == i) for i in range(len(names))]
            occupied = [m for m in members if len(m)]
            floor = min(80, max(1, budget // max(1, 2 * len(occupied))))
            chosen = []
            for group in occupied:
                take = min(len(group), floor)
                chosen.append(rng.choice(group, take, replace=False))
            taken = sum(len(c) for c in chosen)
            spare = max(0, budget - taken)
            if spare:
                remaining = np.setdiff1d(np.arange(self.brain.n), np.concatenate(chosen))
                extra = min(spare, len(remaining))
                if extra:
                    chosen.append(rng.choice(remaining, extra, replace=False))
            sample = np.sort(np.concatenate(chosen))
        self._layout = {
            "regions": [
                {"key": name, **{k: v for k, v in regions[name].items() if k != "channels"}}
                for name in names
            ],
            "sample": sample.astype(np.int32),
            "x": x[sample].round(4).tolist(),
            "y": y[sample].round(4).tolist(),
            "region": assignment[sample].astype(int).tolist(),
            "cells_total": int(self.brain.n),
            "cells_shown": int(len(sample)),
            "note": "Schematic layout by annotated region. Not anatomical"
            " coordinates and not to scale.",
        }
        return self._layout

    def viewport_frame(self):
        """Per-cell activity for the sampled cells, plus per-region rates."""
        layout = self.layout()
        seconds = max(self.window_seconds, 1e-6)
        sample = layout["sample"]
        activity = np.asarray(self.counts)[sample].astype(np.float32) / seconds
        region_index = np.asarray(layout["region"])
        regions = []
        for position, region in enumerate(layout["regions"]):
            here = sample[region_index == position]
            rates = np.asarray(self.counts)[here].astype(np.float64) / seconds if len(here) else np.zeros(0)
            regions.append(
                {
                    "key": region["key"],
                    "label": region["label"],
                    "cells": int(len(here)),
                    "mean_hz": float(rates.mean()) if len(rates) else 0.0,
                    "peak_hz": float(rates.max()) if len(rates) else 0.0,
                    "active": int(np.count_nonzero(rates)),
                }
            )
        return {
            "activity": np.round(activity, 2).tolist(),
            "regions": regions,
            "window_seconds": seconds,
            "brain_ms": float(self.brain.sim_ms),
            "total_spikes": int(self.counts.sum()),
            "synthetic_fixture": self.synthetic_fixture,
        }

    def channel_activity(self, roles=("input", "output", "modulatory", "internal")):
        seconds = max(self.window_seconds, 1e-6)
        out = []
        for resolved in self.atlas.resolved.values():
            if resolved.channel.role not in roles or not resolved.present:
                continue
            out.append(
                {
                    "key": resolved.channel.key,
                    "name": resolved.channel.name,
                    "plain": resolved.channel.plain,
                    "role": resolved.channel.role,
                    "modality": resolved.channel.modality,
                    "cells": int(len(resolved.index)),
                    "hz": signals.rate(self.counts, resolved.index, seconds),
                    "difference_hz": signals.differential(
                        self.counts, resolved.left, resolved.right, seconds
                    ),
                }
            )
        return out

    # -------------------------------------------------------------- provenance
    def catalogue(self):
        return {
            "dataset": {
                **self.manifest,
                "root": str(self.root),
                "synthetic_fixture": self.synthetic_fixture,
            },
            "graph": {
                "neurons": int(self.brain.n),
                "directed_edges": int(len(self.brain.post)),
                "retina_cells": int(len(self.brain.retina)),
                "colour_cells": int(len(getattr(self.brain, "r8", []))),
                "lamina_cells": int(len(self.brain.lamina)),
            },
            "compartments": self.brain.circuit["report"],
            "compartment_presets": {
                k: {"label": v["label"], "plain": v["plain"]} for k, v in PRESETS.items()
            },
            "mushroom_body_survey": survey(self.brain, self.annotations),
            "atlas": self.atlas.catalogue(),
            "coverage": self.atlas.coverage(),
            "vision": getattr(self.brain, "visual_report", {}),
            "config": asdict(self.config),
            "synthetic_fixture": self.synthetic_fixture,
        }

    def provenance(self):
        package = Path(__file__).resolve().parent.parent
        return {
            "config": asdict(self.config),
            "compartments": self.brain.circuit["report"],
            "configuration_sha256": self.brain.configuration_signature(),
            "initial_weight_sha256": self.brain.initial_weight_sha256,
            "graph_ids_sha256": digest(self.brain.ids),
            "dataset": self.manifest,
            "synthetic_fixture": self.synthetic_fixture,
            "source_sha256": {
                str(path.relative_to(package)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted(package.rglob("*"))
                if path.suffix in (".py", ".cpp")
            },
        }

    def signature(self):
        return hashlib.sha256(
            json.dumps(self.provenance(), sort_keys=True).encode()
        ).hexdigest()

    # -------------------------------------------------------------- checkpoints
    def save(self, path, label="", notes=""):
        path = Path(path)
        self.brain.checkpoint(path)
        record = {
            "label": label,
            "notes": notes,
            "saved_at": time.time(),
            "steps": self.steps,
            "brain_ms": float(self.brain.sim_ms),
            "total_spikes": int(self.brain.total_spikes),
            "memory": self.brain.memory(),
            "compartments": self.config.compartments,
            "learning": self.config.learning,
            "synthetic_fixture": self.synthetic_fixture,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        path.with_suffix(".json").write_text(json.dumps(record, indent=2) + "\n")
        return record

    def restore(self, path):
        path = Path(path)
        self.brain.restore(path)
        self.counts = np.zeros(self.brain.n, dtype=np.int32)
        self.window_seconds = 0.0
        return self._read_json(path.with_suffix(".json"))

    def reset(self, keep_memory=False):
        self.brain.reset(keep_memory=keep_memory)
        self.counts = np.zeros(self.brain.n, dtype=np.int32)
        self.window_seconds = 0.0
        self.steps = 0
        self.clear_stimuli()


# Schematic viewport regions: a readable diagram of where work happens, in a
# normalised 0-1 box. Channel keys claim cells in listed order.
REGION_LAYOUT = {
    "eye": {
        "label": "Compound eye (photoreceptors)",
        "x": 0.10, "y": 0.28, "rx": 0.075, "ry": 0.20,
        "channels": ["vision.r1_r6", "vision.r7", "vision.r8", "vision.dra"],
    },
    "optic": {
        "label": "Optic lobe (lamina, medulla, lobula)",
        "x": 0.26, "y": 0.30, "rx": 0.085, "ry": 0.22,
        "channels": ["internal.lamina", "internal.motion", "internal.lobula_lc", "vision.ocelli"],
    },
    "antenna": {
        "label": "Antenna (smell, sound, wind, humidity, temperature)",
        "x": 0.10, "y": 0.70, "rx": 0.075, "ry": 0.18,
        "channels": [
            "olfaction.orn", "olfaction.hrn", "thermo.trn",
            "mechano.johnston", "mechano.wind",
        ],
    },
    "antennal_lobe": {
        "label": "Antennal lobe and lateral horn",
        "x": 0.27, "y": 0.72, "rx": 0.075, "ry": 0.16,
        "channels": ["internal.antennal_lobe", "internal.lateral_horn"],
    },
    "mouth": {
        "label": "Mouthparts (taste and touch)",
        "x": 0.115, "y": 0.93, "rx": 0.065, "ry": 0.05,
        "channels": ["taste.sugar", "taste.bitter", "taste.labellar", "mechano.bristle"],
    },
    "mushroom": {
        "label": "Mushroom body (Kenyon cells)",
        "x": 0.50, "y": 0.26, "rx": 0.10, "ry": 0.15,
        "channels": ["internal.kenyon"],
    },
    "teaching": {
        "label": "Dopaminergic and modulatory cells",
        "x": 0.50, "y": 0.50, "rx": 0.075, "ry": 0.07,
        "channels": [
            "teach.pam", "teach.ppl1", "teach.octopamine",
            "teach.serotonin", "clock.circadian",
        ],
    },
    "mbon": {
        "label": "Mushroom body output (learned value)",
        "x": 0.50, "y": 0.70, "rx": 0.075, "ry": 0.055,
        "channels": ["memory.mbon"],
    },
    "central_complex": {
        "label": "Central complex (heading and goal)",
        "x": 0.68, "y": 0.30, "rx": 0.08, "ry": 0.14,
        "channels": ["compass.epg", "compass.fc2", "compass.pfl3", "internal.central_complex"],
    },
    "descending": {
        "label": "Descending neurons (brain to body)",
        "x": 0.84, "y": 0.42, "rx": 0.07, "ry": 0.16,
        "channels": ["motor.descending"],
    },
    "motor": {
        "label": "Motor neurons (muscles)",
        "x": 0.945, "y": 0.62, "rx": 0.045, "ry": 0.17,
        "channels": ["motor.all", "motor.leg", "motor.wing", "motor.neck", "motor.proboscis", "motor.haltere"],
    },
    "ascending": {
        "label": "Ascending and proprioceptive feedback",
        "x": 0.80, "y": 0.90, "rx": 0.085, "ry": 0.065,
        "channels": [
            "ascending.vnc", "proprio.feco", "proprio.campaniform",
            "proprio.hairplate", "nociception.md",
        ],
    },
    "endocrine": {
        "label": "Neurosecretory cells",
        "x": 0.60, "y": 0.90, "rx": 0.048, "ry": 0.055,
        "channels": ["endocrine.all"],
    },
    "other": {
        "label": "Central brain (everything else)",
        "x": 0.63, "y": 0.11, "rx": 0.20, "ry": 0.075,
        "channels": ["internal.all"],
    },
}
