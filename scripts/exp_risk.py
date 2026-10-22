"""Information-only experiment (team question 2026-10-22): what if risk per trade were higher than 0.25 %?

v1.3 default config on the 3-year snapshot, 30-day windows of Validation and Test (same split as the backtest),
risk per trade / total open risk / max position scaled together. The 3 % kill switch, 1.5 % daily loss stop,
10 slots and 100,000 TMN minimum stay as they are (team risk rules).
Usage: python -I scripts/exp_risk.py
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

LEVELS = [(0.25, 1.0, 15.0), (0.5, 2.0, 15.0), (1.0, 4.0, 25.0), (2.0, 8.0, 40.0)]


def evaluate(args):
    risk, cap, pos, period = args
    import optimize_tf as o

    if not o._G:
        o._init("ecbb7bb14b3ec827")
    from wallex_agent.backtest.suite import episodes, run_episodes, with_overrides
    from wallex_agent.engine.features import FeatureProvider

    cfg = with_overrides(o._G["cfg"], **{"risk.risk_per_trade_pct": risk, "risk.max_open_risk_pct": cap,
                                         "risk.max_position_pct": pos})
    s, e = o._G["split"][period]
    rows, trades = run_episodes(cfg, o._G["view"], o._G["uni"], FeatureProvider(cfg), episodes(s, e, 720, [0, 360]))
    r = np.array([x["return_pct"] for x in rows])
    dd = np.array([x["max_drawdown_pct"] for x in rows])
    pnl = [t["pnl_tmn"] for t in trades]
    return {"risk_per_trade_%": risk, "open_risk_cap_%": cap, "max_position_%": pos, "period": period,
            "windows": len(rows), "trades": len(trades), "mean_ret_%": round(float(r.mean()), 3),
            "median_ret_%": round(float(np.median(r)), 3), "worst_window_%": round(float(r.min()), 3),
            "positive_%": round(100 * float((r > 0).mean())), "mean_maxdd_%": round(float(dd.mean()), 3),
            "worst_maxdd_%": round(float(dd.max()), 3), "kill_switch_windows": int(sum(x["killed"] for x in rows)),
            "fees_tmn": round(sum(t.get("fee_exit_tmn", 0) for t in trades)), "sum_pnl_tmn": round(sum(pnl))}


if __name__ == "__main__":
    tasks = [(r, c, p, per) for r, c, p in LEVELS for per in ("validation", "test")]
    with ProcessPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(evaluate, tasks))
    df = pd.DataFrame(res)
    out = ROOT / "reports" / "experiments"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "risk_levels.csv", index=False)
    pd.set_option("display.width", 250)
    print(df.to_string(index=False))
    print(json.dumps({"saved": str(out / "risk_levels.csv")}))
