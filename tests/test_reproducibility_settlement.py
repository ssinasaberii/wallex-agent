"""Acceptance tests 2 (byte-identical trades.csv for identical config + snapshot) and 8 (zero coin balance at
the end of every episode). The fee gate is disabled here only so that the window produces trades."""

import io

import pandas as pd
import pytest

from wallex_agent.backtest.runner import build_universe, run_period
from wallex_agent.backtest.suite import episodes, with_overrides
from wallex_agent.data.market_view import HistoricalMarketView
from wallex_agent.data.snapshot import SNAP_DIR, load_snapshot

from .conftest import hourly_cfg

SNAP = "0a4e977b89b6b576"
pytestmark = pytest.mark.skipif(not (SNAP_DIR / SNAP).exists(), reason="snapshot not available")


@pytest.fixture(scope="module")
def setup():
    cfg = with_overrides(hourly_cfg(), **{"fee_gate.enabled": False})
    candles, meta_path, _ = load_snapshot(SNAP)
    uni = build_universe(cfg, meta_path, candles)
    end = max(int(d["close_time"].max()) for d in candles.values())
    return cfg, candles, uni, end


def _csv(trades) -> bytes:
    buf = io.StringIO()
    pd.DataFrame(trades).to_csv(buf, index=False, float_format="%.6f")
    return buf.getvalue().encode()


def test_same_input_same_trades_bytes(setup):
    cfg, candles, uni, end = setup
    s = end - 12 * 86400
    a = run_period(cfg, HistoricalMarketView(candles), uni, s, end)
    b = run_period(cfg, HistoricalMarketView(candles), uni, s, end)
    assert len(a.trades) > 0, "window must produce trades for the test to mean something"
    assert _csv(a.trades) == _csv(b.trades)
    assert a.final_equity == b.final_equity


def test_no_coins_left_after_episodes(setup):
    cfg, candles, uni, end = setup
    view = HistoricalMarketView(candles)
    n_trades = 0
    for _, s, e in episodes(end - 15 * 86400, end, 72, [0, 12])[:4]:
        r = run_period(cfg, view, uni, s, e)
        n_trades += len(r.trades)
        assert r.leftover_qty == {}, f"coins left at episode end: {r.leftover_qty}"
        assert r.final_equity == pytest.approx(r.equity[-1][1])
    assert n_trades > 0
