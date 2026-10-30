"""
tests/test_verdict.py   (new)   
Known-case checks for the verdict logic. Run from the repo root:  pytest
If you tune a threshold in src/verdict.py, these tell you which known cases moved.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from verdict import analyze_series, rescale  # noqa: E402


def noise(i, amp=2.0):
    """Deterministic wobble so tests never flake."""
    return amp * math.sin(i * 1.7)


def flat(n, level, amp=2.0):
    return [level + noise(i, amp) for i in range(n)]


def test_too_little_history():
    assert analyze_series(flat(20, 40))["label"] == "insufficient_data"


def test_too_little_volume():
    assert analyze_series([2.0] * 120)["label"] == "insufficient_data"


def test_steady():
    r = analyze_series(flat(120, 40))
    assert r["label"] == "steady"


def test_rising():
    series = flat(94, 30) + [30 + 2 * i for i in range(1, 27)]   # steady climb for 26 weeks
    r = analyze_series(series)
    assert r["label"] == "rising"
    assert r["detail"]["change_pct"] > 25


def test_fading():
    series = flat(94, 70) + [70 - 1.8 * i for i in range(1, 27)]
    r = analyze_series(series)
    assert r["label"] == "fading"


def test_peaking_just_after_climb_then_plateau():
    # climbs for 10 weeks, has only been at the top for 6 weeks -> "peaking"
    series = flat(100, 30, 1) + [30 + 5 * i for i in range(1, 11)] + [80 + noise(i, 1) for i in range(6)]
    assert analyze_series(series)["label"] == "peaking"


def test_long_plateau_is_steady_not_peaking():
    # the same climb, but it has sat at the top for 22 weeks -> that is now the new normal
    series = flat(94, 30, 1) + [30 + 5 * i for i in range(1, 11)] + [80 + noise(i, 1) for i in range(22)]
    assert analyze_series(series)["label"] == "steady"


def test_one_off_spike_is_not_called_rising():
    """The Feb-2026 'y2k fashion' shape: flat ~25, three hot weeks near 100, back to ~26."""
    series = flat(100, 25, 2) + [60, 100, 78] + flat(8, 26, 1)
    r = analyze_series(series, name="y2k fashion")
    assert r["label"] == "one_off_spike"
    assert r["label"] != "rising"


def test_spike_in_progress_is_flagged_low_confidence():
    series = flat(100, 25, 2) + [30, 90, 100]
    r = analyze_series(series)
    assert r["label"] == "spiking"
    assert r["confidence"] == "low"


def test_seasonal_cycle():
    series = [50 + 30 * math.sin(2 * math.pi * i / 52) for i in range(156)]
    r = analyze_series(series)
    assert r["label"] == "seasonal"
    assert r["detail"]["seasonal_corr"] >= 0.9


def test_confidence_high_needs_history_volume_and_calm():
    r = analyze_series(flat(160, 45, 1))
    assert r["label"] == "steady" and r["confidence"] == "high"


def test_rescale_peak_is_100():
    assert max(rescale([5, 10, 20])) == 100
    assert rescale([0, 0]) == [0, 0]