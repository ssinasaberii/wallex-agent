"""Capital deployment study (HANDOFF step 2-A, 2026-10-23): v1.4 invests on average only 3.7 % of capital.
Which sizing / breadth levers put more capital to work without losing control of risk?

Levers (config only; decision logic unchanged): risk per trade (open-risk cap = 4 x risk), max position %,
daily loss stop, universe size, candidates per cycle, gray-zone policy. Kill switch 3 % with daily re-arm,
100,000 TMN minimum, 10 slots, one position per coin and no pyramiding stay fixed.

Snapshot ecbb7bb14b3ec827, same split as optimize_tf.py, 30-day windows (two offsets).
  stage1  full grid on Train
  stage2  best 4 configs per risk level (that pass the guards on Train, duplicates removed) on Validation
  stage3  current + best config per risk level on Validation: Test windows once, plus one continuous 5M TMN account over
          Validation+Test with period_stats (daily/weekly/monthly, capital in positions) and USDT/BTC hold
Score = mean window return - 0.5 x mean window max drawdown; guards: worst window >= -3 %, kill trips <= 2.
Usage: python -I scripts/exp_capital.py stage1|stage2|stage3 [--workers 30]
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

SNAP = "ecbb7bb14b3ec827"
OUT = ROOT / "reports" / "experiments"
KEYS = ["risk", "pos", "daily", "topn", "cands", "gray"]
CURRENT = {"risk": 0.25, "pos": 15.0, "daily": 1.5, "topn": 25, "cands": 3, "gray": "reject"}
WORST_MIN, MAX_KILLS = -3.0, 2


def grid() -> list[dict]:
    return [dict(zip(KEYS, v)) for v in itertools.product([0.25, 0.5, 0.75, 1.0], [15.0, 25.0, 35.0], [1.5, 3.0],
                                                          [25, 40], [3, 5], ["reject", "approve"])]


def overrides(p: dict) -> dict:
    return {"risk.risk_per_trade_pct": p["risk"], "risk.max_open_risk_pct": 4 * p["risk"],
            "risk.max_position_pct": p["pos"], "risk.max_daily_loss_pct": p["daily"],
            "universe.top_n_by_volume": int(p["topn"]), "ranking.max_candidates_per_cycle": int(p["cands"]),
            "judge.baseline_gray_policy": p["gray"]}


def _g():
    import optimize_tf as o

    if not o._G:
        o._init(SNAP)
    return o._G


def evaluate(args) -> dict:
    p, period = args
    from wallex_agent.backtest.suite import episodes, run_episodes, with_overrides
    from wallex_agent.engine.features import FeatureProvider

    G = _g()
    cfg = with_overrides(G["cfg"], **overrides(p))
    s, e = G["split"][period]
    rows, trades = run_episodes(cfg, G["view"], G["uni"], FeatureProvider(cfg), episodes(s, e, 720, [0, 360]))
    r = np.array([x["return_pct"] for x in rows])
    dd = np.array([x["max_drawdown_pct"] for x in rows])
    pnl = [t["pnl_tmn"] for t in trades]
    gw, gl = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x <= 0)
    return {**p, "period": period, "windows": len(rows), "trades": len(trades),
            "mean_ret_%": round(float(r.mean()), 4), "median_ret_%": round(float(np.median(r)), 4),
            "worst_window_%": round(float(r.min()), 4), "positive_%": round(100 * float((r > 0).mean()), 1),
            "mean_maxdd_%": round(float(dd.mean()), 4), "worst_maxdd_%": round(float(dd.max()), 4),
            "profit_factor": round(gw / gl, 3) if gl > 0 else (99.0 if gw > 0 else 0.0),
            "kills": int(sum(x["killed"] for x in rows)), "score": round(float(r.mean() - 0.5 * dd.mean()), 4)}


def continuous(p: dict) -> dict:
    """One 5M TMN account over Validation+Test, like the official backtest's continuous run."""
    import period_stats
    from wallex_agent.backtest.metrics import max_drawdown
    from wallex_agent.backtest.runner import run_period
    from wallex_agent.backtest.suite import with_overrides
    from wallex_agent.engine.features import FeatureProvider

    G = _g()
    cfg = with_overrides(G["cfg"], **overrides(p))
    s, e = G["split"]["validation"][0], G["split"]["test"][1]
    r = run_period(cfg, G["view"], G["uni"], s, e, features=FeatureProvider(cfg))
    eq = pd.Series([x for _, x in r.equity], index=pd.to_datetime([t for t, _ in r.equity], unit="s", utc=True))
    st = period_stats.compute(eq, pd.DataFrame(r.trades), "")
    pnl = [t["pnl_tmn"] for t in r.trades]
    gw, gl = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x <= 0)
    st.update(params=p, trades=len(r.trades), max_drawdown_pct=100 * max_drawdown(eq.values),
              kill_switch_trips=r.kill_count, fees_tmn=r.fees_paid,
              win_rate_pct=100 * float(np.mean([x > 0 for x in pnl])) if pnl else 0.0,
              profit_factor=gw / gl if gl > 0 else None)
    return st


def hold(start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Buy-and-hold USDT and BTC (TMN pairs) over the same span: total and mean monthly return."""
    from wallex_agent.data.snapshot import load_snapshot

    candles, _, _ = load_snapshot(SNAP)
    out = {}
    for sym in ("USDTTMN", "BTCTMN"):
        d = candles[sym]
        c = pd.Series(d["close"].values, index=pd.to_datetime(d["open_time"], unit="s", utc=True)).loc[start:end]
        m = c.resample("MS").last()
        mr = 100 * (m / m.shift(1) - 1)
        mr.iloc[0] = 100 * (m.iloc[0] / c.iloc[0] - 1)
        out[sym] = {"total_return_pct": 100 * (c.iloc[-1] / c.iloc[0] - 1), "monthly_mean_pct": float(mr.mean()),
                    "worst_month_pct": float(mr.min()),
                    "max_drawdown_pct": float(100 * (1 - c / c.cummax()).max())}
    return out


def run(fn, tasks, workers):
    with ProcessPoolExecutor(max_workers=workers) as ex:
        res = []
        for k, r in enumerate(ex.map(fn, tasks), 1):
            res.append(r)
            print(f"[{k}/{len(tasks)}] {json.dumps(r, default=str)[:300]}", flush=True)
    return res


def ok(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["worst_window_%"] >= WORST_MIN) & (df["kills"] <= MAX_KILLS) & (df["trades"] >= 10)]


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Configs with identical results differ only in levers that never bind; keep the simplest one."""
    res = ["trades", "mean_ret_%", "mean_maxdd_%", "worst_window_%", "kills"]
    return (df.sort_values(["pos", "daily", "topn", "cands"]).drop_duplicates(["risk"] + res)
            .sort_values("score", ascending=False))


def per_risk(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Best n configs at each risk level, so the report shows the whole risk / return frontier."""
    return df.groupby("risk", sort=False).head(n)


def params(df: pd.DataFrame) -> list[dict]:
    return [{"risk": float(r["risk"]), "pos": float(r["pos"]), "daily": float(r["daily"]), "topn": int(r["topn"]),
             "cands": int(r["cands"]), "gray": r["gray"]} for _, r in df.iterrows()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("--workers", type=int, default=30)
    ap.add_argument("--top", type=int, default=4)
    ap.add_argument("--chosen", type=int, default=6)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.stage == "stage1":
        res = run(evaluate, [(p, "train") for p in grid()], a.workers)
        pd.DataFrame(res).sort_values("score", ascending=False).to_csv(OUT / "capital_stage1.csv", index=False)
    elif a.stage == "stage2":
        s1 = pd.read_csv(OUT / "capital_stage1.csv")
        cand = params(per_risk(ok(dedupe(s1)), a.top))
        if CURRENT not in cand:
            cand.append(CURRENT)
        res = run(evaluate, [(p, "validation") for p in cand], a.workers)
        pd.DataFrame(res).sort_values("score", ascending=False).to_csv(OUT / "capital_stage2.csv", index=False)
    elif a.stage == "stage3":
        s2 = pd.read_csv(OUT / "capital_stage2.csv")
        chosen = [p for p in params(per_risk(ok(dedupe(s2)), 1)) if p != CURRENT][: a.chosen]
        # information only: best Train config at the highest risk level, which failed the Train guards
        s1 = dedupe(pd.read_csv(OUT / "capital_stage1.csv"))
        info = [p for p in params(s1[s1["risk"] == s1["risk"].max()].head(1)) if p not in chosen]
        sel = [CURRENT] + chosen + info
        res = run(evaluate, [(p, "test") for p in sel], a.workers)
        pd.DataFrame(res).to_csv(OUT / "capital_stage3.csv", index=False)
        cont = run(continuous, sel, a.workers)
        bench = hold(pd.Timestamp(cont[0]["start"]), pd.Timestamp(cont[0]["end"]))
        (OUT / "capital_continuous.json").write_text(json.dumps({"hold": bench, "runs": cont}, indent=1,
                                                                default=float))
        print(json.dumps(bench, indent=1))


if __name__ == "__main__":
    main()
