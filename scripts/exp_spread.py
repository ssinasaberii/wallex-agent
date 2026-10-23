"""Information-only experiment (user request 2026-10-23): loosen or remove the 0.30 % spread filter.

The backtest has no historical spreads; the universe filter uses one snapshot (Phase 0 market metadata) and fills
pay only the fixed slippage tiers, not the spread. A wider universe would look too good unless its spread is paid,
so here every coin pays half its spread on each fill (on top of the tiers). Spread per coin = mean of the snapshot
value and a live /v2/depth/all reading today (both used for the filter and the cost).
  official  max_spread 0.30 %, snapshot spreads, no spread cost (the official v1.4.1 backtest)
  official+cost  the same 9 coins, spread paid (separates the cost from the change of universe)
  0.3 / 0.5 / 1.0 / none   max_spread limit, spread paid
Periods: one continuous 5M TMN account over Validation+Test (9.6 months) and over Train (14.4 months).
Usage: python -I scripts/exp_spread.py   (writes reports/experiments/spread_filter.{csv,json})
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

LIMITS = {"official": 0.30, "official+cost": 0.30, "0.3": 0.30, "0.5": 0.50, "1.0": 1.00, "none": 100.0}


def live_tmn_spreads() -> dict[str, float]:
    import httpx

    d = httpx.get("https://api.wallex.ir/v2/depth/all", timeout=30).json()["result"]
    out = {}
    for s, b in d.items():
        try:
            ask, bid = float(b["ask"][0]["price"]), float(b["bid"][0]["price"])
            out[s] = 100 * (ask - bid) / ((ask + bid) / 2)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


def evaluate(args):
    var, period, live = args
    import optimize_tf as o
    import period_stats

    if not o._G:
        o._init("ecbb7bb14b3ec827")
    from wallex_agent.backtest.metrics import max_drawdown
    from wallex_agent.backtest.runner import Universe, run_period
    from wallex_agent.backtest.suite import with_overrides
    from wallex_agent.engine.features import FeatureProvider

    G = o._G
    u0 = G["uni"]
    cfg = with_overrides(G["cfg"], **{"universe.max_spread_pct": LIMITS[var]})
    sp_mix = {s: (v + live[s]) / 2 if s in live else v for s, v in u0.spread_est.items()}
    if var == "official":
        uni, cost = u0, None
    elif var == "official+cost":
        uni, cost = u0, sp_mix
    else:
        sp = {s: (v + live[s]) / 2 if s in live else v for s, v in u0.spread_est.items()}
        uni, cost = Universe(u0.metas, u0.static_symbols, sp, u0.tier1), sp
    s, e = (G["split"]["validation"][0], G["split"]["test"][1]) if period == "continuous" else G["split"]["train"]
    r = run_period(cfg, G["view"], uni, s, e, features=FeatureProvider(cfg), spread_cost=cost)
    eq = pd.Series([x for _, x in r.equity], index=pd.to_datetime([t for t, _ in r.equity], unit="s", utc=True))
    st = period_stats.compute(eq, pd.DataFrame(r.trades), "")
    pnl = [t["pnl_tmn"] for t in r.trades]
    gw, gl = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x <= 0)
    coins = sorted(x for x in uni.static_symbols if uni.spread_est.get(x, 99) <= LIMITS[var])
    by_coin = {}
    for t in r.trades:
        by_coin[t["symbol"]] = by_coin.get(t["symbol"], 0.0) + t["pnl_tmn"]
    row = {"limit": var, "period": period, "coins": len(coins), "return_%": round(st["total_return_pct"], 2),
           "max_drawdown_%": round(100 * max_drawdown(eq.values), 2), "monthly_mean_%": round(st["monthly"]["mean_pct"], 2),
           "worst_month_%": round(st["monthly"]["worst_pct"], 2), "trades": len(r.trades),
           "win_%": round(100 * float(np.mean([x > 0 for x in pnl])), 1) if pnl else 0.0,
           "profit_factor": round(gw / gl, 2) if gl > 0 else None, "fees_tmn": round(r.fees_paid),
           "kill_trips": r.kill_count, "capital_in_use_mean_%": round(st["capital_in_positions_pct"]["mean_all_hours"], 1)}
    return row, {"coins": coins, "pnl_by_coin": {k: round(v) for k, v in sorted(by_coin.items(), key=lambda kv: kv[1])}}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    live = live_tmn_spreads()
    tasks = [(v, p, live) for v in LIMITS for p in ("continuous", "train")]
    with ProcessPoolExecutor(max_workers=len(tasks)) as ex:
        res = list(ex.map(evaluate, tasks))
    out = ROOT / "reports" / "experiments"
    df = pd.DataFrame([r for r, _ in res])
    df.to_csv(out / "spread_filter.csv", index=False)
    (out / "spread_filter.json").write_text(json.dumps([{"row": r, **x} for r, x in res], indent=1,
                                                       ensure_ascii=False), encoding="utf-8")
    pd.set_option("display.width", 250)
    print(df.to_string(index=False))
    for r, x in res:
        if r["period"] == "continuous":
            print(r["limit"], x["coins"], x["pnl_by_coin"])
