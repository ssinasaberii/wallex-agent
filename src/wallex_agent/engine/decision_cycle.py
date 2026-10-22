"""The single decision cycle used by Backtest, Shadow and (future) Live (spec 09 §3).

Modes differ only in the MarketView, the Broker and the Judge. Gates marked "shadow/live only" in the
spec (order flow, news) run only when the view reports the capability; there is no `if mode == ...`.

Order of work at a closed 1h bar t:
  1. mark-to-market, kill switch, daily loss              (05 §12, §15)
  2. position management -> SELL intents (never via judge) (09 §7.11)
  3. settlement schedule                                   (05 §14)
  4. entry halts: kill, daily loss, cutoff, macro, BTC     (09 §7.2, §7.8)
  5. universe -> regime -> trigger -> fee gate -> rank      (09 §7.1-7.6)
  6. order flow (if available) -> judge -> risk -> BUY     (09 §7.7, §7.9, §7.10)
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from wallex_agent.decision_log import DecisionLog
from wallex_agent.engine.features import FeatureProvider
from wallex_agent.judge.base import safe_review
from wallex_agent.positions.manager import update_and_check
from wallex_agent.risk.portfolio import Order, Portfolio
from wallex_agent.risk.sizing import size_position
from wallex_agent.strategy import fee_horizon
from wallex_agent.strategy.candidate import Candidate, candidate_id
from wallex_agent.strategy.regime import DOWN, HIGH_VOL, UNKNOWN, regime_at
from wallex_agent.strategy.triggers import T2, pick_setup, trigger_inputs
from wallex_agent.strategy.universe_rank import rank, ret_24h, select_universe

H1 = 3600


@dataclass
class CycleContext:
    cfg: Any
    view: Any
    features: FeatureProvider
    portfolio: Portfolio
    broker: Any
    judge: Any
    fee: Any                                  # FeeSnapshot
    metas: dict[str, Any]
    static_symbols: list[str]
    spread_est: dict[str, float]              # percent
    slip_of: Callable[[str], float]           # fraction per side
    end_t: int
    log: DecisionLog
    config_hash: str
    macro_events: list[dict] = field(default_factory=list)
    order_flow: Callable[[str, int], dict] | None = None
    news_gate: Callable[[str, int], dict] | None = None
    context_extra: Callable[[Any], dict] | None = None
    spread_cost_in_gate: bool = True          # backtest prices are mid/close; shadow uses executable prices
    funnel: dict[str, int] = field(default_factory=dict)
    wall_clock: Callable[[], float] = time.time


def _f(d: dict, k: str, by: int = 1) -> None:
    d[k] = d.get(k, 0) + by


def macro_state(t: int, events: list[dict], m) -> str:
    state = "normal"
    for e in events:
        et = int(e["t"])
        if et - m.block_before_min * 60 <= t <= et + m.block_after_min * 60:
            return "blocked"
        if et - m.caution_before_min * 60 <= t < et:
            state = "caution"
    return state


def _to_tmn(feat_row, signal_bar, tmn_bar) -> dict:
    """Signal-price ATR and Bollinger mid converted to TMN by the current price ratio (exits trade in TMN)."""
    k = float(tmn_bar["close"]) / float(signal_bar["close"])
    return {"atr": float(feat_row["atr"]) * k, "bb_mid": float(feat_row["bb_mid"]) * k}


def _r(x) -> float | None:
    x = float(x)
    return None if math.isnan(x) else round(x, 8)


def run_cycle(t: int, ctx: CycleContext) -> dict[str, Any]:
    cfg, pf, view, fn = ctx.cfg, ctx.portfolio, ctx.view, ctx.funnel
    st = cfg.challenge.settlement
    period4 = cfg.data.regime_hours * H1
    # Stops, kill switch and settlement are checked every hour; position updates and new entries happen
    # only when a trigger-timeframe bar closes.
    on_trigger = t % (cfg.data.trigger_hours * H1) == 0
    summary: dict[str, Any] = {"candidates": 0, "buys": 0, "sells": 0, "halt": None}

    # ---- 1. marks and account risk ------------------------------------------------------------
    marks = {}
    for p in pf.positions.values():
        b = view.bars(p.symbol, 1)
        if len(b):
            marks[p.symbol] = float(b["close"].iloc[-1])
    eq = pf.mark(t, marks)
    eq_base = pf.equity_base(marks)
    summary.update(equity=eq, open_positions=len(pf.positions), slots=pf.slots_used())

    btc_bars4, _, btc_reg = ctx.features.regime(view, cfg.data.btc_regime_symbol)
    btc_regime, _ = regime_at(btc_reg, t, period4) if len(btc_reg) else (UNKNOWN, None)
    summary["btc_regime"] = btc_regime

    def sell(pos, reason: str) -> None:
        if pos.exit_order is not None:          # one exit order per position, also while UNKNOWN
            return
        o = Order(f"S-{pos.candidate_id}-{t}", pos.symbol, pos.base, "SELL", "MARKET", pos.qty, t, reason,
                  pos.candidate_id)
        pos.exit_order = o
        ctx.broker.submit(o)
        summary["sells"] += 1
        ctx.log.exit(t, None, reason, pos.symbol, {"order": {"client_id": o.client_id, "side": "SELL",
                                                             "type": o.type, "qty": o.qty, "status": o.status}})

    # ---- 2/3. exits: kill switch, settlement, per-position rules -----------------------------------
    if pf.killed:
        for pos in list(pf.positions.values()):
            sell(pos, "KILL_SWITCH")
    elif t >= ctx.end_t - st.start_selling_min * 60:
        for pos in list(pf.positions.values()):
            sell(pos, "SETTLEMENT")
    elif on_trigger:
        for pos in list(pf.positions.values()):
            if pos.exit_order is not None:
                continue
            bars, feat = ctx.features.trigger(view, pos.symbol)
            tb = ctx.features.tmn_bars(view, pos.symbol)
            if len(bars) == 0 or len(tb) == 0 or int(bars["close_time"].iloc[-1]) != t \
                    or int(tb["close_time"].iloc[-1]) != t:
                continue  # no fresh bar: keep the standing stop, decide nothing new
            _, _, reg = ctx.features.regime(view, pos.symbol)
            sym_regime, _ = regime_at(reg, t, period4)
            why = update_and_check(pos, tb.iloc[-1], _to_tmn(feat.iloc[-1], bars.iloc[-1], tb.iloc[-1]),
                                   sym_regime, btc_regime, t, cfg)
            if why:
                sell(pos, why)

    # ---- 4. entry halts ------------------------------------------------------------------------
    if not on_trigger:
        return summary
    halt = None
    if pf.killed:
        halt = "KILL_SWITCH"
    elif pf.daily_loss_breached(eq, eq_base):
        halt = "DAILY_LOSS"
    elif t >= ctx.end_t - st.stop_entries_min * 60:
        halt = "SETTLEMENT_CUTOFF"
    elif macro_state(t, ctx.macro_events, cfg.macro) == "blocked":
        halt = "MACRO"
    elif cfg.btc_filter.enabled and btc_regime in (DOWN, UNKNOWN):
        halt = f"BTC_{btc_regime}"
    elif ctx.news_gate is not None:  # shadow/live with news: market-wide checks first (09 §7.8.4, Article 6)
        market = ctx.news_gate("__MARKET__", t)
        if market.get("event_shock"):
            halt = "EVENT_SHOCK"
            summary["news"] = market["blocking_items"][:3]
        elif any(b.get("reason") == "NEWS_UNAVAILABLE" for b in market.get("blocking_items", [])):
            halt = "NEWS_UNAVAILABLE"
    if halt:
        summary["halt"] = halt
        _f(fn, f"halt_{halt}")
        ctx.log.cycle(t, summary)
        return summary
    size_mult_btc = cfg.btc_filter.high_vol_size_mult if (cfg.btc_filter.enabled and btc_regime == HIGH_VOL) else 1.0

    # ---- 5. candidates -------------------------------------------------------------------------
    universe = select_universe(view, t, ctx.static_symbols, ctx.spread_est, cfg, fn)
    summary["universe"] = len(universe)
    btc_tmn = view.bars(cfg.data.btc_tmn_symbol, 1)
    btc_ret = ret_24h(btc_tmn, t) if len(btc_tmn) else None
    entry_cutoff = ctx.end_t - st.stop_entries_min * 60
    passed: list[Candidate] = []
    for sym in universe:
        meta = ctx.metas[sym]
        if pf.holds(meta.base):
            _f(fn, "skip_held")
            continue
        bars4, feat4, reg = ctx.features.regime(view, sym)
        sym_regime, ri = regime_at(reg, t, period4)
        if ri is None or len(bars4) < cfg.data.min_regime_bars:
            _f(fn, "regime_warmup")
            continue
        bars, feat = ctx.features.trigger(view, sym)
        tb = ctx.features.tmn_bars(view, sym)
        if len(bars) < cfg.data.min_trigger_bars or int(bars["close_time"].iloc[-1]) != t \
                or int(tb["close_time"].iloc[-1]) != t:
            _f(fn, "stale_1h")
            continue
        tmn_ratio = float(tb["close"].iloc[-1]) / float(bars["close"].iloc[-1])  # signal units -> TMN
        i = len(bars) - 1
        setup, gray = pick_setup(sym_regime, bars, feat, i, cfg.setups)
        if setup is None:
            _f(fn, f"no_trigger_{sym_regime}")
            continue
        _f(fn, "triggered")
        r4 = feat4.iloc[-1]
        c = Candidate(
            candidate_id=candidate_id(sym, t, setup, ctx.config_hash), ts=t, symbol=sym, base=meta.base,
            setup=setup, gray_zone=gray, symbol_regime=sym_regime, btc_regime=btc_regime,
            price=float(tb["close"].iloc[-1]), atr_pct_1h=float(feat["atr_pct"].iloc[i]),
            regime_inputs={k: _r(r4[k]) for k in ("ema20", "ema50", "slope", "adx", "pdi", "mdi", "atr_pct",
                                                  "atr_pct_rank")},
            trigger_inputs=trigger_inputs(bars, feat, i))
        c.fee = {"fee_snapshot_id": ctx.fee.snapshot_id, "f_buy": ctx.fee.taker, "f_sell": ctx.fee.taker,
                 "assumed": ctx.fee.assumed}
        slip = ctx.slip_of(sym)
        spread_cost = (ctx.spread_est.get(sym, 0.0) / 100) if ctx.spread_cost_in_gate else 0.0
        c.trigger_inputs["signal_price"] = cfg.data.signal_price
        c.trigger_inputs["close_tmn"] = c.price
        g = fee_horizon.evaluate(setup, c.price, c.atr_pct_1h, float(feat["bb_mid"].iloc[i]) * tmn_ratio, t,
                                 entry_cutoff, ctx.fee.g_break_even, slip, spread_cost, cfg)
        c.gate = g.as_log()
        c.stop_dist, c.target_dist = g.stop_dist, g.target_dist
        if not g.ok:
            _f(fn, f"gate_{g.reason}")
            ctx.log.candidate(c.reject(f"FEE_GATE:{g.reason}"), {"macro_state": "normal"})
            continue
        r = ret_24h(tb, t)  # vs BTCTMN: both in TMN, the dollar cancels out
        c.rank_inputs = {"rs_vs_btc": None if r is None or btc_ret is None else r - btc_ret,
                         "adx_4h": _r(r4["adx"]), "relvol_1h": _r(feat["relvol"].iloc[i]), "gate": g}
        passed.append(c)

    ranked = rank(passed, cfg)
    for k, c in enumerate(ranked):
        g = c.rank_inputs.pop("gate")
        c._gate_obj = g  # noqa: SLF001 (kept off the log)
        if k >= cfg.ranking.max_candidates_per_cycle:
            ctx.log.candidate(c.reject("RANK_CUTOFF"), {"macro_state": "normal"})
            _f(fn, "rank_cutoff")
    top = ranked[: cfg.ranking.max_candidates_per_cycle]
    summary["candidates"] = len(passed)

    # ---- 6. order flow, judge, risk ------------------------------------------------------------------
    for c in top:
        g = c._gate_obj
        extra = {"macro_state": macro_state(t, ctx.macro_events, cfg.macro)}
        if ctx.order_flow is not None:
            of = ctx.order_flow(c.symbol, g.H or 1)
            c.order_flow = of
            if of.get("status") == "REJECT":
                ctx.log.candidate(c.reject("ORDER_FLOW_CONFLICT"), extra)
                _f(fn, "order_flow_reject")
                continue
            if of.get("status") == "INSUFFICIENT" and cfg.order_flow.obi_mode == "confirm":
                ctx.log.candidate(c.reject("ORDER_FLOW_INSUFFICIENT", "DEFER"), extra)
                _f(fn, "order_flow_defer")
                continue
        if ctx.news_gate is not None:
            ng = ctx.news_gate(c.base, t)
            extra["news"] = ng
            if ng.get("blocking_items"):
                ctx.log.candidate(c.reject("NEWS_BLOCK"), extra)
                _f(fn, "news_block")
                continue
        created_wall = ctx.wall_clock()
        context = {"portfolio": {"open_positions": len(pf.positions), "slots_used": pf.slots_used(),
                                 "max_slots": cfg.risk.max_slots,
                                 "open_risk_pct": round(100 * pf.open_risk() / max(eq_base, 1), 3),
                                 "recent_trades": [round(tr["pnl_tmn"]) for tr in pf.trades[-5:]]},
                   "macro_state": extra["macro_state"]}
        if ctx.context_extra is not None:
            context.update(ctx.context_extra(c))
        c.judge = safe_review(ctx.judge, c, context, cfg.judge.timeout_s, cfg.judge.candidate_ttl_s, created_wall,
                              ctx.wall_clock)
        if "_call" in c.judge.get("extra", {}):
            ctx.log.judge_call(c.candidate_id, c.judge["extra"].pop("_call"))
        if not c.judge["approved"]:
            why = c.judge["error"] or c.judge["decision"]
            ctx.log.candidate(c.reject(f"JUDGE:{why}", "DEFER" if c.judge["decision"] == "DEFER" else "NO_TRADE"),
                              extra)
            _f(fn, f"judge_{why}")
            continue
        size_mult = min(size_mult_btc, cfg.setups.gray_zone.size_mult if c.gray_zone else 1.0)
        meta = ctx.metas[c.symbol]
        sz = size_position(c.price, g.stop_dist, g.cost_rt, eq_base, size_mult, meta, cfg.risk)
        c.risk = sz.as_log()
        if not sz.ok:
            ctx.log.candidate(c.reject(f"RISK:{sz.reason}"), extra)
            _f(fn, f"risk_{sz.reason}")
            continue
        # Final recheck (05 §8): fee snapshot unchanged, candidate not expired, still before cutoff.
        if c.fee["fee_snapshot_id"] != ctx.fee.snapshot_id:
            ctx.log.candidate(c.reject("FEE_SNAPSHOT_CHANGED"), extra)
            _f(fn, "recheck_fee")
            continue
        if ctx.wall_clock() - created_wall > cfg.judge.candidate_ttl_s:
            ctx.log.candidate(c.reject("CANDIDATE_EXPIRED"), extra)
            _f(fn, "recheck_expired")
            continue
        risk_tmn = sz.qty * c.price * (g.stop_dist + g.cost_rt)
        cash_need = sz.notional_tmn * (1 + ctx.fee.taker + ctx.slip_of(c.symbol)) * 1.002  # small price buffer
        plan = {"setup": c.setup, "stop_dist": g.stop_dist,
                "target_dist": None if c.setup == T2 else g.target_dist,  # T2 exits by trailing only
                "cost_rt": g.cost_rt, "g_be": ctx.fee.g_break_even, "H": g.H, "gray_zone": c.gray_zone,
                "candidate_id": c.candidate_id}
        res, why = pf.try_reserve(c.base, c.symbol, cash_need, risk_tmn, eq_base, plan)
        if res is None:
            c.risk["gate_failed"] = why
            ctx.log.candidate(c.reject(f"RISK:{why}"), extra)
            _f(fn, f"risk_{why}")
            continue
        o = Order(f"B-{c.candidate_id}", c.symbol, c.base, "BUY", "MARKET", sz.qty, t, "ENTRY", c.candidate_id)
        res.order = o
        ctx.broker.submit(o)
        c.final_decision, c.reason = "BUY", f"{c.setup} approved by {ctx.judge.provider}"
        c.order = {"client_id": o.client_id, "side": "BUY", "type": o.type, "qty": o.qty, "status": o.status}
        ctx.log.candidate(c, extra)
        summary["buys"] += 1
        _f(fn, "buy")
    ctx.log.cycle(t, summary)
    return summary
