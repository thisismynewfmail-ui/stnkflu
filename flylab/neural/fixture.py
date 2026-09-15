"""Synthetic bench fixture: a small fake graph for plumbing tests only.

This is NOT the connectome. It is a few thousand randomly wired cells carrying
a representative subset of real MaleCNS type names, written in exactly the
on-disk layout the real loader expects. Its only purpose is to exercise the
software - loader, kernel, atlas, workflow engine, interface - on a machine
that has not downloaded the 1.1 GB release, and to make the automated tests
run in seconds.

Every dataset this module writes contains a ``FIXTURE`` marker file and sets
``synthetic_fixture: true`` in its manifest. The runtime refuses to hide that:
any run against a fixture stamps ``synthetic_fixture`` into its telemetry, its
checkpoints and its exported results, and the interface shows a permanent
warning. Numbers produced from a fixture describe this random graph and say
nothing whatsoever about flies.
"""

import json
from pathlib import Path

import numpy as np

MARKER = "FIXTURE"

# Representative real type names, so the atlas and compartment code exercise
# the same matching paths they take on the released dataset.
SUBCLASS = {
    "retina": "", "inner": "", "lamina": "", "medulla": "", "central": "",
    "antennal": "auditory", "bristle": "mechanosensory bristle",
    "taste": "labellar bristle", "vnc": "chordotonal organ",
    "mn": "fl", "dn": "", "mbon": "", "dan": "", "modulator": "",
    "clock": "", "cx": "", "mushroom": "", "endocrine": "",
}
CLASS = {
    "retina": "visual", "inner": "visual", "lamina": "visual", "medulla": "visual",
    "antennal": "olfactory", "taste": "gustatory", "bristle": "mechanosensory_tactile",
    "vnc": "mechanosensory_proprioceptive", "mushroom": "Kenyon_Cell",
    "mbon": "MBON", "dan": "DAN", "cx": "CX", "central": "", "dn": "", "mn": "",
    "modulator": "", "clock": "", "endocrine": "",
}

PLAN = [
    # (type, count, superclass, sided, group)
    ("R1-R6", 240, "ol_sensory", True, "retina"),
    ("R7p", 40, "ol_sensory", True, "inner"),
    ("R7y", 40, "ol_sensory", True, "inner"),
    ("R8p", 60, "ol_sensory", True, "inner"),
    ("R8y", 60, "ol_sensory", True, "inner"),
    ("L1", 40, "ol_intrinsic", True, "lamina"),
    ("L2", 40, "ol_intrinsic", True, "lamina"),
    ("L3", 30, "ol_intrinsic", True, "lamina"),
    ("L5", 20, "ol_intrinsic", True, "lamina"),
    ("aMe12", 12, "ol_intrinsic", True, "medulla"),
    ("Mi1", 24, "ol_intrinsic", True, "medulla"),
    ("T4a", 12, "ol_intrinsic", True, "medulla"),
    ("T4b", 12, "ol_intrinsic", True, "medulla"),
    ("T5a", 12, "ol_intrinsic", True, "medulla"),
    ("T5b", 12, "ol_intrinsic", True, "medulla"),
    ("LC4", 10, "visual_projection", True, "central"),
    ("LPLC2", 10, "visual_projection", True, "central"),
    ("LC11", 8, "visual_projection", True, "central"),
    ("OCG01", 6, "cb_sensory", True, "central"),
    ("ORN_DA1", 8, "cb_sensory", True, "antennal"),
    ("ORN_DM1", 8, "cb_sensory", True, "antennal"),
    ("ORN_VA1v", 8, "cb_sensory", True, "antennal"),
    ("ORN_V", 6, "cb_sensory", True, "antennal"),
    ("HRN_dry", 4, "cb_sensory", True, "antennal"),
    ("TRN_cold", 4, "cb_sensory", True, "antennal"),
    ("JO-A", 10, "cb_sensory", True, "antennal"),
    ("JO-B", 10, "cb_sensory", True, "antennal"),
    ("JO-C", 6, "cb_sensory", True, "antennal"),
    ("JO-D", 6, "cb_sensory", True, "antennal"),
    ("JO-E", 6, "cb_sensory", True, "antennal"),
    ("JO-F", 6, "cb_sensory", True, "antennal"),
    ("BM_InOm", 12, "cb_sensory", True, "bristle"),
    ("LB3c", 6, "cb_sensory", True, "taste"),
    ("LB1a", 6, "cb_sensory", True, "taste"),
    ("Gr66a", 6, "cb_sensory", True, "taste"),
    ("TP1", 4, "cb_sensory", True, "taste"),
    ("FeCO_club", 8, "vnc_sensory", True, "vnc"),
    ("FeCO_claw", 8, "vnc_sensory", True, "vnc"),
    ("CS_Fe", 6, "vnc_sensory", True, "vnc"),
    ("HP_Co", 6, "vnc_sensory", True, "vnc"),
    ("AN_05", 10, "ascending_neuron", True, "vnc"),
    ("M_lPNm11", 12, "cb_intrinsic", True, "antennal"),
    ("LHAV2a1", 10, "cb_intrinsic", True, "central"),
    ("KCab", 120, "cb_intrinsic", True, "mushroom"),
    ("KCg", 120, "cb_intrinsic", True, "mushroom"),
    ("KCapbp", 60, "cb_intrinsic", True, "mushroom"),
    ("APL", 2, "cb_intrinsic", True, "mushroom"),
    ("MBON07", 4, "cb_intrinsic", True, "mbon"),
    ("MBON11", 2, "cb_intrinsic", True, "mbon"),
    ("MBON01", 2, "cb_intrinsic", True, "mbon"),
    ("MBON05", 2, "cb_intrinsic", True, "mbon"),
    ("PAM11", 15, "cb_intrinsic", True, "dan"),
    ("PAM01", 8, "cb_intrinsic", True, "dan"),
    ("PAM04", 8, "cb_intrinsic", True, "dan"),
    ("PPL101", 2, "cb_intrinsic", True, "dan"),
    ("PPL102", 2, "cb_intrinsic", True, "dan"),
    ("OA-VUMa2", 4, "cb_intrinsic", True, "modulator"),
    ("CSDn", 2, "cb_intrinsic", True, "modulator"),
    ("s-LNv", 4, "cb_intrinsic", True, "clock"),
    ("EPG", 16, "cb_intrinsic", True, "cx"),
    ("PEN_a", 12, "cb_intrinsic", True, "cx"),
    ("Delta7", 8, "cb_intrinsic", True, "cx"),
    ("FC2", 8, "cb_intrinsic", True, "cx"),
    ("PFL3", 12, "cb_intrinsic", True, "cx"),
    ("hDeltaB", 8, "cb_intrinsic", True, "cx"),
    ("DNa01", 2, "descending_neuron", True, "dn"),
    ("DNa02", 2, "descending_neuron", True, "dn"),
    ("DNa03", 2, "descending_neuron", True, "dn"),
    ("DNb06", 2, "descending_neuron", True, "dn"),
    ("DNp01", 2, "descending_neuron", True, "dn"),
    ("DNp09", 2, "descending_neuron", True, "dn"),
    ("DNp20", 2, "descending_neuron", True, "dn"),
    ("DNpe017", 2, "descending_neuron", True, "dn"),
    ("MDN", 4, "descending_neuron", True, "dn"),
    ("MN9", 2, "vnc_motor", True, "mn"),
    ("MN11", 2, "vnc_motor", True, "mn"),
    ("MNfl_Ti", 6, "vnc_motor", True, "mn"),
    ("MNwm_b1", 2, "vnc_motor", True, "mn"),
    ("MNwm_i1", 2, "vnc_motor", True, "mn"),
    ("MNnm_01", 2, "vnc_motor", True, "mn"),
    ("IPC", 6, "cb_endocrine", True, "endocrine"),
    ("DH44", 4, "cb_endocrine", True, "endocrine"),
    ("SMP123", 60, "cb_intrinsic", True, "central"),
    ("CRE011", 40, "cb_intrinsic", True, "central"),
    ("LAL045", 40, "cb_intrinsic", True, "central"),
]

TRANSMITTER = {
    "dan": "dopamine",
    "modulator": "octopamine",
    "mbon": "glutamate",
    "lamina": "histamine",
    "retina": "acetylcholine",
    "inner": "acetylcholine",
}


def _neurotransmitter(group, rng):
    if group in TRANSMITTER:
        return TRANSMITTER[group]
    return str(
        rng.choice(
            ["acetylcholine", "gaba", "glutamate"], p=[0.6, 0.25, 0.15]
        )
    )


def build_fixture(root, seed=20260915, connections_per_cell=26):
    """Write a synthetic dataset to ``root`` and return its manifest."""
    import pandas as pd
    import pyarrow.feather as feather

    root = Path(root)
    (root / "normalized").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    types, groups, classes, sides = [], [], [], []
    for name, count, superclass, sided, group in PLAN:
        for k in range(count):
            types.append(name)
            groups.append(group)
            classes.append(superclass)
            sides.append(("L", "R")[k % 2] if sided else "M")
    n = len(types)
    types = np.asarray(types)
    groups = np.asarray(groups)
    classes = np.asarray(classes)
    sides = np.asarray(sides)
    ids = np.arange(720_000_000_000, 720_000_000_000 + n, dtype=np.int64)

    # Retinotopic columns: a 12 x 12 axial hex grid per eye, shared by the
    # photoreceptors and the lamina cells they contact.
    hex_axes = np.arange(12)
    grid = np.asarray([(int(p), int(q)) for p in hex_axes for q in hex_axes])
    column = np.full((n, 2), -1, dtype=np.int64)
    for group_name in ("retina", "inner", "lamina", "medulla"):
        ix = np.flatnonzero(groups == group_name)
        for side in ("L", "R"):
            here = ix[sides[ix] == side]
            if not len(here):
                continue
            column[here] = grid[rng.integers(0, len(grid), len(here))]

    index_of = {}
    for i, t in enumerate(types):
        index_of.setdefault(t, []).append(i)
    index_of = {k: np.asarray(v, dtype=np.int32) for k, v in index_of.items()}

    def pool(*names):
        found = [index_of[k] for k in names if k in index_of]
        return np.concatenate(found) if found else np.zeros(0, dtype=np.int32)

    kc = pool("KCab", "KCg", "KCapbp")
    mbon = pool("MBON07", "MBON11", "MBON01", "MBON05")
    lamina = pool("L1", "L2", "L3", "L5")
    medulla = pool("Mi1", "T4a", "T4b", "T5a", "T5b", "aMe12")
    retina = pool("R1-R6")
    inner = pool("R7p", "R7y", "R8p", "R8y")
    r8 = pool("R8p", "R8y")
    ame12 = pool("aMe12")
    dans = pool("PAM11", "PAM01", "PAM04", "PPL101", "PPL102")

    pairs = set()

    def link(a, b):
        if a != b:
            pairs.add((int(a), int(b)))

    def connect(sources, targets, fan, same_column=False):
        if not len(sources) or not len(targets):
            return
        for s in sources:
            if same_column:
                near = targets[
                    (column[targets, 0] == column[s, 0])
                    & (column[targets, 1] == column[s, 1])
                ]
                chosen = near if len(near) else targets[rng.integers(0, len(targets), 1)]
                for t in chosen[: max(1, fan)]:
                    link(s, t)
                continue
            for t in rng.choice(targets, size=min(fan, len(targets)), replace=False):
                link(s, t)

    # Structured pathways the loaders and encoders depend on.
    connect(retina, lamina, 3, same_column=True)
    connect(inner, medulla, 3, same_column=True)
    connect(r8, ame12, 2)
    connect(lamina, medulla, 4)
    connect(medulla, pool("LC4", "LPLC2", "LC11"), 3)
    connect(pool("LC4", "LPLC2", "LC11"), pool("DNp01", "DNp09", "SMP123"), 3)
    connect(pool("ORN_DA1", "ORN_DM1", "ORN_VA1v", "ORN_V"), pool("M_lPNm11"), 3)
    connect(pool("M_lPNm11"), np.concatenate([kc, pool("LHAV2a1")]), 8)
    connect(pool("LB3c", "LB1a", "Gr66a", "TP1"), pool("PAM11", "PPL101", "MN9"), 3)
    connect(pool("JO-A", "JO-B", "JO-C", "JO-D", "JO-E", "JO-F"), pool("CRE011", "LAL045"), 3)
    connect(pool("FeCO_club", "FeCO_claw", "CS_Fe", "HP_Co"), pool("AN_05"), 2)
    connect(pool("AN_05"), pool("LAL045", "SMP123"), 3)
    connect(pool("BM_InOm"), pool("CRE011"), 2)
    # Every Kenyon cell contacts every output neuron: the plastic set.
    for k in kc:
        for m in mbon:
            link(k, m)
    # Dopaminergic innervation of the output neurons.
    for d in pool("PAM11"):
        for m in pool("MBON07"):
            link(d, m)
    for d in pool("PPL101"):
        for m in pool("MBON11"):
            link(d, m)
    for d in pool("PAM01"):
        for m in pool("MBON01"):
            link(d, m)
    for d in pool("PAM04"):
        for m in pool("MBON05"):
            link(d, m)
    connect(mbon, pool("CRE011", "LAL045", "SMP123"), 4)
    connect(pool("EPG"), pool("PEN_a", "Delta7", "PFL3"), 4)
    connect(pool("FC2"), pool("PFL3"), 4)
    connect(pool("PFL3"), pool("DNa02", "DNa01", "LAL045"), 2)
    connect(pool("LAL045", "CRE011", "SMP123"), pool("DNa01", "DNa02", "DNa03", "DNb06", "DNp09", "DNp20", "DNpe017", "MDN"), 4)
    connect(
        pool("DNa01", "DNa02", "DNa03", "DNb06", "DNp01", "DNp09", "DNp20", "DNpe017", "MDN"),
        pool("MN9", "MN11", "MNfl_Ti", "MNwm_b1", "MNwm_i1", "MNnm_01"),
        3,
    )
    connect(pool("OA-VUMa2", "CSDn", "s-LNv"), pool("KCab", "KCg", "SMP123"), 6)
    connect(pool("SMP123", "CRE011", "LAL045"), pool("IPC", "DH44"), 2)
    # Background wiring so no cell is isolated.
    everyone = np.arange(n, dtype=np.int32)
    for s in everyone:
        for t in rng.choice(everyone, size=connections_per_cell, replace=False):
            link(s, t)

    edges = np.asarray(sorted(pairs), dtype=np.int64)
    pre, post = edges[:, 0].astype(np.int32), edges[:, 1].astype(np.int32)
    counts = rng.integers(1, 12, len(pre)).astype(np.uint32)

    neurotransmitter = np.asarray([_neurotransmitter(g, rng) for g in groups])
    sign = np.where(
        np.isin(neurotransmitter, ["gaba", "glutamate", "histamine"]), -1, 1
    ).astype(np.int8)
    weight = (counts.astype(np.float32) * sign[pre] * 0.275).astype(np.float32)
    # Kenyon-cell drive onto output neurons must stay excitatory: the
    # plasticity rule is defined on positive baseline efficacies.
    is_kc = np.zeros(n, dtype=bool)
    is_kc[kc] = True
    is_mbon = np.zeros(n, dtype=bool)
    is_mbon[mbon] = True
    plastic = is_kc[pre] & is_mbon[post]
    weight[plastic] = np.abs(weight[plastic])
    ptr = np.r_[0, np.cumsum(np.bincount(pre, minlength=n))].astype(np.int64)

    # Retinotopic sample points, built the same way prepare.py builds them.
    mapped = np.asarray(sorted(retina.tolist()), dtype=np.int32)
    hexes = column[mapped].astype(float)
    xy = np.column_stack(
        [hexes[:, 0] - 0.5 * hexes[:, 1], np.sqrt(3) / 2 * hexes[:, 1]]
    )
    uv = np.empty_like(xy)
    for side in ("L", "R"):
        mask = sides[mapped] == side
        z = xy[mask]
        span = z.max(axis=0) - z.min(axis=0)
        span[span == 0] = 1.0
        z = (z - z.min(axis=0)) / span
        uv[mask, 0] = 0.60 * z[:, 0] if side == "L" else 0.40 + 0.60 * (1 - z[:, 0])
        uv[mask, 1] = 1 - z[:, 1]

    np.savez(
        root / "graph.npz",
        ptr=ptr,
        post=post,
        weight=weight,
        ids=ids,
        retina=mapped,
        uv=np.clip(uv, 0, 1).astype(np.float32),
        confidence=np.ones(len(mapped)),
        hexes=hexes,
        lamina=np.asarray(sorted(lamina.tolist()), dtype=np.int32),
        sugar=np.asarray(sorted(pool("LB3c").tolist()), dtype=np.int32),
        superclass=np.asarray(classes, dtype="U64"),
    )

    hex1 = np.where(column[:, 0] >= 0, column[:, 0].astype(float), np.nan)
    hex2 = np.where(column[:, 1] >= 0, column[:, 1].astype(float), np.nan)
    annotations = pd.DataFrame(
        {
            "bodyId": ids,
            "type": types,
            "instance": [f"{t}_{s}" for t, s in zip(types, sides)],
            "superclass": classes,
            "subclass": [SUBCLASS.get(g, "") for g in groups],
            "class": [CLASS.get(g, "") for g in groups],
            "somaSide": sides,
            "rootSide": sides,
            "status": "Traced",
            "statusLabel": "Traced",
            "assignedOlHex1": hex1,
            "assignedOlHex2": hex2,
        }
    )
    feather.write_feather(annotations, root / "annotations.feather")
    neurons = pd.DataFrame(
        {
            "node_index": np.arange(n, dtype=np.uint32),
            "source_id": ids,
            "retained": True,
            "object_kind": "neuron_candidate",
            "inclusion_reason": "synthetic_fixture",
            "quality": "Traced",
            "superclass": classes,
            "cell_type": types,
            "neurotransmitter": neurotransmitter,
            "neurotransmitter_source": "synthetic_fixture",
        }
    )
    feather.write_feather(neurons, root / "normalized" / "neurons.feather")

    manifest = {
        "dataset": "synthetic_fixture",
        "synthetic_fixture": True,
        "warning": "Randomly wired test graph. Not a connectome. Any number "
        "derived from it describes this fixture and nothing biological.",
        "seed": int(seed),
        "neurons": int(n),
        "edges": int(len(pre)),
        "synaptic_contacts": int(counts.sum(dtype=np.uint64)),
        "retina_total": int(len(retina)),
        "retina_mapped": int(len(mapped)),
        "types": int(len(index_of)),
        "plastic_kc_mbon_edges": int(plastic.sum()),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (root / MARKER).write_text(
        "Synthetic bench fixture. This directory does not contain the MaleCNS "
        "release. Delete it to force use of real data.\n"
    )
    return manifest


def is_fixture(root):
    return (Path(root) / MARKER).exists()


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "data-fixture")
    print(json.dumps(build_fixture(target), indent=2))
