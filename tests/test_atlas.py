"""The channel catalogue: labelling contract, resolution and honesty."""

import numpy as np
import pytest

from flylab.neural import atlas


def test_every_channel_carries_all_three_labels():
    for channel in atlas.CHANNELS:
        assert channel.name.strip(), channel.key
        assert channel.plain.strip(), channel.key
        assert len(channel.detail) > 40, f"{channel.key} needs a real description"
        assert channel.role in ("input", "output", "modulatory", "internal")
        assert channel.modality in atlas.MODALITIES
        assert channel.fidelity in atlas.FIDELITY


def test_channel_keys_are_unique():
    keys = [c.key for c in atlas.CHANNELS]
    assert len(keys) == len(set(keys))


def test_every_modality_is_covered_by_at_least_one_channel():
    used = {c.modality for c in atlas.CHANNELS}
    assert used == set(atlas.MODALITIES)


def test_the_catalogue_spans_the_whole_animal():
    roles = {}
    for channel in atlas.CHANNELS:
        roles.setdefault(channel.role, []).append(channel.key)
    # Not just the handful a single application happens to use.
    assert len(roles["input"]) >= 15
    assert len(roles["output"]) >= 12
    assert len(roles["modulatory"]) >= 5
    for expected in ("vision.r1_r6", "olfaction.orn", "mechano.johnston",
                     "taste.sugar", "proprio.feco", "motor.descending",
                     "motor.leg", "memory.mbon", "compass.epg",
                     "teach.pam", "teach.ppl1"):
        assert expected in atlas.BY_KEY


def test_resolution_matches_annotations():
    types = np.asarray(["R1-R6", "R8p", "DNa02", "DNa02", "MBON07", "PAM11", "KCab"])
    classes = np.asarray(["sensory", "sensory", "descending", "descending",
                          "intrinsic", "intrinsic", "intrinsic"])
    sides = np.asarray(["L", "R", "L", "R", "L", "M", "L"])
    resolved = atlas.Atlas(types, classes, sides)
    assert resolved["vision.r1_r6"].present
    assert len(resolved["motor.steering"].index) == 2
    assert len(resolved["motor.steering"].left) == 1
    assert len(resolved["motor.steering"].right) == 1
    assert len(resolved["teach.pam11"].index) == 1
    assert resolved["teach.pam11"].middle.tolist() == [5]


def test_an_absent_channel_is_reported_not_hidden():
    resolved = atlas.Atlas(np.asarray(["KCab"]), np.asarray(["intrinsic"]), np.asarray([""]))
    summary = {c["key"]: c for c in resolved.catalogue()["channels"]}
    assert summary["olfaction.orn"]["present"] is False
    assert summary["olfaction.orn"]["cells"] == 0
    # Every channel still appears, so the interface can say what is missing.
    assert len(summary) == len(atlas.CHANNELS)


def test_a_body_part_is_narrowed_to_the_right_superclass():
    # A leg motor neuron and a descending neuron that steers a leg share the
    # same body-part subclass; only one of them is a motor neuron.
    types = np.asarray(["MNfl_Ti", "MN9", "DNa02"])
    superclasses = np.asarray(["vnc_motor", "vnc_motor", "descending_neuron"])
    subclasses = np.asarray(["fl", "pm", "fl"])
    resolved = atlas.Atlas(types, superclasses, np.asarray(["L", "L", "L"]),
                           subclasses=subclasses)
    assert len(resolved["motor.all"].index) == 2
    assert len(resolved["motor.leg"].index) == 1
    assert len(resolved["motor.descending"].index) == 1


def test_grouping_columns_resolve_cells_a_type_name_misses():
    types = np.asarray(["", "SNpp12", "KC_unclear"])
    superclasses = np.asarray(["vnc_sensory", "vnc_sensory", "cb_intrinsic"])
    classes = np.asarray(["gustatory", "mechanosensory_proprioceptive", "Kenyon_Cell"])
    resolved = atlas.Atlas(types, superclasses, np.asarray(["", "", ""]), classes=classes)
    assert len(resolved["taste.gustatory"].index) == 1
    assert len(resolved["internal.kenyon"].index) == 1
    assert len(resolved["sensory.all"].index) == 2


def test_sensory_laterality_falls_back_to_the_nerve_it_enters_by():
    import pandas as pd

    frame = pd.DataFrame({
        "type": ["ORN_DA1", "DNa02"],
        "superclass": ["cb_sensory", "descending_neuron"],
        "subclass": ["", ""],
        "class": ["olfactory", ""],
        "somaSide": [None, "R"],
        "rootSide": ["L", "R"],
    })
    resolved = atlas.Atlas.from_annotations(frame)
    assert resolved["olfaction.orn"].left.tolist() == [0]
    assert resolved["motor.steering"].right.tolist() == [1]


def test_resolution_on_the_real_loader(session):
    catalogue = session.atlas.catalogue()
    assert catalogue["present"] > 40
    assert catalogue["total"] == len(atlas.CHANNELS)
    coverage = session.atlas.coverage()
    assert coverage["neurons"] == session.brain.n
    assert coverage["in_at_least_one_channel"] > 0


def test_unknown_channel_is_a_clear_error(session):
    with pytest.raises(KeyError):
        session.cells("not.a.channel")
