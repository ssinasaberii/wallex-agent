"""Daily / weekly / monthly return and capital usage of a backtest's continuous run.
Usage: python -I scripts/period_stats.py <run_id>   (writes reports/experiments/period_stats_<run_id>.json)"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
run = sys.argv[1]
d = ROOT / "reports" / "backtest" / run
eq = pd.read_csv(d / "equity.csv")
eq["dt"] = pd.to_datetime(eq["t"], unit="s", utc=True)
eq = eq.set_index("dt")["equity_tmn"]
tr = pd.read_csv(d / "trades_continuous.csv")

# capital in positions each hour: sum of entry notional of positions open in that hour / equity
expo = pd.Series(0.0, index=eq.index)
for _, t in tr.iterrows():
    a, b = pd.to_datetime(int(t["entry_t"]), unit="s", utc=True), pd.to_datetime(int(t["exit_t"]), unit="s", utc=True)
    expo[(expo.index >= a) & (expo.index < b)] += float(t["qty"]) * float(t["entry_price"])
expo_pct = 100 * expo / eq

out = {"run": run, "start": str(eq.index[0]), "end": str(eq.index[-1]), "start_equity": float(eq.iloc[0]),
       "end_equity": float(eq.iloc[-1]), "total_return_pct": 100 * (eq.iloc[-1] / eq.iloc[0] - 1),
       "hours_with_any_position_pct": float(100 * (expo > 0).mean()),
       "capital_in_positions_pct": {"mean_all_hours": float(expo_pct.mean()),
                                    "mean_when_invested": float(expo_pct[expo > 0].mean()),
                                    "max": float(expo_pct.max())}}
for name, rule in (("daily", "D"), ("weekly", "W-SUN"), ("monthly", "MS")):
    end = eq.resample(rule).last()
    start = eq.resample(rule).first()
    r = 100 * (end / start.shift(0) - 1)  # within-period change (first to last hour)
    r = 100 * (end / end.shift(1) - 1)
    r.iloc[0] = 100 * (end.iloc[0] / eq.iloc[0] - 1)
    r = r.dropna()
    e = expo_pct.resample(rule).mean()
    n = len(r)
    out[name] = {"periods": n, "mean_pct": float(r.mean()), "median_pct": float(r.median()),
                 "compound_avg_pct": float(100 * ((eq.iloc[-1] / eq.iloc[0]) ** (1 / n) - 1)),
                 "positive_pct": float(100 * (r > 0).mean()), "zero_pct": float(100 * (r.abs() < 1e-9).mean()),
                 "negative_pct": float(100 * (r < -1e-9).mean()), "best_pct": float(r.max()),
                 "worst_pct": float(r.min()), "avg_capital_in_positions_pct": float(e.mean()),
                 "max_period_avg_capital_pct": float(e.max())}
months = pd.DataFrame({"return_pct": 100 * (eq.resample("MS").last() / eq.resample("MS").last().shift(1) - 1),
                       "avg_capital_pct": expo_pct.resample("MS").mean(),
                       "trades": tr.assign(m=pd.to_datetime(tr["entry_t"], unit="s", utc=True).dt.to_period("M"))
                       .groupby("m").size().reindex(eq.resample("MS").last().index.to_period("M")).fillna(0).values})
months.iloc[0, 0] = 100 * (eq.resample("MS").last().iloc[0] / eq.iloc[0] - 1)
out["by_month"] = {str(k.date()): {kk: round(float(vv), 3) for kk, vv in v.items()} for k, v in months.iterrows()}
(ROOT / "reports" / "experiments" / f"period_stats_{run}.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1, default=lambda x: round(x, 4)))
_ = np
