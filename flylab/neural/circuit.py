"""Anatomically derived mushroom-body learning compartments in MaleCNS v1.0.

A compartment is a set of dopaminergic cells (DANs) together with the
mushroom-body output neurons (MBONs) they contact. Kenyon-cell (KC) synapses
onto those MBONs are the connections the candidate plasticity rule may modify.
Nothing is added to or removed from the graph: the plastic set is always a
subset of edges that already exist in the released reconstruction, and the
rest of the network keeps integrating exactly as before.

Presets:

``alpha1_gamma1pedc``
    PAM11 -> alpha1 / MBON07 and PPL101 -> gamma1pedc / MBON11. The pair used
    for every result recorded in ``docs/validation.md``.

``mushroom_body_full``
    Every MBON in the release that receives both KC input and direct DAN
    input, each driven by the DANs that actually contact it.

Applying one rate rule in every compartment is a modelling assumption, not a
replication of any published experiment. Real compartments differ in receptor
complement, sign and timing; none of that is modelled here.
"""

import re

import numpy as np

from .common import annotations, digest

# Dopaminergic cell families in the MaleCNS type vocabulary. Valence labels are
# summaries of the published *biology*, not claims about what this simulation
# computes or about anything the network experiences.
DAN_FAMILIES = {
    "PAM": {
        "pattern": r"^PAM\d+",
        "cluster": "Protocerebral anterior medial",
        "literature_valence": "appetitive/reward-associated in adult Drosophila",
    },
    "PPL1": {
        "pattern": r"^PPL1\d+",
        "cluster": "Protocerebral posterior lateral 1",
        "literature_valence": "aversive/punishment-associated in adult Drosophila",
    },
    "PPL2": {
        "pattern": r"^PPL2",
        "cluster": "Protocerebral posterior lateral 2",
        "literature_valence": "mixed; calyx-innervating, not a lobe compartment",
    },
    "PAL": {
        "pattern": r"^PAL\d*",
        "cluster": "Protocerebral anterior lateral",
        "literature_valence": "not established",
    },
}

MBON_PATTERN = r"^MBON\d+"
KC_PATTERN = r"^KC"

PRESETS = {
    "alpha1_gamma1pedc": {
        "label": "alpha1 + gamma1pedc (validated pair)",
        "plain": "Two learning compartments: one reward-linked, one punishment-linked.",
        "groups": [
            {
                "name": "alpha1",
                "dan_types": ["PAM11"],
                "mbon_types": ["MBON07"],
                "expect": [15, 4],
                "literature_valence": "appetitive/reward-associated",
            },
            {
                "name": "gamma1pedc",
                "dan_types": ["PPL101"],
                "mbon_types": ["MBON11"],
                "expect": [2, 2],
                "literature_valence": "aversive/punishment-associated",
            },
        ],
    },
    "mushroom_body_full": {
        "label": "Whole mushroom body",
        "plain": "Every output compartment this release supports, all at once.",
        "groups": None,
    },
}


class CompartmentGain:
    """Per-edge dopamine weighting, stored compactly.

    ``gain[d, e]`` equals ``fraction[d, compartment[e]]``: each plastic edge
    belongs to exactly one compartment, so a dense (DAN x edge) matrix repeats
    the same column many times. ``project`` computes ``gain.T @ vector``
    without materialising it, which keeps whole-mushroom-body runs tractable.
    """

    def __init__(self, fraction, compartment, names):
        self.fraction = np.ascontiguousarray(fraction, dtype=np.float32)
        self.compartment = np.ascontiguousarray(compartment, dtype=np.int32)
        self.names = list(names)
        if self.fraction.ndim != 2 or self.fraction.shape[1] != len(self.names):
            raise ValueError("Gain fraction must be (dan, compartment)")
        if self.compartment.ndim != 1:
            raise ValueError("Edge compartment index must be one dimensional")
        if len(self.compartment) and (
            self.compartment.min() < 0
            or self.compartment.max() >= self.fraction.shape[1]
        ):
            raise ValueError("Edge compartment index out of range")
        if not np.isfinite(self.fraction).all() or np.any(self.fraction < 0):
            raise ValueError("Gain fractions must be finite and nonnegative")
        self._wide = self.fraction.astype(np.float64)

    @property
    def shape(self):
        return (self.fraction.shape[0], len(self.compartment))

    def project(self, dan_vector):
        """Return per-edge dopamine drive for one DAN activity vector."""
        v = np.asarray(dan_vector, dtype=np.float64)
        if v.shape != (self.fraction.shape[0],):
            raise ValueError("DAN vector length does not match the gain matrix")
        return (v @ self._wide)[self.compartment]

    def dense(self):
        """Materialise the equivalent (DAN x edge) matrix, for audits/tests."""
        return self.fraction[:, self.compartment].copy()

    def digest(self):
        return {
            "fraction": digest(self.fraction),
            "compartment": digest(self.compartment),
            "names": list(self.names),
        }


def project_gain(gain, dan_vector):
    """Uniform accessor for either a dense matrix or a CompartmentGain."""
    if isinstance(gain, CompartmentGain):
        return gain.project(dan_vector)
    return np.asarray(gain).T @ np.asarray(dan_vector)


def _exact(types, names):
    if not names:
        return np.zeros(len(types), dtype=bool)
    joined = "|".join(f"(?:{re.escape(n)})" for n in names)
    return types.str.fullmatch(joined).to_numpy(dtype=bool)


def _family(types, patterns):
    joined = "|".join(f"(?:{p})" for p in patterns)
    return types.str.match(joined).to_numpy(dtype=bool)


def _out_contacts(brain, source):
    """Summed absolute contact magnitude from one cell onto each target."""
    sl = slice(brain.ptr[source], brain.ptr[source + 1])
    return brain.post[sl], np.abs(brain.weight[sl])


def survey(brain, a=None):
    """Report the dopaminergic families and MBONs present, selecting nothing."""
    a = annotations(brain.ids) if a is None else a
    types = a.type.fillna("")
    families = {}
    for name, info in DAN_FAMILIES.items():
        ix = np.flatnonzero(types.str.match(info["pattern"]))
        families[name] = {
            **info,
            "cells": int(len(ix)),
            "types": sorted({str(types.iloc[i]) for i in ix}),
        }
    mbons = np.flatnonzero(types.str.match(MBON_PATTERN))
    kcs = np.flatnonzero(types.str.match(KC_PATTERN))
    return {
        "dopaminergic_families": families,
        "mbon_cells": int(len(mbons)),
        "mbon_types": sorted({str(types.iloc[i]) for i in mbons}),
        "kenyon_cells": int(len(kcs)),
        "kenyon_types": sorted({str(types.iloc[i]) for i in kcs}),
    }


def identify(brain, preset="alpha1_gamma1pedc", a=None):
    """Build the plastic KC->MBON set and its dopamine gain for one preset."""
    if preset not in PRESETS:
        raise ValueError(f"Unknown compartment preset: {preset}")
    a = annotations(brain.ids) if a is None else a
    types = a.type.fillna("")
    kc = np.flatnonzero(types.str.match(KC_PATTERN)).astype(np.int32)
    if not len(kc):
        raise ValueError("No Kenyon cells in this release")

    definition = PRESETS[preset]["groups"]
    if definition is None:
        groups = _derive_groups(brain, types, kc)
    else:
        groups = []
        for spec in definition:
            dan_ix = np.flatnonzero(_exact(types, spec["dan_types"])).astype(np.int32)
            mbon_ix = np.flatnonzero(_exact(types, spec["mbon_types"])).astype(np.int32)
            expect = spec.get("expect")
            if expect is not None and [len(dan_ix), len(mbon_ix)] != list(expect):
                raise ValueError(
                    f"Unexpected cell counts for compartment {spec['name']}: got "
                    f"{[len(dan_ix), len(mbon_ix)]}, expected {list(expect)}"
                )
            if not len(dan_ix) or not len(mbon_ix):
                raise ValueError(f"Empty compartment: {spec['name']}")
            groups.append({**spec, "dan_index": dan_ix, "mbon_index": mbon_ix})

    mb = np.unique(np.concatenate([g["mbon_index"] for g in groups])).astype(np.int32)
    if sum(len(g["mbon_index"]) for g in groups) != len(mb):
        raise ValueError("An output neuron was claimed by two compartments")
    dan = np.unique(np.concatenate([g["dan_index"] for g in groups])).astype(np.int32)
    dan_row = {int(d): i for i, d in enumerate(dan)}
    column = np.full(brain.n, -1, dtype=np.int32)
    column[mb] = np.arange(len(mb), dtype=np.int32)

    # Every existing KC synapse onto a selected MBON, and nothing else.
    selected = np.zeros(brain.n, dtype=bool)
    selected[mb] = True
    edges = np.flatnonzero(selected[brain.post]).astype(np.int64)
    pre = (np.searchsorted(brain.ptr, edges, side="right") - 1).astype(np.int32)
    is_kc = np.zeros(brain.n, dtype=bool)
    is_kc[kc] = True
    keep = is_kc[pre]
    edges, pre = edges[keep], pre[keep]
    if not len(edges) or np.any(brain.weight[edges] <= 0):
        raise ValueError("Invalid reconstructed KC inputs")
    compartment = column[brain.post[edges]].astype(np.int32)
    edges_per_column = np.bincount(compartment, minlength=len(mb))

    # The gain a DAN has on an edge depends only on which MBON the edge
    # targets, so one column per selected MBON captures the whole matrix.
    fraction = np.zeros((len(dan), len(mb)), dtype=np.float32)
    support = []
    for group in groups:
        for target in group["mbon_index"]:
            col = int(column[target])
            contact = np.zeros(len(group["dan_index"]))
            for k, d in enumerate(group["dan_index"]):
                post, magnitude = _out_contacts(brain, d)
                contact[k] = float(magnitude[post == target].sum())
            if contact.sum() <= 0:
                raise ValueError(
                    "Missing direct DAN-to-MBON anatomical support for "
                    f"{types.iloc[target]} in {group['name']}"
                )
            for d, value in zip(group["dan_index"], contact / contact.sum()):
                fraction[dan_row[int(d)], col] = value
            support.append(
                {
                    "compartment": group["name"],
                    "mbon": str(types.iloc[target]),
                    "mbon_id": str(brain.ids[target]),
                    "dan_cells_contacting": int(np.count_nonzero(contact > 0)),
                    "dan_cells_in_group": int(len(group["dan_index"])),
                    "contact_magnitude": float(contact.sum()),
                    "kc_edges": int(edges_per_column[col]),
                }
            )
    gain = CompartmentGain(fraction, compartment, [str(types.iloc[i]) for i in mb])

    mask = np.zeros(brain.n, dtype=np.uint8)
    mask[kc] = 1
    dan_index = np.full(brain.n, -1, dtype=np.int32)
    dan_index[dan] = np.arange(len(dan), dtype=np.int32)

    def cells(ix):
        return [
            {
                "index": int(i),
                "id": str(brain.ids[i]),
                "type": str(types.iloc[i]),
                "instance": str(a.instance.iloc[i]),
            }
            for i in ix
        ]

    named = {}
    for group in groups:
        named[group["name"]] = {
            "dan": group["dan_index"],
            "mbon": group["mbon_index"],
            "dan_types": sorted({str(types.iloc[i]) for i in group["dan_index"]}),
            "mbon_types": sorted({str(types.iloc[i]) for i in group["mbon_index"]}),
            "literature_valence": group.get("literature_valence", "not established"),
        }
    report = {
        "release": "MaleCNS v1.0",
        "preset": preset,
        "preset_label": PRESETS[preset]["label"],
        "neurons": int(brain.n),
        "directed_edges": int(len(brain.post)),
        "kenyon_cells": int(len(kc)),
        "dopaminergic_cells": int(len(dan)),
        "output_neurons": int(len(mb)),
        "plastic_edges": int(len(edges)),
        "plastic_edges_sha256": digest(edges),
        "compartments": [
            {
                "name": g["name"],
                "dan_types": named[g["name"]]["dan_types"],
                "mbon_types": named[g["name"]]["mbon_types"],
                "dan_cell_count": int(len(g["dan_index"])),
                "mbon_cell_count": int(len(g["mbon_index"])),
                "dan_cells": cells(g["dan_index"]) if len(g["dan_index"]) <= 32 else None,
                "mbon_cells": cells(g["mbon_index"]) if len(g["mbon_index"]) <= 32 else None,
                "literature_valence": named[g["name"]]["literature_valence"],
            }
            for g in groups
        ],
        "anatomical_support": support,
        "selection": "All existing KC-to-selected-MBON connections; no graph cropping, no added edges.",
        "gain": "Within-compartment DAN-to-MBON contact fractions; not measured receptor or dopamine kinetics.",
        "validated": False,
    }
    return {
        "preset": preset,
        "kc": kc,
        "mb": mb,
        "dan": dan,
        "groups": named,
        # Legacy aliases: a two-compartment configuration stays addressable by
        # the role names the earlier controller used.
        "reward": named.get("alpha1", {}).get("dan", np.zeros(0, np.int32)),
        "aversive": named.get("gamma1pedc", {}).get("dan", np.zeros(0, np.int32)),
        "edges": edges,
        "pre": pre,
        "gain": gain,
        "kc_mask": mask,
        "dan_index": dan_index,
        "report": report,
    }


def _derive_groups(brain, types, kc):
    """One group per MBON that has both KC input and direct DAN input."""
    mbon_ix = np.flatnonzero(types.str.match(MBON_PATTERN)).astype(np.int32)
    if not len(mbon_ix):
        raise ValueError("No mushroom-body output neurons in this release")
    dan_ix = np.flatnonzero(
        _family(types, [info["pattern"] for info in DAN_FAMILIES.values()])
    ).astype(np.int32)
    if not len(dan_ix):
        raise ValueError("No dopaminergic neurons in this release")

    is_mbon = np.zeros(brain.n, dtype=bool)
    is_mbon[mbon_ix] = True
    is_kc = np.zeros(brain.n, dtype=bool)
    is_kc[kc] = True

    # Which MBONs receive KC input at all: one pass over the KC-targeted edges.
    kc_target = np.zeros(brain.n, dtype=bool)
    for cell in kc:
        sl = slice(brain.ptr[cell], brain.ptr[cell + 1])
        targets = brain.post[sl]
        kc_target[targets[is_mbon[targets]]] = True

    drivers = {int(m): [] for m in mbon_ix}
    for d in dan_ix:
        sl = slice(brain.ptr[d], brain.ptr[d + 1])
        targets = brain.post[sl]
        for target in np.unique(targets[is_mbon[targets]]):
            drivers[int(target)].append(int(d))

    groups = []
    for m in mbon_ix:
        if not kc_target[m] or not drivers[int(m)]:
            continue
        groups.append(
            {
                "name": f"{types.iloc[m]}#{int(m)}",
                "mbon_index": np.asarray([m], dtype=np.int32),
                "dan_index": np.asarray(sorted(drivers[int(m)]), dtype=np.int32),
                "literature_valence": "see DAN_FAMILIES for the cluster summary",
            }
        )
    if not groups:
        raise ValueError("No MBON has both KC and DAN input in this release")
    return groups
