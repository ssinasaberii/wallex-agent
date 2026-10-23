"""Information-only experiment (user question 2026-10-23): shorter decision windows with the v1.4.1 rules.

  1D/4h  current: daily regime, 4h trigger (v1.4.1)
  1D/1h  daily regime, 1h trigger, everything else v1.4.1
  4h/1h  4h regime, 1h trigger (the old v1.2 timeframes with the v1.4.1 exits), holds up to 48 h
15 minutes cannot be backtested: Wallex serves only 1h and 1D candles (api-verification V2). Instead, the typical
absolute move per bar (median over coins and bars, 3-year snapshot) at 1h / 4h / 1D, and 15m estimated as half of
1h (random-walk scaling), is compared with the round-trip cost of one trade (fees 0.6 % + slippage + spread).
Periods: one continuous 5M TMN account over Validation+Test (9.6 months) and over Train (14.4 months), spread paid.
Usage: python -I scripts/exp_timeframe.py   (writes reports/experiments/timeframe_now.{csv,json})
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

TF = {  # name: overrides on top of v1.4.1
    "1D/4h": {},
    "1D/1h": {"data.trigger_hours": 1},
    "4h/1h": {"data.regime_hours": 4, "data.trigger_hours": 1, "data.min_regime_bars": 150,
              "indicators.atr_pct_rank_window": 360, "stops.max_stop_pct": 4.0,
              "setups.T1_pullback.h_max": 48, "setups.T2_breakout.h_max": 48, "setups.T3_meanrev.h_max": 48,
              "exits.time_exit_t12_max_bars": 48, "exits.time_exit_t3_bars": 48},
}


def evaluate(args):
    tf, period = args
    import optimize_tf as o
    import period_stats

    if not o._G:
        o._init("ecbb7bb14b3ec827")
    from wallex_agent.backtest.metrics import max_drawdown
    from wallex_agent.backtest.runner import run_period
    from wallex_agent.backtest.suite import with_overrides
    from wallex_agent.engine.features import FeatureProvider

    G = o._G
    cfg = with_overrides(G["cfg"], **TF[tf])
    s, e = (G["split"]["validation"][0], G["split"]["test"][1]) if period == "continuous" else G["split"]["train"]
    r = run_period(cfg, G["view"], G["uni"], s, e, features=FeatureProvider(cfg), spread_cost=G["uni"].spread_est)
    eq = pd.Series([x for _, x in r.equity], index=pd.to_datetime([t for t, _ in r.equity], unit="s", utc=True))
    st = period_stats.compute(eq, pd.DataFrame(r.trades), "")
    pnl = [t["pnl_tmn"] for t in r.trades]
    gw, gl = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x <= 0)
    hold = [(t["exit_t"] - t["entry_t"]) / 3600 for t in r.trades]
    return {"tf": tf, "period": period, "return_%": round(st["total_return_pct"], 2),
            "max_drawdown_%": round(100 * max_drawdown(eq.values), 2), "worst_month_%": round(st["monthly"]["worst_pct"], 2),
            "trades": len(r.trades), "win_%": round(100 * float(np.mean([x > 0 for x in pnl])), 1) if pnl else 0.0,
            "profit_factor": round(gw / gl, 2) if gl > 0 else None, "fees_tmn": round(r.fees_paid),
            "median_hold_h": round(float(np.median(hold)), 1) if hold else None, "kill_trips": r.kill_count,
            "capital_in_use_mean_%": round(st["capital_in_positions_pct"]["mean_all_hours"], 1)}


def typical_moves():
    from wallex_agent.backtest.runner import build_universe  # noqa: F401 (same package import path)
    from wallex_agent.data.snapshot import load_snapshot

    candles, _, _ = load_snapshot("ecbb7bb14b3ec827")
    out = {}
    for h, name in ((1, "1h"), (4, "4h"), (24, "1D")):
        med = []
        for s, df in candles.items():
            if not s.endswith("TMN") or s == "USDTTMN" or len(df) < 5000:
                continue
            c = df.set_index("open_time")["close"]
            c = c[c.index % (h * 3600) == 0] if h > 1 else c
            med.append(float((100 * np.log(c).diff().abs()).median()))
        out[name] = round(float(np.median(med)), 3)
    out["15m (estimate = 1h / 2)"] = round(out["1h"] / 2, 3)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    tasks = [(tf, p) for tf in TF for p in ("continuous", "train")]
    with ProcessPoolExecutor(max_workers=len(tasks)) as ex:
        res = list(ex.map(evaluate, tasks))
    moves = typical_moves()
    out = ROOT / "reports" / "experiments"
    df = pd.DataFrame(res)
    df.to_csv(out / "timeframe_now.csv", index=False)
    (out / "timeframe_now.json").write_text(json.dumps({"runs": res, "median_abs_move_per_bar_%": moves}, indent=1),
                                            encoding="utf-8")
    pd.set_option("display.width", 250)
    print(df.to_string(index=False))
    print(json.dumps(moves))
