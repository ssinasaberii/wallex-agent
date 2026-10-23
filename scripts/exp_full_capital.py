"""Information-only experiment (user question 2026-10-23): what if the whole capital were put to work?

Same signals and exits as v1.4.1; only sizing changes. Each buy = a fixed share of equity (risk budget and open-risk
cap set out of the way, equity cap lifted so gains compound), up to the 10 slots and the free cash:
  ~100 % (one coin at a time), 50 %, 33 %, 20 %, 10 % (all 10 slots) vs the v1.4.1 rule (0.5 % risk per trade).
Each with the team's stops (1.5 % daily loss, 3 % kill switch re-arming daily) and with no stops.
Note: with risk-based sizing off, the 0.5x size for a high-volatility BTC regime no longer shrinks positions.

  continuous  one 5M TMN account over Validation+Test (2026-01-03 .. 2026-10-22), period_stats on it
  train       one 5M TMN account over Train (2024-10-23 .. 2026-01-03)
Usage: python -I scripts/exp_full_capital.py   (writes reports/experiments/full_capital.{csv,json})
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

SIZES = [None, 97.0, 48.0, 32.0, 19.0, 9.5]  # % of equity per buy; None = v1.4.1 risk-based sizing (fees need headroom)
STOPS = ["team", "none"]


def overrides(size, stops) -> dict:
    o = {}
    if size is not None:
        o.update({"risk.risk_per_trade_pct": 1000.0, "risk.max_open_risk_pct": 100000.0,
                  "risk.max_position_pct": size, "risk.equity_cap_tmn": 1e15})
    if stops == "none":
        o.update({"risk.max_daily_loss_pct": 1000.0, "risk.kill_switch_drawdown_pct": 1000.0})
    return o


def evaluate(args):
    size, stops, period = args
    import optimize_tf as o
    import period_stats

    if not o._G:
        o._init("ecbb7bb14b3ec827")
    from wallex_agent.backtest.metrics import max_drawdown
    from wallex_agent.backtest.runner import run_period
    from wallex_agent.backtest.suite import with_overrides
    from wallex_agent.engine.features import FeatureProvider

    G = o._G
    cfg = with_overrides(G["cfg"], **overrides(size, stops))
    s, e = (G["split"]["validation"][0], G["split"]["test"][1]) if period == "continuous" else G["split"]["train"]
    r = run_period(cfg, G["view"], G["uni"], s, e, features=FeatureProvider(cfg))
    eq = pd.Series([x for _, x in r.equity], index=pd.to_datetime([t for t, _ in r.equity], unit="s", utc=True))
    st = period_stats.compute(eq, pd.DataFrame(r.trades), "")
    pnl = [t["pnl_tmn"] for t in r.trades]
    gw, gl = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x <= 0)
    row = {"size_%": "v1.4.1 (0.5% risk)" if size is None else size, "stops": stops, "period": period,
           "return_%": round(st["total_return_pct"], 2), "end_tmn": round(float(eq.iloc[-1])),
           "max_drawdown_%": round(100 * max_drawdown(eq.values), 2),
           "capital_in_use_mean_%": round(st["capital_in_positions_pct"]["mean_all_hours"], 1),
           "capital_in_use_max_%": round(st["capital_in_positions_pct"]["max"], 1),
           "monthly_mean_%": round(st["monthly"]["mean_pct"], 2), "worst_month_%": round(st["monthly"]["worst_pct"], 2),
           "best_month_%": round(st["monthly"]["best_pct"], 2), "worst_day_%": round(st["daily"]["worst_pct"], 2),
           "worst_week_%": round(st["weekly"]["worst_pct"], 2), "trades": len(r.trades),
           "win_%": round(100 * float(np.mean([x > 0 for x in pnl])), 1) if pnl else 0.0,
           "profit_factor": round(gw / gl, 2) if gl > 0 else None, "fees_tmn": round(r.fees_paid),
           "kill_trips": r.kill_count}
    return row, st


if __name__ == "__main__":
    tasks = [(s, k, p) for s in SIZES for k in STOPS for p in ("continuous", "train")]
    with ProcessPoolExecutor(max_workers=24) as ex:
        res = list(ex.map(evaluate, tasks))
    out = ROOT / "reports" / "experiments"
    out.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([r for r, _ in res])
    df.to_csv(out / "full_capital.csv", index=False)
    (out / "full_capital.json").write_text(json.dumps([{"row": r, "stats": s} for r, s in res], indent=1,
                                                      default=float))
    pd.set_option("display.width", 260)
    print(df.to_string(index=False))
