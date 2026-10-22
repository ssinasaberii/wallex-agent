"""Staged selling (scale-out) in the backtest broker, and the pyramiding guard."""

import pandas as pd
import pytest

from wallex_agent.execution.backtest_sim import BacktestSimBroker
from wallex_agent.risk.portfolio import Order, Portfolio

from .conftest import hourly_cfg

CFG = hourly_cfg()
T0 = 1_792_670_400


def bar(t, o, h, lo, c):
    return pd.Series({"open_time": t, "close_time": t + 3600, "open": o, "high": h, "low": lo, "close": c})


def open_position(scale_out):
    pf = Portfolio(5_000_000, CFG.risk, T0, T0 + 72 * 3600)
    plan = {"setup": "T2_breakout", "stop_dist": 0.02, "target_dist": None, "cost_rt": 0.008, "g_be": 0.006,
            "H": 24, "gray_zone": False, "candidate_id": "C-1", "scale_out": scale_out}
    r, _ = pf.try_reserve("AAA", "AAATMN", 600_000, 10_000, 5_000_000, plan)
    b = BacktestSimBroker(pf, 0.003, lambda s: 0.0)
    o = Order("B-1", "AAATMN", "AAA", "BUY", "MARKET", 4_000.0, T0, "ENTRY", "C-1")
    r.order = o
    b.submit(o)
    b.on_bar("AAATMN", bar(T0, 100.0, 100.5, 99.5, 100.2))  # entry at 100, stop 98, R = 2
    return pf, b


def test_partial_sale_at_1R_then_breakeven_stop():
    pf, b = open_position([[1.0, 0.5]])
    b.on_bar("AAATMN", bar(T0 + 3600, 100.5, 102.5, 100.4, 102.0))  # reaches 102 = +1R
    pos = pf.positions["AAA"]
    assert pos.qty == pytest.approx(2_000.0)
    assert pos.stop == pytest.approx(100 * 1.006)
    assert [t["exit_type"] for t in pf.trades] == ["SCALE_OUT"]
    b.on_bar("AAATMN", bar(T0 + 7200, 101.5, 101.6, 100.0, 100.3))  # back to break-even: rest is stopped out
    assert "AAA" not in pf.positions
    assert sum(t["qty"] for t in pf.trades) == pytest.approx(4_000.0)


def test_stop_comes_before_partial_in_same_bar():
    pf, b = open_position([[1.0, 0.5]])
    b.on_bar("AAATMN", bar(T0 + 3600, 100.0, 103.0, 97.5, 101.0))  # touches both 98 and 102
    assert "AAA" not in pf.positions
    assert [t["exit_type"] for t in pf.trades] == ["STOP"]


def test_fractions_cannot_exceed_whole_position():
    from pydantic import ValidationError

    from wallex_agent.backtest.suite import with_overrides

    with pytest.raises(ValidationError):
        with_overrides(CFG, **{"exits.scale_out": [[1.0, 0.7], [2.0, 0.5]]})


def test_shadow_refuses_pyramiding():
    from wallex_agent.backtest.suite import with_overrides
    from wallex_agent.shadow.runner import ShadowRunner

    with pytest.raises(ValueError, match="Article 4"):
        ShadowRunner(with_overrides(CFG, **{"exits.pyramid_enabled": True}), session_id="never")
