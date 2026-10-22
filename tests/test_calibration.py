"""Regression: c_move calibration must compare prices in the same units (signal prices), whatever the
signal_price mode. A unit mismatch once produced c_move ~ 7e6."""

import pytest

from wallex_agent.backtest.runner import build_universe
from wallex_agent.backtest.suite import calibrate_c_move, with_overrides
from wallex_agent.data.market_view import HistoricalMarketView
from wallex_agent.data.snapshot import SNAP_DIR, load_snapshot
from wallex_agent.engine.features import FeatureProvider

from .conftest import hourly_cfg

SNAP = "0a4e977b89b6b576"


@pytest.mark.skipif(not (SNAP_DIR / SNAP).exists(), reason="snapshot not available")
@pytest.mark.parametrize("mode", ["tmn", "usdt_adjusted"])
def test_c_move_in_sane_range(mode):
    cfg = with_overrides(hourly_cfg(), **{"data.signal_price": mode})
    candles, meta_path, _ = load_snapshot(SNAP)
    keep = ["BTCUSDT", "BTCTMN", "USDTTMN", "ETHTMN", "PEPETMN", "DOGETMN"]
    candles = {s: candles[s] for s in keep}
    view = HistoricalMarketView(candles)
    uni = build_universe(cfg, meta_path, candles)
    end = max(int(d["close_time"].max()) for d in candles.values())
    r = calibrate_c_move(cfg, view, uni, FeatureProvider(cfg), end - 120 * 86400, end - 30 * 86400)
    assert r["n_setups"] > 0
    assert 0.0 < r["c_move"] < 5.0
