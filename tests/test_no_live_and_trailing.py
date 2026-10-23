"""Acceptance tests 9 (no path reaches LiveBroker) and 10 (the stop never moves down)."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wallex_agent.config import load_config
from wallex_agent.execution import live
from wallex_agent.positions.manager import update_and_check
from wallex_agent.risk.portfolio import Position
from wallex_agent.strategy.regime import UP

CFG = load_config(apply_env=False)


def test_live_orders_cannot_be_sent_without_user_approval(monkeypatch):
    from wallex_agent import secrets
    from wallex_agent.data import wallex_orders

    monkeypatch.setattr(secrets, "get", lambda k, d=None: {"WALLEX_API_KEY": "1|x", "WALLEX_SUB_ACCOUNT_CLIENT_ID": "s"}.get(k, d))
    with pytest.raises(wallex_orders.LiveNotApproved):
        wallex_orders.WallexOrders(dry_run=False)


def test_backtest_never_touches_live_broker(monkeypatch):
    calls = []
    monkeypatch.setattr(live.LiveBroker, "__init__", lambda *a, **k: calls.append(1))
    from wallex_agent.data.market_view import HistoricalMarketView

    from .conftest import make_ohlcv

    df = make_ohlcv(2600)
    data = {"AAATMN": df, CFG.data.btc_regime_symbol: make_ohlcv(2600, seed=3),
            CFG.data.btc_tmn_symbol: make_ohlcv(2600, seed=4)}
    from wallex_agent.backtest.runner import Universe, run_period
    from wallex_agent.data.market_meta import MarketMeta

    m = MarketMeta("AAATMN", "AAA", "TMN", True, (), 50000, 1e-5, 5, 2, True, 100, 100.1, 0.1, 1e10)
    uni = Universe({"AAATMN": m}, ["AAATMN"], {"AAATMN": 0.1}, {"AAATMN"})
    end = int(df["close_time"].iloc[-1])
    run_period(CFG, HistoricalMarketView(data), uni, end - 5 * 86400, end)
    assert calls == []


def _pos(setup):
    return Position(base="A", symbol="ATMN", setup=setup, qty=1.0, entry_price=100.0, entry_t=0, stop=98.0,
                    initial_stop=98.0, target=None, stop_dist=0.02, cost_rt=0.008, g_be=0.006, H=8,
                    gray_zone=False, candidate_id="C", entry_fee_tmn=0.3, highest_high=100.0)


@settings(max_examples=200, deadline=None)
@given(st.lists(st.tuples(st.floats(80, 140), st.floats(0.1, 6)), min_size=1, max_size=40),
       st.sampled_from(["T1_pullback", "T2_breakout", "T3_meanrev"]))
def test_stop_never_moves_down(path, setup):
    pos = _pos(setup)
    prev = pos.stop
    for k, (high, atr) in enumerate(path):
        update_and_check(pos, {"high": high}, {"atr": atr, "bb_mid": high}, UP, UP, k, CFG)
        assert pos.stop >= prev
        prev = pos.stop
