"""Shadow test on live Wallex data with simulated orders (spec 09 §10). No order ever leaves this process.

Loops (single thread, wall clock UTC):
  every 10 s   GET /v2/depth/all  -> latest books, OBI history, depth recording (20 levels)
  every 15 s   GET /v1/trades for the active universe -> trade recording, TFI (log only)
  every 15 s   protective stop / target check on every variant using the best bid
  hh:00:20     update 1h candles, run the shared decision cycle once per variant, write state + report
Variants share the same candidates (same cycle inputs) and keep separate virtual accounts:
  A = RulesBaselineJudge, B = LayaJudge (local laya-serve). Variant A is also B's counterfactual ledger.
  XA / XB = information-only experiment: the same pair with the fee gate disabled, so the judge actually
  sees candidates (with real fees the default gate rejects nearly all of them). XB vs XA measures whether
  Laya filters losing entries. Not the trading configuration.
Websocket is unavailable (api-verification V6), so REST polling is used (Q-25).
"""

from __future__ import annotations

import json
import logging
import pickle
import sqlite3
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from wallex_agent import secrets
from wallex_agent.backtest.runner import build_universe
from wallex_agent.data import market_meta
from wallex_agent.data.candles import CandleStore
from wallex_agent.data.fees import FeeSnapshot
from wallex_agent.data.market_view import HistoricalMarketView
from wallex_agent.data.wallex_rest import WallexRest
from wallex_agent.decision_log import DecisionLog
from wallex_agent.engine.decision_cycle import CycleContext, run_cycle
from wallex_agent.engine.features import FeatureProvider
from wallex_agent.features.orderflow import OrderFlow
from wallex_agent.judge.laya import LayaJudge
from wallex_agent.judge.rules_baseline import RulesBaselineJudge
from wallex_agent.risk.portfolio import Order, Portfolio

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
H1 = 3600


class LiveView(HistoricalMarketView):
    has_orderbook = True
    has_trades = True

    def as_of(self, t: int) -> LiveView:
        v = LiveView({}, now=t, _shared=self._shared)
        return v


def walk_book(levels: list[dict], qty: float) -> float | None:
    """Average fill price for a taker order of `qty` against `levels` (best first)."""
    left, cost = qty, 0.0
    for lv in levels:
        q = min(left, float(lv["quantity"]))
        cost += q * float(lv["price"])
        left -= q
        if left <= 1e-12:
            return cost / qty
    return None  # not enough depth


class ShadowSimBroker:
    def __init__(self, pf: Portfolio, fee: float, books: dict[str, dict], dlog: DecisionLog):
        self.pf, self.fee, self.books, self.log = pf, fee, books, dlog
        self.pending_sells: list[Order] = []

    def submit(self, order: Order) -> Order:
        order.status = "SUBMITTED"
        book = self.books.get(order.symbol)
        now = int(time.time())
        if order.side == "BUY":
            px = walk_book(book.get("ask", []), order.qty) if book else None
            if px is None:
                order.status = "REJECTED"
                self.pf.release(order.base)
                return order
            fee = order.qty * px * self.fee
            pos = self.pf.on_buy_fill(order.base, order.qty, px, fee, now)
            pos.highest_high = px
            order.status, order.filled_qty = "FILLED", order.qty
            order.fills.append({"t": now, "price": px, "qty": order.qty, "fee_tmn": fee})
            return order
        self._try_sell(order)
        return order

    def _try_sell(self, order: Order) -> None:
        pos = self.pf.positions.get(order.base)
        if pos is None:
            order.status = "CANCELED"
            return
        book = self.books.get(order.symbol)
        qty = min(order.qty, pos.qty)
        px = walk_book(book.get("bid", []), qty) if book else None
        if px is None:  # no book: keep the single exit order alive and retry (never duplicate it)
            order.status = "ACKNOWLEDGED"
            if order not in self.pending_sells:
                self.pending_sells.append(order)
            return
        fee = qty * px * self.fee
        now = int(time.time())
        tr = self.pf.on_sell_fill(order.base, qty, px, fee, now, order.reason)
        order.status, order.filled_qty = "FILLED", qty
        order.fills.append({"t": now, "price": px, "qty": qty, "fee_tmn": fee})
        if order in self.pending_sells:
            self.pending_sells.remove(order)
        self.log.exit(now, tr, order.reason, order.symbol)

    def cancel(self, order: Order) -> Order:
        order.status = "CANCELED"
        return order

    def poll(self, order: Order) -> Order:
        return order

    def retry_pending(self) -> None:
        for o in list(self.pending_sells):
            self._try_sell(o)

    def check_stops(self) -> None:
        for pos in list(self.pf.positions.values()):
            if pos.exit_order is not None:
                continue
            book = self.books.get(pos.symbol)
            if not book or not book.get("bid"):
                continue
            bid = float(book["bid"][0]["price"])
            if bid > pos.stop:
                for lvl in pos.scale_plan:  # staged selling: reduce only, never more than the free quantity
                    if lvl["done"] or bid < lvl["price"] or pos.qty <= 0:
                        continue
                    q = min(pos.qty, pos.qty0 * lvl["fraction"])
                    px = walk_book(book.get("bid", []), q)
                    if px is None:
                        break
                    lvl["done"] = True
                    now = int(time.time())
                    tr = self.pf.on_sell_fill(pos.base, q, px, q * px * self.fee, now, "SCALE_OUT")
                    self.log.exit(now, tr, "SCALE_OUT", pos.symbol)
                    if pos.base not in self.pf.positions:
                        break
                    pos.raise_stop(pos.entry_price * (1 + pos.g_be), "SCALE_OUT_BREAKEVEN", now)
                if pos.base not in self.pf.positions:
                    continue
            why = None
            if bid <= pos.stop:
                why = "STOP" if pos.stop < pos.entry_price else "TRAIL_STOP"
            elif pos.target is not None and bid >= pos.target:
                why = "TARGET"
            if why:
                o = Order(f"S-{pos.candidate_id}-{int(time.time())}", pos.symbol, pos.base, "SELL", "MARKET",
                          pos.qty, int(time.time()), why, pos.candidate_id)
                pos.exit_order = o
                self.submit(o)


@dataclass
class Variant:
    name: str
    judge: Any
    pf: Portfolio
    broker: ShadowSimBroker
    log: DecisionLog
    cfg: Any = None
    ctx: CycleContext | None = None


class ShadowRunner:
    def __init__(self, cfg, session_id: str | None = None, variants: tuple[str, ...] = ("A", "B")):
        if cfg.exits.pyramid_enabled:
            raise ValueError("pyramiding (adding to positions) is forbidden outside backtest experiments (Article 4)")
        self.cfg = cfg
        self.api = WallexRest()
        self.store = CandleStore()
        self.fee = FeeSnapshot.from_config(cfg.fees)
        self.end_t = int((cfg.shadow.virtual_end_utc or cfg.challenge.end_utc).timestamp())
        self.session = session_id or datetime.now(UTC).strftime("%Y%m%dT%H%MZ")
        self.dir = ROOT / "reports" / "shadow" / self.session
        self.dir.mkdir(parents=True, exist_ok=True)
        self.books: dict[str, dict] = {}
        self.of = OrderFlow(cfg.order_flow)
        self.features = FeatureProvider(cfg)
        self.metas, meta_path = market_meta.fetch_and_save(self.api)
        self.meta_path = meta_path
        self.symbols: list[str] = []
        self.rec_depth: list[dict] = []
        self.rec_trades: list[dict] = []
        self.seen_trades: set[tuple] = set()
        self.stats = {"api_errors": 0, "cycles": 0, "started": int(time.time()), "laya_errors": 0}
        self.db = sqlite3.connect(self.dir / "state.sqlite")
        self.db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v BLOB)")
        # News (09 §7.8): one shared store; only variants with news enabled use its gate and headlines.
        from wallex_agent.context.fng import FearGreed
        from wallex_agent.context.news import AssetMatcher, NewsStore, load_feeds
        from wallex_agent.context.news_interpreter import LayaNewsInterpreter

        self.news = NewsStore(cfg.news)
        self.news_feeds = load_feeds()
        self.news_matcher = AssetMatcher(self.metas, cfg.data.quote_asset)
        self.news_interp = LayaNewsInterpreter(secrets.get("LAYA_URL") or "http://127.0.0.1:8000",
                                               cfg.news.min_choice_prob, cfg.judge.timeout_s)
        self.fng = FearGreed(cfg.news.fng_url)
        self.variants: dict[str, Variant] = {}
        for name in variants:
            self.variants[name] = self._load_or_new(name)

    # ------------------------------------------------------------------ state
    def _load_or_new(self, name: str) -> Variant:
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (f"pf_{name}",)).fetchone()
        start = int(time.time())
        pf = pickle.loads(row[0]) if row else Portfolio(self.cfg.challenge.initial_equity_tmn, self.cfg.risk,
                                                         start, self.end_t)
        from wallex_agent.backtest.suite import source_hash

        dlog = DecisionLog(self.dir / name, {"mode": "shadow", "variant": name,
                                             "strategy_version": self.cfg.strategy_version,
                                             "config_hash": self.cfg.config_hash,
                                             "git_commit": source_hash()})
        from wallex_agent.backtest.suite import with_overrides

        # A = rules, B = Laya, both without news (comparison); C = Laya + news (the trading algorithm).
        vcfg = with_overrides(self.cfg, **{"news.enabled": name.endswith("C")})
        if name.startswith("X"):
            vcfg = with_overrides(vcfg, **{"fee_gate.enabled": False})
        if name.endswith("A"):
            judge = RulesBaselineJudge(self.cfg.judge.baseline_gray_policy)
        else:
            judge = LayaJudge(secrets.get("LAYA_URL") or "http://127.0.0.1:8000", self.cfg.judge,
                              self.cfg.judge.timeout_s)
        return Variant(name, judge, pf, ShadowSimBroker(pf, self.fee.taker, self.books, dlog), dlog, vcfg)

    def save_state(self) -> None:
        for v in self.variants.values():
            self.db.execute("REPLACE INTO kv VALUES (?, ?)", (f"pf_{v.name}", pickle.dumps(v.pf)))
        self.db.commit()

    # ------------------------------------------------------------------ data
    def poll_depth(self) -> None:
        try:
            d = self.api.get_json("/v2/depth/all")["result"]
        except Exception as e:
            self.stats["api_errors"] += 1
            log.warning("depth/all failed: %s", type(e).__name__)
            return
        now = time.time()
        watch = set(self.symbols) | {p.symbol for v in self.variants.values() for p in v.pf.positions.values()}
        for sym, book in d.items():
            if not isinstance(book, dict):
                continue
            book = {"ask": sorted(book.get("ask") or [], key=lambda x: float(x["price"])),
                    "bid": sorted(book.get("bid") or [], key=lambda x: -float(x["price"]))}
            self.books[sym] = book
            if sym in watch:
                self.of.on_book(sym, now, book)
                n = self.cfg.shadow.record_depth_levels

                def lv(side, book=book, n=n):
                    return json.dumps([[float(x["price"]), float(x["quantity"])] for x in book[side][:n]])

                self.rec_depth.append({"t": now, "symbol": sym, "bids": lv("bid"), "asks": lv("ask")})

    def poll_trades(self) -> None:
        now = time.time()
        for sym in self.symbols:
            try:
                tr = self.api.get_json("/v1/trades", {"symbol": sym})["result"]["latestTrades"]
            except Exception:
                self.stats["api_errors"] += 1
                continue
            new = []
            for x in tr:
                key = (sym, x["timestamp"], x["price"], x["quantity"], x["isBuyOrder"])
                if key in self.seen_trades:
                    continue
                self.seen_trades.add(key)
                ts = datetime.fromisoformat(x["timestamp"].replace("Z", "+00:00")).timestamp()
                new.append((ts, float(x["price"]), float(x["quantity"]), bool(x["isBuyOrder"])))
                book = self.books.get(sym) or {}
                self.rec_trades.append({"t_recv": now, "t": ts, "symbol": sym, "price": float(x["price"]),
                                        "qty": float(x["quantity"]), "isBuyOrder": bool(x["isBuyOrder"]),
                                        "best_bid": float(book["bid"][0]["price"]) if book.get("bid") else None,
                                        "best_ask": float(book["ask"][0]["price"]) if book.get("ask") else None})
            self.of.on_trades(sym, new)
        if len(self.seen_trades) > 200_000:
            self.seen_trades.clear()

    def flush_recordings(self) -> None:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        d = ROOT / "data" / "recordings" / day
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%H%M%S")
        if self.rec_depth:
            pd.DataFrame(self.rec_depth).to_parquet(d / f"depth_{stamp}.parquet", index=False)
            self.rec_depth = []
        if self.rec_trades:
            pd.DataFrame(self.rec_trades).to_parquet(d / f"trades_{stamp}.parquet", index=False)
            self.rec_trades = []

    def update_candles(self) -> dict[str, pd.DataFrame]:
        uni = build_universe(self.cfg, self.meta_path, {s: None for s in self.metas})
        refs = [self.cfg.data.btc_regime_symbol, self.cfg.data.btc_tmn_symbol, self.cfg.data.usdt_tmn_symbol]
        syms = uni.static_symbols[: self.cfg.data.fetch_top_n] + refs
        out = {}
        for s in syms:
            try:
                # the daily regime needs a year of 1h history; the cache keeps it, so this fetches only new bars
                out[s] = self.store.update(self.api, s, self.cfg.data.history_days, self.cfg.data.udf_chunk_bars)
            except Exception:
                self.stats["api_errors"] += 1
                cached = self.store.load(s)
                if len(cached):
                    out[s] = cached
        return out

    # ------------------------------------------------------------------ cycle
    def live_spreads(self) -> dict[str, float]:
        out = {}
        for s, b in self.books.items():
            if b.get("bid") and b.get("ask"):
                bid, ask = float(b["bid"][0]["price"]), float(b["ask"][0]["price"])
                if bid > 0 and ask >= bid:
                    out[s] = (ask - bid) / ((ask + bid) / 2) * 100
        return out

    def cycle(self, t: int) -> None:
        candles = self.update_candles()
        uni = build_universe(self.cfg, self.meta_path, candles)
        self.symbols = uni.static_symbols[: self.cfg.universe.top_n_by_volume + 10]
        view = LiveView(candles).as_of(t)
        spreads = self.live_spreads()
        tier1 = uni.tier1

        def slip_of(sym: str) -> float:
            s = self.cfg.slippage
            return (s.tier1_per_side_pct if sym in tier1 else s.tier2_per_side_pct) / 100

        for v in self.variants.values():
            ctx = CycleContext(cfg=v.cfg, view=view, features=self.features, portfolio=v.pf, broker=v.broker,
                               judge=v.judge, fee=self.fee, metas=uni.metas, static_symbols=uni.static_symbols,
                               spread_est=spreads, slip_of=slip_of, end_t=self.end_t, log=v.log,
                               config_hash=v.cfg.config_hash,
                               order_flow=lambda sym, H: self.of.evaluate(sym, H, time.time()),
                               news_gate=(lambda base, _t: self.news.gate(base, time.time()))
                               if v.cfg.news.enabled else None,
                               context_extra=self._context_fn(v) if v.judge.provider == "laya" else None,
                               spread_cost_in_gate=False)
            if v.ctx is not None:
                ctx.funnel = v.ctx.funnel
            v.ctx = ctx
            try:
                run_cycle(t, ctx)
            except Exception:
                log.exception("cycle failed for variant %s", v.name)
        self.stats["cycles"] += 1
        self.save_state()
        self.report()

    def _context_fn(self, v: Variant):
        """Context for the Laya judge: Fear & Greed always; related headlines only when news is on."""
        def fn(c) -> dict:
            f = self.fng.get()
            out: dict = {"fng": f"{f['value']} ({f['label']})" if f else "na"}
            if v.cfg.news.enabled:
                g = self.news.gate(c.base, time.time())
                out["news_headlines"] = [f"{x['title']} [{x['source']}, {x['direction']}, severity {x['severity']}]"
                                         for x in g["context_items"]]
            return out
        return fn

    def poll_news(self) -> None:
        new = self.news.poll(self.news_feeds, self.news_matcher)
        done = self.news.interpret_pending(self.news_interp)
        self.stats["news_new"] = self.stats.get("news_new", 0) + new
        self.stats["news_interpreted"] = self.stats.get("news_interpreted", 0) + done

    def finish(self) -> None:
        for v in self.variants.values():
            for pos in list(v.pf.positions.values()):
                if pos.exit_order is None:
                    o = Order(f"S-{pos.candidate_id}-final", pos.symbol, pos.base, "SELL", "MARKET", pos.qty,
                              int(time.time()), "FINAL_SETTLEMENT", pos.candidate_id)
                    pos.exit_order = o
                    v.broker.submit(o)
        self.save_state()
        self.report(final=True)

    # ------------------------------------------------------------------ report
    def report(self, final: bool = False) -> None:
        lines = [f"# Shadow session {self.session}", "",
                 f"- generated: {datetime.now(UTC).isoformat(timespec='seconds')}  final={final}",
                 f"- challenge end (UTC): {datetime.fromtimestamp(self.end_t, UTC).isoformat()}",
                 f"- uptime h: {(time.time() - self.stats['started']) / 3600:.2f}, cycles: {self.stats['cycles']}, "
                 f"API errors: {self.stats['api_errors']}", f"- fee snapshot: {self.fee.snapshot_id} "
                 f"(taker {self.fee.taker:.2%}), config {self.cfg.config_hash}", "",
                 "| variant | judge | equity TMN | return % | trades | win % | open | fees TMN | killed |",
                 "|---|---|---:|---:|---:|---:|---:|---:|---|"]
        rows = []
        for v in self.variants.values():
            marks = {p.symbol: float(self.books[p.symbol]["bid"][0]["price"])
                     for p in v.pf.positions.values() if self.books.get(p.symbol, {}).get("bid")}
            eq = v.pf.equity(marks)
            tr = v.pf.trades
            win = 100 * sum(1 for x in tr if x["pnl_tmn"] > 0) / len(tr) if tr else 0.0
            lines.append(f"| {v.name} | {v.judge.provider} | {eq:,.0f} | {100 * (eq / v.pf.initial - 1):.3f} | "
                         f"{len(tr)} | {win:.0f} | {len(v.pf.positions)} | {v.pf.fees_paid:,.0f} | {v.pf.killed} |")
            rows.append((v, eq))
            pd.DataFrame(tr).to_csv(self.dir / f"trades_{v.name}.csv", index=False)
            with (self.dir / f"equity_{v.name}.csv").open("a", encoding="utf-8") as fh:
                fh.write(f"{int(time.time())},{eq:.2f}\n")
        for v, _ in rows:
            recs = [r for r in v.log.records if r.get("kind") == "candidate"]
            lines += ["", f"## Variant {v.name}: decision funnel", ""]
            for k, n in sorted((v.ctx.funnel if v.ctx else {}).items(), key=lambda kv: -kv[1]):
                lines.append(f"- {k}: {n}")
            j = [r["judge"] for r in recs if r.get("judge")]
            if j:
                lat = [x["latency_ms"] for x in j if x.get("latency_ms") is not None]
                dec = {d: sum(1 for x in j if x.get("decision") == d) for d in ("APPROVE", "REJECT", "DEFER")}
                errs = sum(1 for x in j if x.get("error"))
                lines += ["", f"Judge calls: {len(j)}, decisions {dec}, errors/timeouts {errs}, "
                              f"latency p50 {statistics.median(lat):.0f} ms" if lat else ""]
        # News section: what the gate would block right now and the latest interpreted headlines.
        now = time.time()
        recent = self.news.recent(now - 24 * 3600)
        strong = [x for x in recent if self.news._strong_negative(x, now)]
        last_ok = (datetime.fromtimestamp(self.news.last_ok_poll, UTC).isoformat(timespec="seconds")
                   if self.news.last_ok_poll else "never")
        lines += ["", "## News (last 24 h)", "",
                  f"- headlines stored: {len(recent)}, interpreted: {sum(1 for x in recent if x.interp)}, "
                  f"feeds last read OK: {last_ok}",
                  f"- strong negative (would block): {len(strong)}", ""]
        for x in strong[:10]:
            i = x.interp or {}
            lines.append(f"  - [{x.source}] {x.title} -> assets {x.assets or '-'}, {i.get('event_type')}, "
                         f"systemic={i.get('systemic')}")
        with (self.dir / "news.jsonl").open("w", encoding="utf-8") as fh:
            for x in recent:
                fh.write(json.dumps(x.__dict__, ensure_ascii=False) + "\n")
        (self.dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------ main loop
    def run(self) -> None:
        sc = self.cfg.shadow
        next_depth = next_trades = next_flush = next_news = 0.0
        next_cycle = self._next_cycle_time()
        log.info("shadow %s started; end %s; next cycle %s", self.session,
                 datetime.fromtimestamp(self.end_t, UTC), datetime.fromtimestamp(next_cycle, UTC))
        self.poll_depth()
        if not self.symbols:  # first universe for recording before the first cycle
            self.symbols = build_universe(self.cfg, self.meta_path, {s: None for s in self.metas}).static_symbols[:35]
        while True:
            now = time.time()
            if now >= self.end_t:
                self.poll_depth()
                self.finish()
                self.flush_recordings()
                log.info("challenge end reached; shadow finished")
                return
            try:
                if now >= next_depth:
                    self.poll_depth()
                    for v in self.variants.values():
                        v.broker.retry_pending()
                        v.broker.check_stops()
                    next_depth = now + sc.depth_poll_s
                if now >= next_trades:
                    self.poll_trades()
                    next_trades = now + sc.trades_poll_s
                if now >= next_news:
                    self.poll_news()
                    next_news = now + self.cfg.news.poll_seconds
                if now >= next_cycle:
                    t = int(now) // H1 * H1
                    log.info("decision cycle t=%s", datetime.fromtimestamp(t, UTC))
                    self.cycle(t)
                    next_cycle = self._next_cycle_time()
                if now >= next_flush:
                    self.flush_recordings()
                    next_flush = now + 300
            except Exception:
                self.stats["api_errors"] += 1
                log.exception("shadow loop error (continuing)")
            time.sleep(1)

    def _next_cycle_time(self) -> float:
        now = time.time()
        return (int(now) // H1 + 1) * H1 + self.cfg.shadow.candle_poll_delay_s
