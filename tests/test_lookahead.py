"""Acceptance test 1 (09 §5.3), P1 part: features computed on data cut at t equal features computed on the
full data and read at t, for 50 random moments with a fixed seed. Also checks the closed-bar rule and that
4h aggregation of the visible prefix equals the sliced full aggregation. The decision-level version of this
test is added in P2.
"""

import numpy as np
import pandas as pd
import pytest

from wallex_agent.config import load_config
from wallex_agent.data.candles import aggregate
from wallex_agent.data.market_view import HistoricalMarketView
from wallex_agent.features.indicators import regime_features, trigger_features

from .conftest import H1, make_ohlcv

SEED = 20261022
N_MOMENTS = 50


@pytest.fixture(scope="module")
def setup():
    cfg = load_config(apply_env=False)
    df = make_ohlcv(3600, seed=11, gaps=(1500, 1501, 2700))  # include gaps: 4h buckets become incomplete
    view = HistoricalMarketView({"XTMN": df})
    rng = np.random.default_rng(SEED)
    lo = int(df["close_time"].iloc[1700])  # leave room for the 360-bar 4h percentile window
    hi = int(df["close_time"].iloc[-1])
    # Random instants, not only bar boundaries: half on boundaries, half mid-bar.
    ts = rng.integers(lo, hi, N_MOMENTS)
    ts[: N_MOMENTS // 2] = ts[: N_MOMENTS // 2] // H1 * H1
    return cfg, df, view, sorted(int(t) for t in ts)


def _frame_eq(a: pd.DataFrame, b: pd.DataFrame):
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True), check_exact=True)


def test_only_closed_bars_visible(setup):
    _, _, view, ts = setup
    for t in ts:
        for hours in (1, 4, 24):
            bars = view.as_of(t).bars("XTMN", hours)
            assert len(bars) > 0
            assert (bars["close_time"] <= t).all()


def test_aggregation_prefix_equivalence(setup):
    _, df, view, ts = setup
    for t in ts:
        prefix = df[df["close_time"] <= t]
        for hours in (4, 24):
            agg_prefix = aggregate(prefix, hours)
            agg_prefix = agg_prefix[agg_prefix["close_time"] <= t]
            _frame_eq(view.as_of(t).bars("XTMN", hours), agg_prefix)


def test_features_identical_cut_vs_full(setup):
    cfg, df, view, ts = setup
    p = cfg.indicators
    full_1h = trigger_features(view.full("XTMN", 1), p)
    full_4h = regime_features(view.full("XTMN", 4), p)
    checked = 0
    for t in ts:
        cut_view = HistoricalMarketView({"XTMN": df[df["close_time"] <= t]}).as_of(t)
        b1, b4 = cut_view.bars("XTMN", 1), cut_view.bars("XTMN", 4)
        cut_1h, cut_4h = trigger_features(b1, p), regime_features(b4, p)
        _frame_eq(cut_1h, full_1h.iloc[: len(b1)])
        _frame_eq(cut_4h, full_4h.iloc[: len(b4)])
        last = cut_4h.iloc[-1]
        checked += int(not np.isnan(last["atr_pct_rank"]))
    assert checked >= N_MOMENTS * 0.8, "most moments must have a fully warmed-up 4h percentile"


def test_future_bar_change_does_not_affect_past(setup):
    cfg, df, view, ts = setup
    t = ts[len(ts) // 2]
    before = trigger_features(view.as_of(t).bars("XTMN", 1), cfg.indicators)
    poisoned = df.copy()
    future = poisoned["open_time"] >= t
    poisoned.loc[future, ["high", "close"]] *= 10
    after = trigger_features(HistoricalMarketView({"XTMN": poisoned}).as_of(t).bars("XTMN", 1), cfg.indicators)
    _frame_eq(before, after)
