"""The simulator: compartments, the plasticity rule, and what a step does."""

import numpy as np
import pytest

from flylab.neural.circuit import CompartmentGain, project_gain
from flylab.neural.rule import advance


def trace_protocol(order, frozen=False):
    kc = np.zeros(2)
    dan = np.zeros(1)
    u = np.zeros(2)
    w = np.zeros(2)
    gain = np.ones((1, 2))
    for phase in order:
        for _ in range(20):
            kc_hz = np.array([20.0, 0.0]) if phase == "cue" else np.zeros(2)
            dan_hz = np.array([30.0]) if phase == "reinforce" else np.zeros(1)
            advance(kc, dan, u, w, kc_hz, dan_hz, gain, 0.01, 0.001, frozen=frozen)
    return w


def test_memory_rule_is_temporally_specific():
    paired = trace_protocol(["cue", "reinforce"])
    reverse = trace_protocol(["reinforce", "cue"])
    assert paired[0] < 0 and reverse[0] > 0
    # An input that never fired is never modified.
    assert paired[1] == 0 and reverse[1] == 0


def test_freezing_blocks_every_write():
    assert np.array_equal(trace_protocol(["cue", "reinforce"], True), np.zeros(2))


def test_compact_gain_equals_the_dense_matrix():
    fraction = np.asarray([[0.6, 0.0], [0.4, 1.0]], dtype=np.float32)
    compartment = np.asarray([0, 0, 1, 1, 0], dtype=np.int32)
    gain = CompartmentGain(fraction, compartment, ["MBON07", "MBON11"])
    vector = np.asarray([3.0, 5.0])
    assert np.allclose(gain.project(vector), gain.dense().T @ vector)
    assert np.allclose(project_gain(gain, vector), project_gain(gain.dense(), vector))
    assert gain.shape == (2, 5)


def test_gain_rejects_a_mismatched_vector():
    gain = CompartmentGain(np.ones((2, 1), np.float32), np.zeros(3, np.int32), ["MBON07"])
    with pytest.raises(ValueError):
        gain.project(np.ones(5))


def test_rate_bins_longer_than_ten_milliseconds_are_refused():
    with pytest.raises(ValueError):
        advance(np.zeros(1), np.zeros(1), np.zeros(1), np.zeros(1),
                np.zeros(1), np.zeros(1), np.ones((1, 1)), 0.05, 0.001)


def test_the_validated_preset_selects_exactly_the_documented_cells(session):
    circuit = session.brain.circuit
    assert circuit["preset"] == "alpha1_gamma1pedc"
    groups = circuit["groups"]
    assert len(groups["alpha1"]["dan"]) == 15
    assert len(groups["gamma1pedc"]["dan"]) == 2
    assert groups["alpha1"]["mbon_types"] == ["MBON07"]
    assert groups["gamma1pedc"]["mbon_types"] == ["MBON11"]
    # Every plastic edge is a KC synapse that already existed in the graph.
    kc = set(circuit["kc"].tolist())
    assert set(circuit["pre"].tolist()) <= kc
    assert np.all(session.brain.weight[circuit["edges"]] != 0)


def test_the_whole_mushroom_body_preset_is_a_superset(fixture_root):
    from flylab.neural.visual import VisualMemoryBrain

    narrow = VisualMemoryBrain(path=fixture_root / "graph.npz")
    wide = VisualMemoryBrain(path=fixture_root / "graph.npz",
                             compartments="mushroom_body_full")
    assert len(wide.circuit["edges"]) > len(narrow.circuit["edges"])
    assert set(narrow.circuit["edges"].tolist()) <= set(wide.circuit["edges"].tolist())
    assert len(wide.circuit["mb"]) > len(narrow.circuit["mb"])


def test_a_step_moves_neural_time_and_produces_spikes(clean_session):
    session = clean_session
    session.set_image(np.full((90, 160, 3), 235, np.uint8))
    before = session.brain.sim_ms
    result = session.step(100.0, learning=False)
    assert result.brain_ms == pytest.approx(before + 100.0)
    assert result.total_spikes > 0
    assert result.synthetic_fixture is True


def test_teaching_a_channel_makes_its_cells_fire(clean_session):
    session = clean_session
    session.add_teaching_pulse("teach.pam11")
    result = session.step(100.0, learning=True, pulse_ms=50.0)
    reward = session.cells("teach.pam11")
    assert int(result.counts[reward].sum()) > 0
    assert result.memory["teaching_pulse_ms"] == pytest.approx(50.0)


def test_freezing_leaves_every_efficacy_untouched(clean_session):
    session = clean_session
    edges = session.brain.circuit["edges"]
    session.brain.weights_frozen = True
    before = session.brain.weight[edges].copy()
    session.set_image(np.full((90, 160, 3), 235, np.uint8))
    session.add_teaching_pulse("teach.pam11")
    session.step(100.0, learning=True)
    assert np.array_equal(before, session.brain.weight[edges])
    session.brain.weights_frozen = False


def test_a_checkpoint_round_trips(clean_session, tmp_path):
    session = clean_session
    session.set_image(np.full((90, 160, 3), 235, np.uint8))
    session.add_teaching_pulse("teach.pam11")
    session.step(100.0, learning=True)
    edges = session.brain.circuit["edges"]
    saved = session.brain.weight[edges].copy()
    record = session.save(tmp_path / "model.npz", label="test", notes="round trip")
    assert record["sha256"] and record["synthetic_fixture"] is True
    session.reset(keep_memory=False)
    assert not np.array_equal(saved, session.brain.weight[edges])
    session.restore(tmp_path / "model.npz")
    assert np.array_equal(saved, session.brain.weight[edges])


def test_a_checkpoint_from_a_different_configuration_is_refused(session, fixture_root, tmp_path):
    from flylab.neural.visual import VisualMemoryBrain

    other = VisualMemoryBrain(path=fixture_root / "graph.npz",
                              compartments="mushroom_body_full")
    other.checkpoint(tmp_path / "other.npz")
    with pytest.raises(ValueError, match="provenance"):
        session.brain.restore(tmp_path / "other.npz")


def test_readouts_are_means_not_totals(clean_session):
    session = clean_session
    session.step(100.0, learning=False)
    channel = session.read_channel("motor.descending")
    cells = len(session.cells("motor.descending"))
    assert channel["cells"] == cells
    assert channel["hz"] == pytest.approx(channel["spikes"] / cells / 0.1, rel=1e-6)


def test_a_reading_outside_its_range_is_clamped_not_rejected(clean_session):
    session = clean_session
    stimulus = session.add_stimulus("taste.sugar", 900.0, low=0.0, high=10.0)
    assert np.isfinite(stimulus.current).all()
    assert stimulus.current.max() <= atlas_default("taste.sugar") + 1e-3


def atlas_default(key):
    from flylab.neural.atlas import BY_KEY

    return BY_KEY[key].default_current


def test_driving_an_absent_channel_fails_loudly(clean_session):
    session = clean_session
    absent = [c for c in session.atlas.resolved.values()
              if not c.present and c.channel.role == "input"]
    if not absent:
        pytest.skip("this fixture happens to contain every input channel")
    with pytest.raises(ValueError, match="matches no cell"):
        session.add_stimulus(absent[0].channel.key, 1.0)
