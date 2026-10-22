"""Exit study (team request 2026-10-23): higher take-profit, staged selling, different stops and trailing, and
(separately, information only) staged buying. v1.3 default config on the 3-year snapshot, 30-day windows,
same split as scripts/optimize_tf.py. Risk limits unchanged.

  stage1  63 exit configs on Train        stage2  top configs on Validation        stage3  chosen one on Test once
  pyramid staged buying (Article 4 forbids it; information only) on baseline and the chosen exit, all periods
Usage: python -I scripts/exp_exits.py stage1|stage2|stage3|pyramid [--workers 18]
"""

import argparse
import itertools
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = ROOT / "reports" / "experiments"
SNAP = "ecbb7bb14b3ec827"
STYLES = {
    "base (T1 2R target, rest trailing)": {},
    "T1 target 3R": {"setups.T1_pullback.target_r": 3.0},
    "no fixed target, trail only": {"exits.t1_fixed_target": False},
    "sell 50% at 1R, trail rest": {"exits.t1_fixed_target": False, "exits.scale_out": [[1.0, 0.5]]},
    "sell 50% at 2R, trail rest": {"exits.t1_fixed_target": False, "exits.scale_out": [[2.0, 0.5]]},
    "sell 1/3 at 1R and 2R, trail rest": {"exits.t1_fixed_target": False,
                                          "exits.scale_out": [[1.0, 0.33], [2.0, 0.33]]},
    "sell 1/4 at 1R, 2R, 3R, trail rest": {"exits.t1_fixed_target": False,
                                           "exits.scale_out": [[1.0, 0.25], [2.0, 0.25], [3.0, 0.25]]},
}
_G: dict = {}


def _init():
    import optimize_tf as o

    o._init(SNAP)
    _G.update(o._G)


def overrides(p):
    return {**STYLES[p["style"]], "stops.k_atr": p["k_atr"], "exits.trail_atr_mult": p["trail"],
            **({"exits.pyramid_enabled": True, "exits.pyramid_at_r": p["pyr_at"],
                "exits.pyramid_add_fraction": p["pyr_frac"]} if p.get("pyr_at") else {})}


def evaluate(args):
    p, period = args
    from wallex_agent.backtest.suite import episodes, run_episodes, with_overrides
    from wallex_agent.engine.features import FeatureProvider

    cfg = with_overrides(_G["cfg"], **overrides(p))
    s, e = _G["split"][period]
    rows, trades = run_episodes(cfg, _G["view"], _G["uni"], FeatureProvider(cfg), episodes(s, e, 720, [0, 360]))
    r = np.array([x["return_pct"] for x in rows])
    dd = np.array([x["max_drawdown_pct"] for x in rows])
    pos = pd.DataFrame(trades).groupby(["episode", "candidate_id"])["pnl_tmn"].sum() if trades else pd.Series([])
    gw, gl = float(pos[pos > 0].sum()), float(-pos[pos <= 0].sum())
    return {**p, "period": period, "windows": len(rows), "positions": int(len(pos)),
            "mean_ret_%": round(float(r.mean()), 3), "median_ret_%": round(float(np.median(r)), 3),
            "worst_window_%": round(float(r.min()), 3), "positive_%": round(100 * float((r > 0).mean())),
            "win_rate_%": round(100 * float((pos > 0).mean()), 1) if len(pos) else 0.0,
            "profit_factor": round(gw / gl, 2) if gl > 0 else None, "sum_pnl_tmn": round(float(pos.sum())),
            "mean_maxdd_%": round(float(dd.mean()), 3), "kills": int(sum(x["killed"] for x in rows)),
            "score": round(float(r.mean() - 0.5 * dd.mean()), 3)}


def run(tasks, workers):
    with ProcessPoolExecutor(max_workers=workers, initializer=_init) as ex:
        res = list(ex.map(evaluate, tasks))
    for r in res:
        print(json.dumps(r, ensure_ascii=False))
    return pd.DataFrame(res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("--workers", type=int, default=18)
    ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    keys = ["style", "k_atr", "trail"]
    if a.stage == "stage1":
        grid = [{"style": s, "k_atr": k, "trail": t} for s, k, t in itertools.product(STYLES, [1.5, 2.0, 3.0],
                                                                                        [2.0, 3.0, 4.0])]
        run([(p, "train") for p in grid], a.workers).sort_values("score", ascending=False).to_csv(
            OUT / "exits_stage1.csv", index=False)
    elif a.stage == "stage2":
        s1 = pd.read_csv(OUT / "exits_stage1.csv")
        top = s1[s1["mean_ret_%"] > 0].head(a.top)
        base = s1[(s1["style"] == list(STYLES)[0]) & (s1["k_atr"] == 2.0) & (s1["trail"] == 3.0)]
        params = [{k: (float(r[k]) if k != "style" else r[k]) for k in keys} for _, r in pd.concat([top, base])
                  .drop_duplicates(keys).iterrows()]
        run([(p, "validation") for p in params], a.workers).sort_values("score", ascending=False).to_csv(
            OUT / "exits_stage2.csv", index=False)
    elif a.stage == "stage3":
        s2 = pd.read_csv(OUT / "exits_stage2.csv")
        best = {k: (float(s2.iloc[0][k]) if k != "style" else s2.iloc[0][k]) for k in keys}
        base = {"style": list(STYLES)[0], "k_atr": 2.0, "trail": 3.0}
        tasks = [(best, "test")] + ([(base, "test")] if best != base else [])
        run(tasks, 2).to_csv(OUT / "exits_stage3.csv", index=False)
        (OUT / "exits_chosen.json").write_text(json.dumps({"params": best, "overrides": overrides(best)}, indent=1))
    elif a.stage == "pyramid":
        chosen = json.loads((OUT / "exits_chosen.json").read_text())["params"]
        base = {"style": list(STYLES)[0], "k_atr": 2.0, "trail": 3.0}
        tasks = []
        for p in (base, chosen):
            for pyr_at, frac in ((None, None), (1.0, 0.5), (1.0, 1.0), (2.0, 0.5)):
                q = {**p, "pyr_at": pyr_at, "pyr_frac": frac}
                tasks += [(q, per) for per in ("train", "validation", "test")]
        run(tasks, a.workers).to_csv(OUT / "exits_pyramid.csv", index=False)


if __name__ == "__main__":
    main()
