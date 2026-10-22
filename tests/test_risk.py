"""Acceptance tests 4 and 5 (09 §13): risk invariants (05 §17) and kill switch."""

import threading

import pandas as pd
import pytest

from wallex_agent.data.market_meta import MarketMeta
from wallex_agent.execution.backtest_sim import BacktestSimBroker
from wallex_agent.risk.portfolio import Order, Portfolio
from wallex_agent.risk.sizing import size_position

from .conftest import hourly_cfg

CFG = hourly_cfg()  # risk limits are identical in every config; hourly keeps every test hour a decision hour
T0 = 1_792_670_400


def meta(sym="AAATMN", min_notional=50_000):
    return MarketMeta(sym, sym[:-3], "TMN", True, (), min_notional, 1e-5, 5, 0, True, 100.0, 100.1, 0.1, 1e10)


def plan(cid="C-x"):
    return {"setup": "T1_pullback", "stop_dist": 0.02, "target_dist": 0.04, "cost_rt": 0.008, "g_be": 0.006,
            "H": 8, "gray_zone": False, "candidate_id": cid}


def pf():
    return Portfolio(5_000_000, CFG.risk, T0, T0 + 72 * 3600)


def test_never_more_than_10_slots_even_concurrently():
    p = pf()
    results = []

    def go(i):
        r, _ = p.try_reserve(f"B{i}", f"B{i}TMN", 100_000, 4_000, 5_000_000, plan(f"C{i}"))
        results.append(r is not None)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(40)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sum(results) == 10
    assert p.slots_used() == 10


def test_no_buy_below_floor():
    sz = size_position(100.0, 0.02, 0.008, 300_000, 1.0, meta(), CFG.risk)  # tiny equity -> tiny notional
    assert not sz.ok and sz.reason == "BELOW_MIN_BUY"
    m = meta(min_notional=500_000)  # exchange floor above the team floor
    sz = size_position(100.0, 0.02, 0.008, 5_000_000, 1.0, m, CFG.risk)
    assert not sz.ok and sz.reason == "BELOW_MIN_BUY"


def test_position_cap_and_open_risk_cap():
    sz = size_position(100.0, 0.001, 0.0, 5_000_000, 1.0, meta(), CFG.risk)  # tiny stop -> huge qty
    assert sz.notional_tmn <= 0.15 * 5_000_000 + 1e-6
    p = pf()
    cap = 0.01 * 5_000_000
    for i in range(4):
        r, why = p.try_reserve(f"B{i}", f"B{i}TMN", 300_000, 12_500, 5_000_000, plan(f"C{i}"))
        assert r is not None, why
    r, why = p.try_reserve("B9", "B9TMN", 300_000, 1, 5_000_000, plan("C9"))
    assert r is None and why == "OPEN_RISK_CAP"
    assert p.open_risk() <= cap + 1e-6


def test_no_buy_on_held_asset():
    p = pf()
    assert p.try_reserve("BTC", "BTCTMN", 200_000, 5_000, 5_000_000, plan())[0] is not None
    r, why = p.try_reserve("BTC", "BTCTMN", 200_000, 5_000, 5_000_000, plan("C2"))
    assert r is None and why == "ASSET_ALREADY_HELD"


def _filled_position(p, broker):
    r, _ = p.try_reserve("AAA", "AAATMN", 600_000, 10_000, 5_000_000, plan())
    o = Order("B-1", "AAATMN", "AAA", "BUY", "MARKET", 5_000.0, T0, "ENTRY", "C-x")
    r.order = o
    broker.submit(o)
    bar = pd.Series({"open_time": T0, "close_time": T0 + 3600, "open": 100.0, "high": 101.0, "low": 99.5,
                     "close": 100.5})
    broker.on_bar("AAATMN", bar)
    return p.positions["AAA"]


def test_sell_never_exceeds_free_balance():
    p = pf()
    broker = BacktestSimBroker(p, 0.003, lambda s: 0.0)
    pos = _filled_position(p, broker)
    o = Order("S-1", "AAATMN", "AAA", "SELL", "MARKET", pos.qty * 3, T0 + 3600, "TEST", "C-x")
    pos.exit_order = o
    broker.submit(o)
    broker.on_bar("AAATMN", pd.Series({"open_time": T0 + 3600, "close_time": T0 + 7200, "open": 100.0,
                                       "high": 100.2, "low": 99.9, "close": 100.0}))
    assert o.filled_qty == pytest.approx(5_000.0)
    assert "AAA" not in p.positions


def test_unknown_order_keeps_slot_and_blocks_duplicate():
    p = pf()
    r, _ = p.try_reserve("AAA", "AAATMN", 600_000, 10_000, 5_000_000, plan())
    r.order = Order("B-1", "AAATMN", "AAA", "BUY", "LIMIT", 1.0, T0, "ENTRY", "C-x", status="UNKNOWN")
    p.release("AAA")  # must not release while the order state is unknown
    assert p.slots_used() == 1
    assert p.try_reserve("AAA", "AAATMN", 600_000, 10_000, 5_000_000, plan("C2"))[0] is None
    r.order.status = "CANCELED"
    p.release("AAA")
    assert p.slots_used() == 0


def test_kill_switch_at_3pct_drawdown_stops_buys():
    p = pf()
    p.mark(T0, {})
    assert not p.killed
    p.cash -= 0.031 * 5_000_000  # synthetic 3.1% loss
    p.mark(T0 + 3600, {})
    assert p.killed
    r, why = p.try_reserve("NEW", "NEWTMN", 100_000, 1_000, 5_000_000, plan())
    assert r is None and why == "KILL_SWITCH"


@pytest.mark.parametrize("mode,next_t,rearmed", [("never", T0 + 86400, False), ("daily", T0 + 86400, True),
                                                 ("daily", T0 + 7200, False), ("monthly", T0 + 86400, False)])
def test_kill_switch_reset_modes(mode, next_t, rearmed):
    from wallex_agent.backtest.suite import with_overrides

    cfg = with_overrides(CFG, **{"risk.kill_switch_reset": mode})
    p = Portfolio(5_000_000, cfg.risk, T0, T0 + 40 * 86400)
    p.mark(T0, {})
    p.cash -= 0.031 * 5_000_000
    p.mark(T0 + 3600, {})
    assert p.killed
    p.mark(next_t, {})
    assert p.killed is (not rearmed)
    if rearmed:  # the drawdown peak restarts from the current equity
        assert p.peak_equity == pytest.approx(p.equity({}))


def test_kill_switch_sells_everything_in_cycle(monkeypatch):
    from wallex_agent.engine import decision_cycle as dc

    p = pf()
    broker = BacktestSimBroker(p, 0.003, lambda s: 0.0)
    _filled_position(p, broker)
    p.killed = True
    sold = []

    class View:
        def bars(self, s, h=1):
            return pd.DataFrame({"close_time": [T0 + 3600], "close": [100.0], "high": [100], "low": [100],
                                 "open": [100]})

    class Feat:
        def regime(self, v, s):
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    class Log:
        def exit(self, *a, **k):
            sold.append(a)

        def cycle(self, *a, **k):
            pass

    ctx = dc.CycleContext(cfg=CFG, view=View(), features=Feat(), portfolio=p, broker=broker, judge=None, fee=None,
                          metas={}, static_symbols=[], spread_est={}, slip_of=lambda s: 0, end_t=T0 + 72 * 3600,
                          log=Log(), config_hash="x")
    s = dc.run_cycle(T0 + 3600, ctx)
    assert s["halt"] == "KILL_SWITCH" and s["sells"] == 1 and s["buys"] == 0
    assert p.positions["AAA"].exit_order is not None
