"""Information-only experiment (user question 2026-10-23): hold the capital in USDT and trade only USDT pairs,
whose Wallex fees are much lower (credit account: maker 0.10 %, taker 0.13 % vs 0.25 / 0.30 % on TMN pairs;
confirmed with GET /v1/account/fee).

The 3-year snapshot has TMN pairs only. A USDT pair's price is the TMN price divided by the USDT/TMN rate of the
same hour, so the USDT account is simulated on those prices: every TMN pair X is replaced by X / USDTTMN x R0
(R0 = the rate at the period start, so amounts keep TMN-like sizes and the 100,000 TMN minimum stays meaningful),
USDTTMN is held at R0. The account is then "USDT valued at the start rate"; its TMN value at the end is
units x rate_end / R0. Converting 5M TMN to USDT at the start and back at the end costs the TMN taker fee each way.
Universe: only coins whose live USDT pair spread is <= universe.max_spread_pct (fetched once from /v2/depth/all;
the same static-spread limitation as the TMN backtest). Same v1.4.1 strategy and risk rules everywhere.

  A  TMN pairs, TMN fees (v1.4.1 as is)
  B  USDT pairs, USDT fees
  C  USDT pairs, TMN fees (isolates the fee effect from the dollar effect)
  D  B with 2x slippage (USDT books are thinner)
  E  B with all 28 coins (as if every USDT pair were as liquid as its TMN pair)
  F  TMN pairs, TMN fees, only the coins of B (isolates the effect of the smaller universe)
Periods: one continuous 5M account over Validation+Test (9.6 months) and over Train (14.4 months).
Also: how much USDT/TMN moves in 72 hours (the time left in the challenge).
Usage: python -I scripts/exp_usdt_account.py   (writes reports/experiments/usdt_account.{csv,json})
"""

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

SNAP = "ecbb7bb14b3ec827"
OUT = ROOT / "reports" / "experiments"
USDT_FEES = {"fees.maker_pct": 0.10, "fees.taker_pct": 0.13, "fees.snapshot_id": "wallex-credit-usdt-2026-10-23"}
VARIANTS = {"A": ("tmn", {}, 1.0, "all"), "B": ("usdt", USDT_FEES, 1.0, "liquid"), "C": ("usdt", {}, 1.0, "liquid"),
            "D": ("usdt", USDT_FEES, 2.0, "liquid"), "E": ("usdt", USDT_FEES, 1.0, "all"),
            "F": ("tmn", {}, 1.0, "liquid")}
_G: dict = {}


def live_usdt_spreads() -> dict[str, float]:
    import httpx

    d = httpx.get("https://api.wallex.ir/v2/depth/all", timeout=30).json()["result"]
    out = {}
    for s, book in d.items():
        if not s.endswith("USDT"):
            continue
        try:
            ask, bid = float(book["ask"][0]["price"]), float(book["bid"][0]["price"])
            out[s[:-4]] = 100 * (ask - bid) / ((ask + bid) / 2)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


def _usdt_candles(candles: dict, usdt_sym: str, r0: float) -> dict:
    u = candles[usdt_sym].set_index("open_time")["close"]
    out = {}
    for s, df in candles.items():
        if s == usdt_sym:
            c = df.copy()
            c[["open", "high", "low", "close"]] = r0
            out[s] = c
        elif s.endswith("TMN"):
            c = df.copy()
            f = r0 / c["open_time"].map(u).ffill().bfill()
            for k in ("open", "high", "low", "close"):
                c[k] = c[k] * f
            out[s] = c
        else:
            out[s] = df
    return out


def _init(spreads):
    import optimize_tf as o

    o._init(SNAP)
    from wallex_agent.data.snapshot import load_snapshot

    candles, _, _ = load_snapshot(SNAP)
    _G.update(o._G, candles=candles, usdt_spreads=spreads)


def evaluate(args):
    var, period = args
    import period_stats
    from wallex_agent.backtest.metrics import max_drawdown
    from wallex_agent.backtest.runner import Universe, run_period
    from wallex_agent.backtest.suite import with_overrides
    from wallex_agent.data.market_view import HistoricalMarketView
    from wallex_agent.engine.features import FeatureProvider

    market, fees, slip, coins = VARIANTS[var]
    cfg = with_overrides(_G["cfg"], **fees)
    s, e = (_G["split"]["validation"][0], _G["split"]["test"][1]) if period == "continuous" else _G["split"]["train"]
    usym = cfg.data.usdt_tmn_symbol
    u = _G["candles"][usym].set_index("open_time")["close"]
    r0, r1 = float(u[u.index <= s].iloc[-1]), float(u[u.index <= e].iloc[-1])
    conv = cfg.fees.taker_pct / 100 if market == "tmn" else _G["cfg"].fees.taker_pct / 100  # USDTTMN is a TMN pair
    view, uni, start_units = _G["view"], _G["uni"], 5e6
    sp = _G["usdt_spreads"]
    if coins == "liquid":
        static = [x for x in _G["uni"].static_symbols
                  if x[:-3] in sp and sp[x[:-3]] <= cfg.universe.max_spread_pct]
        spread = {x: sp[x[:-3]] for x in static} if market == "usdt" else             {x: v for x, v in _G["uni"].spread_est.items() if x in static}
        uni = Universe(_G["uni"].metas, static, spread, set(static[: cfg.slippage.tier1_top_n]))
    if market == "usdt":
        view = HistoricalMarketView(_usdt_candles(_G["candles"], usym, r0))
        start_units = 5e6 * (1 - conv)
    r = run_period(cfg, view, uni, s, e, features=FeatureProvider(cfg), initial_tmn=start_units, slip_mult=slip)
    eq = pd.Series([x for _, x in r.equity], index=pd.to_datetime([t for t, _ in r.equity], unit="s", utc=True))
    st = period_stats.compute(eq, pd.DataFrame(r.trades), "")
    end_units = float(eq.iloc[-1])
    end_tmn = end_units * (r1 / r0) * (1 - conv) if market == "usdt" else end_units
    pnl = [t["pnl_tmn"] for t in r.trades]
    gw, gl = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x <= 0)
    syms = sorted({t["symbol"] for t in r.trades})
    row = {"variant": var, "period": period, "pairs": "USDT" if market == "usdt" else "TMN",
           "taker_%": cfg.fees.taker_pct, "slip_x": slip, "coins": len(uni.static_symbols),
           "strategy_return_%": round(100 * (end_units / start_units - 1), 2),
           "end_value_tmn": round(end_tmn), "return_in_tmn_%": round(100 * (end_tmn / 5e6 - 1), 2),
           "hold_usdt_in_tmn_%": round(100 * ((1 - conv) ** 2 * r1 / r0 - 1), 2),
           "max_drawdown_%": round(100 * max_drawdown(eq.values), 2),
           "monthly_mean_%": round(st["monthly"]["mean_pct"], 2), "worst_month_%": round(st["monthly"]["worst_pct"], 2),
           "trades": len(r.trades), "win_%": round(100 * float(np.mean([x > 0 for x in pnl])), 1) if pnl else 0.0,
           "profit_factor": round(gw / gl, 2) if gl > 0 else None, "fees_paid": round(r.fees_paid),
           "kill_trips": r.kill_count, "capital_in_use_mean_%": round(st["capital_in_positions_pct"]["mean_all_hours"], 1)}
    return row, {"symbols_traded": syms, "universe": uni.static_symbols}


def usdt_72h(candles, usym) -> dict:
    c = candles[usym].set_index("open_time")["close"]
    r = 100 * (c.shift(-72) / c - 1).dropna()
    return {"windows": int(len(r)), "mean_%": round(float(r.mean()), 2), "median_%": round(float(r.median()), 2),
            "p5_%": round(float(np.percentile(r, 5)), 2), "p95_%": round(float(np.percentile(r, 95)), 2),
            "worst_%": round(float(r.min()), 2), "best_%": round(float(r.max()), 2),
            "negative_share_%": round(float(100 * (r < 0).mean()), 1)}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    spreads = live_usdt_spreads()
    tasks = [(v, p) for v in VARIANTS for p in ("continuous", "train")]
    with ProcessPoolExecutor(max_workers=len(tasks), initializer=_init, initargs=(spreads,)) as ex:
        res = list(ex.map(evaluate, tasks))
    from wallex_agent.data.snapshot import load_snapshot

    candles, _, _ = load_snapshot(SNAP)
    move = usdt_72h(candles, "USDTTMN")
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([r for r, _ in res])
    df.to_csv(OUT / "usdt_account.csv", index=False)
    (OUT / "usdt_account.json").write_text(json.dumps({"usdt_tmn_72h_move": move, "live_usdt_spreads": spreads,
                                                       "runs": [{"row": r, **x} for r, x in res]}, indent=1,
                                                      ensure_ascii=False, default=float), encoding="utf-8")
    pd.set_option("display.width", 280)
    print(df.to_string(index=False))
    print(json.dumps({"usdt_tmn_72h_move": move, "universe_usdt": res[2][1]["universe"],
                      "traded_B_cont": res[2][1]["symbols_traded"], "universe_E": res[8][1]["universe"]}, ensure_ascii=False))
