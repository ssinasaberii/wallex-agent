"""Information-only experiment (team question 2026-10-23): what if the 3 % kill switch re-armed daily, weekly or
monthly instead of never? v1.4 default config on the 3-year snapshot.

  continuous  one 5M TMN account over Validation+Test (2026-01-03 .. 2026-10-22), no other reset
  windows     30-day windows of Validation and Test (each starts with fresh capital)
Usage: python -I scripts/exp_killswitch.py
"""

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

MODES = ["never", "daily", "weekly", "monthly"]


def evaluate(args):
    mode, what = args
    import optimize_tf as o

    if not o._G:
        o._init("ecbb7bb14b3ec827")
    from wallex_agent.backtest.metrics import max_drawdown
    from wallex_agent.backtest.runner import run_period
    from wallex_agent.backtest.suite import episodes, run_episodes, with_overrides
    from wallex_agent.engine.features import FeatureProvider

    cfg = with_overrides(o._G["cfg"], **{"risk.kill_switch_reset": mode})
    fp = FeatureProvider(cfg)
    if what == "continuous":
        s, e = o._G["split"]["validation"][0], o._G["split"]["test"][1]
        r = run_period(cfg, o._G["view"], o._G["uni"], s, e, features=fp)
        eq = np.array([x for _, x in r.equity])
        pnl = [t["pnl_tmn"] for t in r.trades]
        return {"kill_reset": mode, "run": "one account, 9.6 months",
                "return_%": round(100 * (r.final_equity / 5e6 - 1), 2), "max_drawdown_%": round(100 * max_drawdown(eq), 2),
                "trades": len(r.trades), "win_%": round(100 * float(np.mean([x > 0 for x in pnl])), 1) if pnl else 0,
                "kill_switch_trips": r.kill_count, "re_arms": r.kill_resets, "fees_tmn": round(r.fees_paid)}
    s, e = o._G["split"][what]
    rows, trades = run_episodes(cfg, o._G["view"], o._G["uni"], fp, episodes(s, e, 720, [0, 360]))
    ret = np.array([x["return_pct"] for x in rows])
    return {"kill_reset": mode, "run": f"30-day windows, {what}", "return_%": round(float(ret.mean()), 3),
            "max_drawdown_%": round(float(np.max([x["max_drawdown_pct"] for x in rows])), 2), "trades": len(trades),
            "win_%": None, "kill_switch_trips": int(sum(x["killed"] for x in rows)), "re_arms": None, "fees_tmn": None}


if __name__ == "__main__":
    tasks = [(m, w) for m in MODES for w in ("continuous", "validation", "test")]
    with ProcessPoolExecutor(max_workers=12) as ex:
        res = list(ex.map(evaluate, tasks))
    df = pd.DataFrame(res).sort_values(["run", "kill_reset"])
    out = ROOT / "reports" / "experiments"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "killswitch_reset.csv", index=False)
    pd.set_option("display.width", 220)
    print(df.to_string(index=False))
