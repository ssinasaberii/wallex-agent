"""Universe selection per cycle (09 §7.1) and cross-sectional ranking (09 §7.6)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from wallex_agent.data.quality import passes_quality
from wallex_agent.strategy.triggers import T3


def volume_24h_tmn(bars_1h: pd.DataFrame, t: int) -> float:
    w = bars_1h[bars_1h["close_time"] > t - 24 * 3600]
    return float((w["close"] * w["volume"]).sum())


def ret_24h(bars_1h: pd.DataFrame, t: int) -> float | None:
    if len(bars_1h) < 25 or int(bars_1h["close_time"].iloc[-1]) != t:
        return None
    past = bars_1h[bars_1h["close_time"] <= t - 24 * 3600]
    if len(past) == 0:
        return None
    return float(bars_1h["close"].iloc[-1] / past["close"].iloc[-1] - 1)


def select_universe(view, t: int, static_symbols: list[str], spread_est: dict[str, float], cfg,
                    funnel: dict) -> list[str]:
    """static_symbols: already spot+TMN+allowed+not stable/non-crypto. Returns symbols for this cycle."""
    u = cfg.universe
    rows = []
    for s in static_symbols:
        b = view.bars(s, 1)
        if len(b) == 0:
            continue
        if not passes_quality(b, t, u.data_quality_window_h, u.max_missing_pct):
            funnel["universe_quality"] = funnel.get("universe_quality", 0) + 1
            continue
        rows.append((s, volume_24h_tmn(b, t)))
    rows.sort(key=lambda r: -r[1])
    out = []
    for s, _ in rows[: u.top_n_by_volume]:
        sp = spread_est.get(s)
        if sp is None or sp > u.max_spread_pct:
            funnel["universe_spread"] = funnel.get("universe_spread", 0) + 1
            continue
        out.append(s)
    return out


def _pct(x: list[float]) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if len(a) == 1:
        return np.array([0.5])
    order = a.argsort().argsort()
    return order / (len(a) - 1)


def rank(cands: list, cfg) -> list:
    """Sets rank_score on each candidate and returns them best first."""
    if not cands:
        return []
    r = cfg.ranking
    rs = _pct([c.rank_inputs.get("rs_vs_btc") or 0.0 for c in cands])
    adx = _pct([c.rank_inputs.get("adx_4h") or 0.0 for c in cands])
    rv = _pct([c.rank_inputs.get("relvol_1h") or 0.0 for c in cands])
    for k, c in enumerate(cands):
        a = 1 - adx[k] if c.setup == T3 else adx[k]
        c.rank_score = round(float(r.w_rs_vs_btc * rs[k] + r.w_adx * a + r.w_relvol * rv[k]), 6)
    return sorted(cands, key=lambda c: (-c.rank_score, c.symbol))
