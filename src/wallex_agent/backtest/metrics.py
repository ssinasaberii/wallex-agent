"""Backtest metrics (spec 09 §9.4)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def max_drawdown(eq: np.ndarray) -> float:
    if len(eq) == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float(np.max((peak - eq) / peak)) if len(eq) else 0.0


def run_metrics(trades: list[dict], equity: list[tuple[int, float]], initial: float, fees: float) -> dict:
    eq = np.array([e for _, e in equity], dtype=float) if equity else np.array([initial])
    final = float(eq[-1])
    rets = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([0.0])
    sd = float(np.std(rets))
    tr = pd.DataFrame(trades)
    wins = tr[tr["pnl_tmn"] > 0] if len(tr) else tr
    losses = tr[tr["pnl_tmn"] <= 0] if len(tr) else tr
    gross_win = float(wins["pnl_tmn"].sum()) if len(tr) else 0.0
    gross_loss = float(-losses["pnl_tmn"].sum()) if len(tr) else 0.0
    return {
        "return_pct": 100 * (final / initial - 1),
        "max_drawdown_pct": 100 * max_drawdown(eq),
        "sharpe_hourly_annualized": (float(np.mean(rets)) / sd * math.sqrt(24 * 365)) if sd > 0 else 0.0,
        "trades": len(tr),
        "win_rate_pct": 100 * len(wins) / len(tr) if len(tr) else 0.0,
        "avg_r": float(tr["r_multiple"].mean()) if len(tr) else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0),
        "avg_hold_h": float(tr["bars_held"].mean()) if len(tr) else 0.0,
        "fees_tmn": fees,
        "final_equity_tmn": final,
    }


def episode_stats(rows: list[dict]) -> dict:
    if not rows:
        return {}
    r = np.array([x["return_pct"] for x in rows])
    dd = np.array([x["max_drawdown_pct"] for x in rows])
    return {"episodes": len(rows), "mean_return_pct": float(np.mean(r)), "median_return_pct": float(np.median(r)),
            "p10_return_pct": float(np.percentile(r, 10)),
            "p90_return_pct": float(np.percentile(r, 90)), "positive_pct": 100 * float(np.mean(r > 0)),
            "zero_trade_pct": 100 * float(np.mean([x["trades"] == 0 for x in rows])),
            "median_maxdd_pct": float(np.median(dd)), "p90_maxdd_pct": float(np.percentile(dd, 90)),
            "kill_switch_episodes": int(sum(1 for x in rows if x.get("killed"))),
            "total_trades": int(sum(x["trades"] for x in rows))}
