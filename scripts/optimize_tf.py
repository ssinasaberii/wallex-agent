"""Timeframe study (team request 2026-10-22): different (regime, trigger) timeframes, holding not limited
to the 72-hour challenge. Research basis: daily time-series momentum / Donchian breakout with ATR trailing
stops is the best documented crypto edge net of fees (Zarattini et al. 2025; Han et al.; HSE).

Evaluation unit: 30-day episodes (two start offsets 15 days apart), 5,000,000 TMN each, all risk limits
unchanged (kill switch per episode), everything sold at episode end. Strict split on the 3-year snapshot:
first 365 days warm-up (daily regime needs 360 bars), then Train 60 % / Validation 20 % / Test 20 %.

  stage1  grid on Train          stage2  top configs on Validation          stage3  chosen config on Test once
Usage: python -I scripts/optimize_tf.py stage1|stage2|stage3 --snapshot <hash> [--workers 18]
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
DAY, H1 = 86400, 3600
EP_HOURS = 30 * 24
OFFSETS = [0, 15 * 24]

TF = {  # (regime_hours, trigger_hours): horizons (h), max hold (h), max stop % (2 x ATR grows with the bar)
    "4h/1h": (4, 1, [2, 4, 6, 8, 12, 24, 48], 48, 4.0),
    "1D/4h": (24, 4, [8, 12, 24, 48, 96, 168], 168, 8.0),
    "1D/1D": (24, 24, [48, 96, 168, 336, 504], 504, 15.0),
}
_G: dict = {}


def _init(snapshot):
    from wallex_agent.backtest.runner import build_universe
    from wallex_agent.config import load_config
    from wallex_agent.data.market_view import HistoricalMarketView
    from wallex_agent.data.snapshot import load_snapshot

    cfg = load_config(apply_env=False)
    candles, meta_path, _ = load_snapshot(snapshot)
    t_first = min(int(d["open_time"].min()) for s, d in candles.items() if s in ("BTCTMN", "BTCUSDT", "USDTTMN"))
    t_last = max(int(d["close_time"].max()) for d in candles.values())
    usable = (t_first // DAY + 366) * DAY
    span = t_last - usable
    tr = usable + int(span * 0.6) // DAY * DAY
    va = tr + int(span * 0.2) // DAY * DAY
    _G.update(cfg=cfg, view=HistoricalMarketView(candles), uni=build_universe(cfg, meta_path, candles),
              split={"train": (usable, tr), "validation": (tr, va), "test": (va, t_last)})


def grid() -> list[dict]:
    out = []
    for tf, entry, trail, gate, cm in itertools.product(TF, ["trend", "all"], [2.0, 3.0], [True, False],
                                                        ["p50", "p75"]):
        if not gate and cm == "p75":
            continue  # c_move is irrelevant without the gate
        out.append({"tf": tf, "entry": entry, "trail": trail, "gate": gate, "c_move": cm})
    return out


def overrides(p: dict, cal: dict) -> dict:
    reg_h, trig_h, hs, hold, max_stop = TF[p["tf"]]
    o = {"data.regime_hours": reg_h, "data.trigger_hours": trig_h, "fee_gate.horizons_h": hs,
         "stops.max_stop_pct": max_stop,
         "setups.T1_pullback.h_max": hold, "setups.T2_breakout.h_max": hold, "setups.T3_meanrev.h_max": hold,
         "exits.time_exit_t12_max_bars": hold, "exits.time_exit_t3_bars": hold,
         "exits.trail_atr_mult": p["trail"], "fee_gate.enabled": p["gate"]}
    if p["entry"] == "trend":  # Donchian breakout in an uptrend + ATR trailing stop (Turtle-like)
        o.update({"setups.T1_pullback.enabled": False, "setups.T3_meanrev.enabled": False,
                  "setups.T2_breakout.relvol_min": 0.0, "setups.gray_zone.enabled": False})
    if p["gate"]:
        o["fee_gate.c_move"] = cal[p["tf"]][p["c_move"]]
    return o


def calibrate(args):
    tf, = args
    from wallex_agent.backtest.suite import calibrate_c_move, with_overrides
    from wallex_agent.engine.features import FeatureProvider

    reg_h, trig_h, hs, hold, max_stop = TF[tf]
    cfg = with_overrides(_G["cfg"], **{"data.regime_hours": reg_h, "data.trigger_hours": trig_h,
                                       "fee_gate.horizons_h": hs, "stops.max_stop_pct": max_stop})
    s, e = _G["split"]["train"]
    r = calibrate_c_move(cfg, _G["view"], _G["uni"], FeatureProvider(cfg), s, e)
    return tf, {"p50": r["c_move"], "p75": r.get("p75", r["c_move"]), "n": r["n_setups"]}


def evaluate(args) -> dict:
    p, period, cal = args
    from wallex_agent.backtest.suite import episodes, run_episodes, with_overrides
    from wallex_agent.engine.features import FeatureProvider

    cfg = with_overrides(_G["cfg"], **overrides(p, cal))
    s, e = _G["split"][period]
    eps = episodes(s, e, EP_HOURS, OFFSETS)
    rows, trades = run_episodes(cfg, _G["view"], _G["uni"], FeatureProvider(cfg), eps)
    r = np.array([x["return_pct"] for x in rows]) if rows else np.array([0.0])
    dd = np.array([x["max_drawdown_pct"] for x in rows]) if rows else np.array([0.0])
    pnl = [t["pnl_tmn"] for t in trades]
    gw = sum(x for x in pnl if x > 0)
    gl = -sum(x for x in pnl if x <= 0)
    return {**p, "period": period, "episodes": len(rows), "trades": len(trades),
            "mean_ret_%": round(float(r.mean()), 4), "median_ret_%": round(float(np.median(r)), 4),
            "p10_%": round(float(np.percentile(r, 10)), 4), "positive_eps_%": round(100 * float((r > 0).mean()), 1),
            "mean_maxdd_%": round(float(dd.mean()), 4),
            "win_rate_%": round(100 * float(np.mean([x > 0 for x in pnl])), 1) if pnl else 0.0,
            "profit_factor": round(gw / gl, 3) if gl > 0 else (99.0 if gw > 0 else 0.0),
            "sum_pnl_tmn": round(sum(pnl)), "kills": int(sum(x["killed"] for x in rows)),
            "score": round(float(r.mean() - 0.5 * dd.mean()), 4)}


def run(fn, tasks, workers, snapshot):
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(snapshot,)) as ex:
        res = []
        for k, r in enumerate(ex.map(fn, tasks), 1):
            res.append(r)
            print(f"[{k}/{len(tasks)}] {json.dumps(r, default=str)}", flush=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--workers", type=int, default=18)
    ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    cal_path = OUT / "tf_calibration.json"
    keys = ["tf", "entry", "trail", "gate", "c_move"]

    def params(df):
        return [{"tf": r["tf"], "entry": r["entry"], "trail": float(r["trail"]), "gate": bool(r["gate"]),
                 "c_move": r["c_move"]} for _, r in df.iterrows()]

    if a.stage == "stage1":
        cal = dict(run(calibrate, [(tf,) for tf in TF], len(TF), a.snapshot))
        cal_path.write_text(json.dumps(cal, indent=1))
        res = run(evaluate, [(p, "train", cal) for p in grid()], a.workers, a.snapshot)
        pd.DataFrame(res).sort_values("score", ascending=False).to_csv(OUT / "tf_stage1.csv", index=False)
    elif a.stage == "stage2":
        cal = json.loads(cal_path.read_text())
        s1 = pd.read_csv(OUT / "tf_stage1.csv")
        cand = s1[(s1["trades"] >= 10) & (s1["mean_ret_%"] > 0)].head(a.top)
        res = run(evaluate, [(p, "validation", cal) for p in params(cand)], a.workers, a.snapshot)
        pd.DataFrame(res).sort_values("score", ascending=False).to_csv(OUT / "tf_stage2.csv", index=False)
    elif a.stage == "stage3":
        cal = json.loads(cal_path.read_text())
        s2 = pd.read_csv(OUT / "tf_stage2.csv")
        best = params(s2.head(1))[0]
        res = run(evaluate, [(best, "test", cal)], 1, a.snapshot)
        pd.DataFrame(res).to_csv(OUT / "tf_stage3.csv", index=False)
        (OUT / "tf_chosen.json").write_text(json.dumps({"params": best, "overrides": overrides(best, cal)},
                                                       indent=1))
    _ = keys


if __name__ == "__main__":
    main()
