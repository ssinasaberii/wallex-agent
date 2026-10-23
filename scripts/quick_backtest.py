"""Quick backtest for the dashboard: one continuous 5M TMN account over Validation+Test (9.6 months) with the
given config overrides, spread paid (as in exp_spread.py), next to the current config (cached by config hash).
Usage: python -I scripts/quick_backtest.py --overrides '{"risk.risk_per_trade_pct": 0.75}' --out result.json
"""

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def run(overrides: dict) -> dict:
    import numpy as np
    import optimize_tf as o
    import pandas as pd
    import period_stats

    from wallex_agent.backtest.metrics import max_drawdown
    from wallex_agent.backtest.runner import Universe, run_period
    from wallex_agent.backtest.suite import with_overrides
    from wallex_agent.engine.features import FeatureProvider

    if not o._G:
        o._init("ecbb7bb14b3ec827")
    G = o._G
    cfg = with_overrides(G["cfg"], **{k: v for k, v in overrides.items() if not k.startswith("live.")})
    u = G["uni"]
    uni = Universe(u.metas, u.static_symbols, u.spread_est, u.tier1)
    s, e = G["split"]["validation"][0], G["split"]["test"][1]
    r = run_period(cfg, G["view"], uni, s, e, features=FeatureProvider(cfg), spread_cost=u.spread_est)
    eq = pd.Series([x for _, x in r.equity], index=pd.to_datetime([t for t, _ in r.equity], unit="s", utc=True))
    st = period_stats.compute(eq, pd.DataFrame(r.trades), "")
    pnl = [t["pnl_tmn"] for t in r.trades]
    gw, gl = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x <= 0)
    step = max(1, len(eq) // 300)
    return {"return_pct": round(st["total_return_pct"], 2), "end_tmn": round(float(eq.iloc[-1])),
            "max_drawdown_pct": round(100 * max_drawdown(eq.values), 2), "monthly_mean_pct": round(st["monthly"]["mean_pct"], 2),  # noqa: E501
            "worst_month_pct": round(st["monthly"]["worst_pct"], 2), "trades": len(r.trades),
            "win_pct": round(100 * float(np.mean([x > 0 for x in pnl])), 1) if pnl else 0.0,
            "profit_factor": round(gw / gl, 2) if gl > 0 else None, "fees_tmn": round(r.fees_paid),
            "kill_trips": r.kill_count, "capital_in_use_pct": round(st["capital_in_positions_pct"]["mean_all_hours"], 1),  # noqa: E501
            "equity": [[int(t.timestamp()), round(float(v))] for t, v in eq.iloc[::step].items()]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--overrides", default="{}")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ov = json.loads(a.overrides)
    from wallex_agent.config import load_config

    cache = ROOT / "reports" / "dashboard_bt" / f"baseline_{load_config(apply_env=False).config_hash}.json"
    with ProcessPoolExecutor(max_workers=2) as ex:
        fut_new = ex.submit(run, ov)
        base = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else ex.submit(run, {}).result()
        new = fut_new.result()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(base), encoding="utf-8")
    Path(a.out).write_text(json.dumps({"overrides": ov, "current": base, "proposed": new,
                                       "period": "2026-01-03 .. 2026-10-22 (9.6 months), spread paid"}), encoding="utf-8")  # noqa: E501


if __name__ == "__main__":
    main()
