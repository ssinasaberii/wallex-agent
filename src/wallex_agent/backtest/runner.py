"""Backtest runner: drives the shared decision cycle over historical hours (spec 09 §9).

For each hour t (a 1h close): first the broker processes the bar that just closed (queued orders fill at
its open, standing stops/targets intrabar), then the decision cycle runs on the view as of t.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from wallex_agent.data import market_meta
from wallex_agent.data.allowed import load_rules
from wallex_agent.data.fees import FeeSnapshot
from wallex_agent.data.market_view import HistoricalMarketView
from wallex_agent.decision_log import DecisionLog
from wallex_agent.engine.decision_cycle import CycleContext, run_cycle
from wallex_agent.engine.features import FeatureProvider
from wallex_agent.execution.backtest_sim import BacktestSimBroker
from wallex_agent.judge.rules_baseline import RulesBaselineJudge
from wallex_agent.risk.portfolio import Portfolio

H1 = 3600


@dataclass
class Universe:
    metas: dict[str, Any]
    static_symbols: list[str]
    spread_est: dict[str, float]
    tier1: set[str]


def build_universe(cfg, metas_path: Path, candles: dict[str, pd.DataFrame]) -> Universe:
    raw = json.loads(Path(metas_path).read_text(encoding="utf-8"))
    metas = {s: market_meta.MarketMeta(**(d | {"categories": tuple(d["categories"])}))
             for s, d in raw["markets"].items()}
    rules = load_rules()
    elig = market_meta.eligible_tmn_spot(metas, cfg.universe, cfg.data.quote_asset)
    static = [m.symbol for m in elig if rules.is_allowed(m.symbol) and m.symbol in candles]
    spread = {s: metas[s].spread_pct for s in static if metas[s].spread_pct is not None}
    tier1 = set(static[: cfg.slippage.tier1_top_n])
    return Universe(metas, static, spread, tier1)


@dataclass
class RunResult:
    trades: list[dict[str, Any]]
    equity: list[tuple[int, float]]
    funnel: dict[str, int]
    decisions: list[dict[str, Any]]
    final_equity: float
    fees_paid: float
    killed: bool
    kill_reason: str
    leftover_qty: dict[str, float] = field(default_factory=dict)


def run_period(cfg, base_view: HistoricalMarketView, uni: Universe, start_t: int, end_t: int, *,
               initial_tmn: float | None = None, judge=None, fee: FeeSnapshot | None = None,
               features: FeatureProvider | None = None, log_dir: Path | None = None,
               header: dict | None = None, slip_mult: float = 1.0, cfg_override=None) -> RunResult:
    cfg = cfg_override or cfg
    fee = fee or FeeSnapshot.from_config(cfg.fees)
    judge = judge or RulesBaselineJudge(cfg.judge.baseline_gray_policy)
    features = features or FeatureProvider(cfg)
    pf = Portfolio(initial_tmn or cfg.challenge.initial_equity_tmn, cfg.risk, start_t, end_t)

    def slip_of(sym: str) -> float:
        s = cfg.slippage
        return slip_mult * (s.tier1_per_side_pct if sym in uni.tier1 else s.tier2_per_side_pct) / 100

    broker = BacktestSimBroker(pf, fee.taker, slip_of)
    log = DecisionLog(log_dir, header or {"mode": "backtest", "variant": "A", "strategy_version": cfg.strategy_version})
    ctx = CycleContext(cfg=cfg, view=base_view, features=features, portfolio=pf, broker=broker, judge=judge, fee=fee,
                       metas=uni.metas, static_symbols=uni.static_symbols, spread_est=uni.spread_est,
                       slip_of=slip_of, end_t=end_t, log=log, config_hash=cfg.config_hash)
    equity: list[tuple[int, float]] = []
    t = (start_t // H1) * H1
    while t <= end_t:
        view = base_view.as_of(t)
        ctx.view = view
        active = {p.symbol for p in pf.positions.values()} | {o.symbol for o in broker.queue}
        for sym in sorted(active):
            b = view.bars(sym, 1)
            if len(b) and int(b["close_time"].iloc[-1]) == t:
                for fill in broker.on_bar(sym, b.iloc[-1]):
                    if fill["side"] == "SELL" and fill.get("trade"):
                        tr = fill["trade"]
                        log.exit(fill["t"], tr, tr["exit_type"], sym)
        if t < end_t:
            run_cycle(t, ctx)
        marks = {}
        for p in pf.positions.values():
            b = view.bars(p.symbol, 1)
            if len(b):
                marks[p.symbol] = float(b["close"].iloc[-1])
        equity.append((t, pf.equity(marks)))
        t += H1
    # End of period: anything left is sold at the last close (09 §8.1 settlement).
    view = base_view.as_of(end_t)
    closes = {p.symbol: float(view.bars(p.symbol, 1)["close"].iloc[-1]) for p in pf.positions.values()}
    for fill in broker.liquidate_all(closes, end_t, "FINAL_SETTLEMENT"):
        log.exit(fill["t"], fill["trade"], "FINAL_SETTLEMENT", fill["symbol"])
    leftover = {p.symbol: p.qty for p in pf.positions.values()}
    final = pf.equity({})
    if equity:
        equity[-1] = (equity[-1][0], final)
    log.close()
    return RunResult(pf.trades, equity, dict(ctx.funnel), log.records, final, pf.fees_paid, pf.killed,
                     pf.kill_reason, leftover)
