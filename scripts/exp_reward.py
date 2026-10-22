"""Information-only experiment (team question 2026-10-22): what happens with higher reward targets?
Runs the same 72h Validation and Test episodes as the backtest suite with different target R multiples.
Does not change config/default.yaml. Usage: python -I scripts/exp_reward.py"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wallex_agent.backtest.metrics import episode_stats  # noqa: E402
from wallex_agent.backtest.runner import build_universe  # noqa: E402
from wallex_agent.backtest.suite import episodes, run_episodes, with_overrides  # noqa: E402
from wallex_agent.config import load_config  # noqa: E402
from wallex_agent.data.market_view import HistoricalMarketView  # noqa: E402
from wallex_agent.data.snapshot import load_snapshot  # noqa: E402
from wallex_agent.engine.features import FeatureProvider  # noqa: E402

SNAP = "0a4e977b89b6b576"
C_MOVE = 0.355  # calibrated on Train in reports/backtest/20261022T172742Z
# split from the same report
TR_END, VA_END = 1785436200 // 3600 * 3600, 0

cfg0 = load_config(apply_env=False)
candles, meta_path, _ = load_snapshot(SNAP)
view = HistoricalMarketView(candles)
uni = build_universe(cfg0, meta_path, candles)
res = json.loads((Path(__file__).resolve().parents[1] / "reports/backtest/20261022T172742Z/results.json").read_text())
import pandas as pd  # noqa: E402

split = {k: [int(pd.Timestamp(x, tz="UTC").timestamp()) for x in v] for k, v in res["split_utc"].items()}
val = episodes(split["validation"][0], split["validation"][1], 72, [0, 6, 12, 18])
test = episodes(split["test"][0], split["test"][1], 72, [0, 6, 12, 18])

rows_out = []
for t1r, t2r in ((2.0, 2.5), (3.0, 3.5), (4.0, 5.0), (5.0, 6.0)):
    for gate_name, extra in (("calibrated horizon gate (c_move 0.355)", {"fee_gate.c_move": C_MOVE}),
                             ("horizon check off, hold up to 24h", {"fee_gate.c_move": 10.0,
                                                                     "fee_gate.horizons_h": [24]})):
        c = with_overrides(cfg0, **{"setups.T1_pullback.target_r": t1r, "setups.T2_breakout.target_r": t2r,
                                    **extra})
        fp = FeatureProvider(c)
        for period, eps in (("validation", val), ("test", test)):
            rows, trades = run_episodes(c, view, uni, fp, eps)
            s = episode_stats(rows)
            wins = sum(1 for t in trades if t["pnl_tmn"] > 0)
            exits = pd.Series([t["exit_type"] for t in trades]).value_counts().to_dict() if trades else {}
            rows_out.append({"T1/T2 target R": f"{t1r}/{t2r}", "gate": gate_name, "period": period,
                             "trades": s["total_trades"], "median_72h_%": round(s["median_return_pct"], 3),
                             "p10_%": round(s["p10_return_pct"], 3), "p90_%": round(s["p90_return_pct"], 3),
                             "positive_eps_%": round(s["positive_pct"]), "win_rate_%": round(100 * wins / len(trades)) if trades else 0,
                             "sum_pnl_tmn": round(sum(t["pnl_tmn"] for t in trades)), "exits": exits})
            print(json.dumps(rows_out[-1], ensure_ascii=False), flush=True)

out = Path(__file__).resolve().parents[1] / "reports" / "experiments"
out.mkdir(parents=True, exist_ok=True)
pd.DataFrame(rows_out).to_csv(out / "reward_targets.csv", index=False)
