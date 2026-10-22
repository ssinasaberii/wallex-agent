"""Information-only experiment (team question 2026-10-22): would any single coin have worked on its own?

Runs the strategy WITHOUT the fee gate (with the gate there are no trades at all) on the same 72h Validation
and Test episodes, for TMN signal prices and for dollar-adjusted signal prices, and breaks the results down
per coin. A coin only counts as "working" if it is profitable in BOTH periods (picking a coin after looking
at one period is curve fitting). Usage: python -I scripts/exp_per_coin.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from wallex_agent.backtest.runner import build_universe  # noqa: E402
from wallex_agent.backtest.suite import episodes, run_episodes, with_overrides  # noqa: E402
from wallex_agent.config import load_config  # noqa: E402
from wallex_agent.data.market_view import HistoricalMarketView  # noqa: E402
from wallex_agent.data.snapshot import load_snapshot  # noqa: E402
from wallex_agent.engine.features import FeatureProvider  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
cfg0 = load_config(apply_env=False)
candles, meta_path, _ = load_snapshot("0a4e977b89b6b576")
view = HistoricalMarketView(candles)
uni = build_universe(cfg0, meta_path, candles)
res = json.loads((ROOT / "reports/backtest/20261022T172742Z/results.json").read_text())
split = {k: [int(pd.Timestamp(x, tz="UTC").timestamp()) for x in v] for k, v in res["split_utc"].items()}
periods = {"validation": episodes(*split["validation"], 72, [0, 6, 12, 18]),
           "test": episodes(*split["test"], 72, [0, 6, 12, 18])}

all_rows = []
for sig in ("tmn", "usdt_adjusted"):
    c = with_overrides(cfg0, **{"fee_gate.enabled": False, "data.signal_price": sig})
    fp = FeatureProvider(c)
    for period, eps in periods.items():
        _, trades = run_episodes(c, view, uni, fp, eps)
        for t in trades:
            all_rows.append({"signal": sig, "period": period, **{k: t[k] for k in
                             ("symbol", "setup", "pnl_tmn", "r_multiple", "exit_type", "fee_exit_tmn")}})
        print(sig, period, len(trades), flush=True)

df = pd.DataFrame(all_rows)
out = ROOT / "reports" / "experiments"
out.mkdir(parents=True, exist_ok=True)
df.to_csv(out / "per_coin_trades.csv", index=False)
g = df.groupby(["signal", "symbol", "period"]).agg(trades=("pnl_tmn", "size"), pnl=("pnl_tmn", "sum"),
                                                   win=("pnl_tmn", lambda s: (s > 0).mean() * 100),
                                                   avg_r=("r_multiple", "mean")).reset_index()
wide = g.pivot_table(index=["signal", "symbol"], columns="period", values=["trades", "pnl", "win"]).fillna(0)
wide.columns = [f"{a}_{b}" for a, b in wide.columns]
wide = wide.reset_index()
wide["both_positive"] = (wide["pnl_validation"] > 0) & (wide["pnl_test"] > 0)
wide = wide.sort_values(["signal", "pnl_test"], ascending=[True, False])
wide.to_csv(out / "per_coin_summary.csv", index=False)
pd.set_option("display.width", 200)
print(wide.round(1).to_string(index=False))
setup = df.groupby(["signal", "setup", "period"])["pnl_tmn"].agg(["size", "sum"]).round(0)
print(setup.to_string())
