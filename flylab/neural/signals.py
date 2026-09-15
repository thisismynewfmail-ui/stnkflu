"""Turning values into neural drive, and spike counts back into values.

Every function here is a declared interface convention. None of them is a
measured neural code. They are written as small pure functions so the choices
are visible, testable and replaceable rather than buried in a controller.
"""

import math

import numpy as np


def saturating(value, half=0.02, peak=30.0):
    """Michaelis-Menten style drive: 0 -> 0, large -> peak.

    The same shape the photoreceptor adapter uses, so a scalar sensor reading
    and an image pixel reach the network through the same nonlinearity.
    """
    x = np.clip(np.asarray(value, dtype=np.float32), 0, None)
    return (peak * x / (half + x)).astype(np.float32)


def linear(value, peak=20.0):
    x = np.clip(np.asarray(value, dtype=np.float32), 0, 1)
    return (peak * x).astype(np.float32)


CURVES = {
    "saturating": saturating,
    "linear": linear,
}


def normalise(value, low, high):
    """Map a real-world reading onto 0-1, clamped, with a degenerate guard."""
    low, high = float(low), float(high)
    if not math.isfinite(low) or not math.isfinite(high) or high == low:
        raise ValueError("Input range needs two different finite bounds")
    x = (np.asarray(value, dtype=np.float64) - low) / (high - low)
    return np.clip(x, 0.0, 1.0)


def spread(values, cells, groups=None):
    """Distribute a vector of values across a channel's cells.

    ``groups`` is a list of index arrays (for example the subtypes of
    Johnston's organ). Value ``i`` drives group ``i``. Without groups the
    values are laid across the cells in index order, repeating or truncating
    as needed so a mismatch never silently drops a cell.
    """
    values = np.atleast_1d(np.asarray(values, dtype=np.float32))
    if groups:
        out = np.zeros(len(cells), dtype=np.float32)
        position = {int(c): k for k, c in enumerate(cells)}
        for i, group in enumerate(groups):
            level = float(values[i % len(values)])
            for c in group:
                if int(c) in position:
                    out[position[int(c)]] = level
        return out
    if len(values) == 1:
        return np.full(len(cells), float(values[0]), dtype=np.float32)
    index = (np.arange(len(cells)) * len(values)) // max(1, len(cells))
    return values[np.clip(index, 0, len(values) - 1)].astype(np.float32)


def rate(counts, cells, seconds):
    """Mean firing rate of a cell group, in spikes per second.

    Mean rather than sum, so a large population does not outweigh a small one
    purely because it has more cells.
    """
    if seconds <= 0:
        raise ValueError("A rate needs a positive window")
    if not len(cells):
        return 0.0
    return float(np.mean(np.asarray(counts)[cells]) / seconds)


def differential(counts, left, right, seconds):
    """Right minus left mean rate: the two-sided steering convention."""
    return rate(counts, right, seconds) - rate(counts, left, seconds)


def gate(counts, cells, minimum=1):
    """True when a group produced at least ``minimum`` spikes in the window."""
    if not len(cells):
        return False
    return int(np.asarray(counts)[cells].sum()) >= int(minimum)


def bump_angle(counts, cells, seconds):
    """Circular mean of activity over cells placed evenly around a circle.

    Used for the compass channels. The placement is by position within the
    supplied index array, which is a declared ordering convention: this code
    does not know each cell's anatomical wedge, and does not pretend to.
    """
    cells = np.asarray(cells)
    if not len(cells):
        return {"angle_degrees": 0.0, "strength": 0.0, "peak_hz": 0.0, "cells": 0}
    activity = np.asarray(counts)[cells].astype(np.float64) / seconds
    angles = 2 * np.pi * np.arange(len(cells)) / len(cells)
    total = activity.sum()
    if total <= 0:
        return {
            "angle_degrees": 0.0,
            "strength": 0.0,
            "peak_hz": 0.0,
            "cells": int(len(cells)),
        }
    x = float((activity * np.cos(angles)).sum() / total)
    y = float((activity * np.sin(angles)).sum() / total)
    return {
        "angle_degrees": float((math.degrees(math.atan2(y, x)) + 360) % 360),
        "strength": float(min(1.0, math.hypot(x, y))),
        "peak_hz": float(activity.max()),
        "cells": int(len(cells)),
        "convention": "Cells ordered by index around a circle; not anatomical"
        " wedge identity.",
    }


def winner(scores, margin=0.0):
    """Pick the highest-scoring label, or None when the lead is too small."""
    if not scores:
        return None, 0.0, 0.0
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, value = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else 0.0
    lead = value - runner_up
    return (best if lead >= margin else None), float(value), float(lead)
