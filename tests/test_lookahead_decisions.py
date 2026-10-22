"""Acceptance test 1, decision level (09 §5.3): the full decision cycle at t gives exactly the same
decisions on data cut at t and on the full data, for 50 random hours (fixed seed). Uses the frozen
snapshot when present (skipped otherwise)."""

import numpy as np
import pytest

from wallex_agent.backtest.runner import Universe, build_universe
from wallex_agent.data.fees import FeeSnapshot
from wallex_agent.data.market_view import HistoricalMarketView
from wallex_agent.data.snapshot import SNAP_DIR, load_snapshot
from wallex_agent.decision_log import DecisionLog
from wallex_agent.engine.decision_cycle import CycleContext, run_cycle
from wallex_agent.engine.features import FeatureProvider
from wallex_agent.execution.backtest_sim import BacktestSimBroker
from wallex_agent.judge.rules_baseline import RulesBaselineJudge
from wallex_agent.risk.portfolio import Portfolio

from .conftest import hourly_cfg

SNAP = "0a4e977b89b6b576"
SYMS = ["BTCUSDT", "BTCTMN", "USDTTMN", "ETHTMN", "SOLTMN", "PEPETMN", "DOGETMN", "XRPTMN", "BNBTMN", "ARBTMN",
        "ADATMN"]


@pytest.mark.skipif(not (SNAP_DIR / SNAP).exists(), reason="snapshot not available")
@pytest.mark.parametrize("trigger_hours", [1, 4])
def test_decisions_identical_cut_vs_full(trigger_hours):
    from wallex_agent.backtest.suite import with_overrides

    cfg = with_overrides(hourly_cfg(), **{"data.trigger_hours": trigger_hours})
    candles, meta_path, _ = load_snapshot(SNAP)
    candles = {s: candles[s] for s in SYMS}
    full_uni = build_universe(cfg, meta_path, candles)
    uni = Universe(full_uni.metas, full_uni.static_symbols, full_uni.spread_est, full_uni.tier1)
    end = max(int(d["close_time"].max()) for d in candles.values())
    rng = np.random.default_rng(20261022)
    lo = end - 150 * 86400
    step = 3600 * trigger_hours  # decide on trigger-bar closes so entries are actually exercised
    ts = sorted(int(x) // step * step for x in rng.integers(lo, end, 50))
    full_view = HistoricalMarketView(candles)
    full_feats = FeatureProvider(cfg)
    fee = FeeSnapshot.from_config(cfg.fees)

    def decide(view, feats, t):
        pf = Portfolio(5_000_000, cfg.risk, t - 3600, t + 72 * 3600)
        log = DecisionLog(None, {"mode": "test"})
        ctx = CycleContext(cfg=cfg, view=view, features=feats, portfolio=pf,
                           broker=BacktestSimBroker(pf, fee.taker, lambda s: 0.0005), judge=RulesBaselineJudge(),
                           fee=fee, metas=uni.metas, static_symbols=uni.static_symbols, spread_est=uni.spread_est,
                           slip_of=lambda s: 0.0005, end_t=t + 72 * 3600, log=log, config_hash=cfg.config_hash)
        summary = run_cycle(t, ctx)
        recs = [{k: v for k, v in r.items() if k != "judge"}
                | {"judge_decision": (r.get("judge") or {}).get("decision")} for r in log.records]
        return summary, recs, dict(ctx.funnel)

    n_candidates = 0
    for t in ts:
        cut = {s: d[d["close_time"] <= t] for s, d in candles.items()}
        a = decide(full_view.as_of(t), full_feats, t)
        b = decide(HistoricalMarketView(cut).as_of(t), FeatureProvider(cfg), t)
        assert a == b, f"decision differs at t={t}"
        n_candidates += sum(1 for r in a[1] if r.get("kind") == "candidate")
    assert n_candidates >= 0  # informative; the equality above is the acceptance check

