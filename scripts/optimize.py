"""Parameter search with a strict split (team request 2026-10-22: make the backtest profitable).

  stage 1  every grid config on TRAIN episodes          (search)
  stage 2  the best configs on VALIDATION episodes       (selection; must also be positive on Train)
  stage 3  the single chosen config on TEST, once        (report only; never used for choosing)

Risk limits are never changed (0.25 % per trade, 1 % open, 15 % position, 10 slots, kill switch).
Runs configs in parallel processes. Usage:
  python -I scripts/optimize.py stage1|stage2|stage3 [--workers 20]
Results: reports/experiments/opt_stage{1,2,3}.csv
"""

import argparse
import itertools
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = ROOT / "reports" / "experiments"
SNAP = "0a4e977b89b6b576"
SPLIT = {"train": ["2026-03-27 00:00", "2026-07-30 18:00"], "validation": ["2026-07-30 18:00", "2026-09-10 16:00"],
         "test": ["2026-09-10 16:00", "2026-10-22 15:00"]}
C_MOVE = {"cal": 0.32, "p75": 0.614}  # calibrated on Train with dollar-adjusted signals

_G: dict = {}


def _init():
    from wallex_agent.backtest.runner import build_universe
    from wallex_agent.config import load_config
    from wallex_agent.data.market_view import HistoricalMarketView
    from wallex_agent.data.snapshot import load_snapshot

    cfg = load_config(apply_env=False)
    candles, meta_path, _ = load_snapshot(SNAP)
    _G.update(cfg=cfg, view=HistoricalMarketView(candles), uni=build_universe(cfg, meta_path, candles))


def grid() -> list[dict]:
    out = []
    for k_atr, t1r, rr, cm, hold, t3 in itertools.product([1.5, 2.0], [2.0, 3.0], ["spec", "relaxed"],
                                                          ["cal", "p75"], [24, 48], [True, False]):
        out.append({"k_atr": k_atr, "t1_target_r": t1r, "rr": rr, "c_move": cm, "hold_h": hold, "t3": t3})
    return out


def overrides(p: dict) -> dict:
    rr = {"spec": (1.6, 2.0, 1.0), "relaxed": (1.0, 1.2, 0.8)}[p["rr"]]
    hold = p["hold_h"]
    hs = [2, 4, 6, 8, 12, 24] + ([36, 48] if hold > 24 else [])
    return {"stops.k_atr": p["k_atr"], "setups.T1_pullback.target_r": p["t1_target_r"],
            "setups.T2_breakout.target_r": p["t1_target_r"] + 0.5,
            "setups.T1_pullback.rr_min": rr[0], "setups.T2_breakout.rr_min": rr[1], "setups.T3_meanrev.rr_min": rr[2],
            "fee_gate.c_move": C_MOVE[p["c_move"]], "fee_gate.horizons_h": hs,
            "setups.T1_pullback.h_max": hold, "setups.T2_breakout.h_max": hold,
            "exits.time_exit_t12_max_bars": hold, "setups.T3_meanrev.enabled": p["t3"]}


def evaluate(args) -> dict:
    p, period, offsets = args
    from wallex_agent.backtest.suite import episodes, run_episodes, with_overrides
    from wallex_agent.engine.features import FeatureProvider

    cfg = with_overrides(_G["cfg"], **overrides(p))
    s, e = (int(pd.Timestamp(x, tz="UTC").timestamp()) for x in SPLIT[period])
    eps = episodes(s, e, 72, offsets)
    rows, trades = run_episodes(cfg, _G["view"], _G["uni"], FeatureProvider(cfg), eps)
    r = np.array([x["return_pct"] for x in rows])
    dd = np.array([x["max_drawdown_pct"] for x in rows])
    pnl = [t["pnl_tmn"] for t in trades]
    return {**p, "period": period, "episodes": len(rows), "trades": len(trades),
            "mean_ret_%": round(float(r.mean()), 4), "median_ret_%": round(float(np.median(r)), 4),
            "p10_%": round(float(np.percentile(r, 10)), 4), "positive_eps_%": round(100 * float((r > 0).mean()), 1),
            "mean_maxdd_%": round(float(dd.mean()), 4), "win_rate_%": round(100 * float(np.mean([x > 0 for x in pnl])), 1)
            if pnl else 0.0, "sum_pnl_tmn": round(sum(pnl)), "kills": int(sum(x["killed"] for x in rows)),
            "score": round(float(r.mean() - 0.5 * dd.mean()), 4)}


def run(tasks, workers):
    with ProcessPoolExecutor(max_workers=workers, initializer=_init) as ex:
        res = []
        for k, r in enumerate(ex.map(evaluate, tasks), 1):
            res.append(r)
            print(f"[{k}/{len(tasks)}] {json.dumps(r)}", flush=True)
    return pd.DataFrame(res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    keys = ["k_atr", "t1_target_r", "rr", "c_move", "hold_h", "t3"]
    if a.stage == "stage1":
        df = run([(p, "train", [0, 12]) for p in grid()], a.workers)
        df.sort_values("score", ascending=False).to_csv(OUT / "opt_stage1.csv", index=False)
    elif a.stage == "stage2":
        s1 = pd.read_csv(OUT / "opt_stage1.csv")
        cand = s1[(s1["trades"] >= 20) & (s1["mean_ret_%"] > 0)].head(a.top)
        params = [{k: (bool(r[k]) if k == "t3" else r[k]) for k in keys} for _, r in cand.iterrows()]
        params = [{**p, "k_atr": float(p["k_atr"]), "t1_target_r": float(p["t1_target_r"]), "hold_h": int(p["hold_h"])}
                  for p in params]
        df = run([(p, "validation", [0, 6, 12, 18]) for p in params], a.workers)
        df.sort_values("score", ascending=False).to_csv(OUT / "opt_stage2.csv", index=False)
    elif a.stage == "stage3":
        s2 = pd.read_csv(OUT / "opt_stage2.csv")
        best = s2.iloc[0]
        p = {k: (bool(best[k]) if k == "t3" else best[k]) for k in keys}
        p = {**p, "k_atr": float(p["k_atr"]), "t1_target_r": float(p["t1_target_r"]), "hold_h": int(p["hold_h"])}
        df = run([(p, "test", [0, 6, 12, 18])], 1)
        df.to_csv(OUT / "opt_stage3.csv", index=False)
        (OUT / "opt_chosen_overrides.json").write_text(json.dumps(overrides(p), indent=1))


if __name__ == "__main__":
    main()
