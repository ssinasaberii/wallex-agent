"""Data-quality checks used by the universe filter (Q-30)."""

from __future__ import annotations

import pandas as pd

H1 = 3600


def missing_pct(bars_1h: pd.DataFrame, now: int, window_h: int) -> float:
    """Percent of the hourly slots in the trailing window (now - window_h, now] that have no closed bar.
    Slots before the symbol's first bar count as missing, so a short history also fails the check."""
    end = int(now) // H1 * H1  # last fully closed hour boundary
    start = end - window_h * H1
    if len(bars_1h) == 0:
        return 100.0
    ct = bars_1h["close_time"]
    present = int(((ct > start) & (ct <= end)).sum())
    return 100.0 * (window_h - min(present, window_h)) / window_h


def passes_quality(bars_1h: pd.DataFrame, now: int, window_h: int, max_missing_pct: float) -> bool:
    return missing_pct(bars_1h, now, window_h) <= max_missing_pct
