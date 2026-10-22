"""Regime classification on closed 4h bars (spec 09 §7.2-7.3).

Rules are checked in order; the first that holds wins:
  0 UNKNOWN   incomplete bar, missing features or insufficient warm-up
  1 HIGH_VOL  ATR% percentile >= 90
  2 DOWNTREND ADX >= 25 and EMA20 < EMA50 and -DI > +DI
  3 UPTREND   ADX >= 25 and EMA20 > EMA50 and slope(EMA50) > 0 and +DI > -DI
  4 RANGE     ADX < 20 and |slope(EMA50)| < ATR(14)
  5 GRAY      everything else
Hysteresis: the confirmed regime changes only when the new raw regime is seen on 2 consecutive bars,
except changes to HIGH_VOL, DOWNTREND or UNKNOWN, which are immediate (conservative direction).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

UNKNOWN, HIGH_VOL, DOWN, UP, RANGE, GRAY = "UNKNOWN", "HIGH_VOLATILITY", "DOWNTREND", "UPTREND", "RANGE", "GRAY"
IMMEDIATE = {UNKNOWN, HIGH_VOL, DOWN}
NEEDED = ["ema20", "ema50", "slope", "atr", "atr_pct_rank", "adx", "pdi", "mdi"]


def raw_regimes(feat: pd.DataFrame, complete: pd.Series, cfg_regime, min_bars: int) -> np.ndarray:
    n = len(feat)
    out = np.full(n, UNKNOWN, dtype=object)
    if n == 0:
        return out
    f = feat[NEEDED].to_numpy(dtype=float)
    ok = ~np.isnan(f).any(axis=1) & complete.to_numpy(dtype=bool)
    ok[: max(min_bars - 1, 0)] = False  # warm-up: need min_bars closed bars including this one
    ema20, ema50, slope, atr, rank, adx, pdi, mdi = (f[:, i] for i in range(8))
    hv = rank >= cfg_regime.high_vol_atr_pct_rank
    down = (adx >= cfg_regime.trend_adx_min) & (ema20 < ema50) & (mdi > pdi)
    up = (adx >= cfg_regime.trend_adx_min) & (ema20 > ema50) & (slope > 0) & (pdi > mdi)
    rng = (adx < cfg_regime.range_adx_max) & (np.abs(slope) < atr)
    out[:] = GRAY
    out[rng] = RANGE
    out[up] = UP
    out[down] = DOWN
    out[hv] = HIGH_VOL
    out[~ok] = UNKNOWN
    return out


def confirm(raw: np.ndarray, bars: int = 2, enabled: bool = True) -> np.ndarray:
    if not enabled:
        return raw.copy()
    out = np.empty(len(raw), dtype=object)
    confirmed = UNKNOWN
    run_regime, run_len = None, 0
    for i, r in enumerate(raw):
        run_len = run_len + 1 if r == run_regime else 1
        run_regime = r
        if r in IMMEDIATE or run_len >= bars:
            confirmed = r
        out[i] = confirmed
    return out


def regime_series(feat: pd.DataFrame, bars4h: pd.DataFrame, cfg, ) -> pd.DataFrame:
    """Raw and confirmed regime per closed 4h bar (index aligned with bars4h)."""
    raw = raw_regimes(feat, bars4h["complete"], cfg.regime, cfg.data.min_regime_bars)
    conf = confirm(raw, cfg.regime.hysteresis_bars, cfg.regime.hysteresis_enabled)
    return pd.DataFrame({"close_time": bars4h["close_time"].to_numpy(), "raw": raw, "confirmed": conf},
                        index=bars4h.index)


def regime_at(regimes: pd.DataFrame, t: int, period_s: int) -> tuple[str, int | None]:
    """Confirmed regime valid at time t: the last 4h bar with close_time <= t. If that bar is not the
    most recent bucket that should have closed by t, the data is stale -> UNKNOWN."""
    if len(regimes) == 0:
        return UNKNOWN, None
    last = regimes.iloc[-1]
    expected_close = int(t) // period_s * period_s
    if int(last["close_time"]) < expected_close:
        return UNKNOWN, len(regimes) - 1
    return str(last["confirmed"]), len(regimes) - 1
