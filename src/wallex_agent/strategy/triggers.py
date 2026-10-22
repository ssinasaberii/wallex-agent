"""Entry triggers on closed 1h bars (spec 09 §7.4). Pure functions of bars/features up to index i."""

from __future__ import annotations

import math

import pandas as pd

from wallex_agent.strategy.regime import GRAY, RANGE, UP

T1, T2, T3 = "T1_pullback", "T2_breakout", "T3_meanrev"


def _nan(*xs) -> bool:
    return any(x is None or (isinstance(x, float) and math.isnan(x)) for x in xs)


def t1_pullback(bars: pd.DataFrame, f: pd.DataFrame, i: int, c) -> bool:
    n = c.lookback_bars
    if i < n - 1:
        return False
    w = slice(i - n + 1, i + 1)
    low, ema20, atr, rsi = bars["low"].iloc[w], f["ema20"].iloc[w], f["atr"].iloc[w], f["rsi"].iloc[w]
    if low.isna().any() or ema20.isna().any() or atr.isna().any() or rsi.isna().any():
        return False
    touched = bool((low <= ema20 + c.touch_atr_mult * atr).any())
    rsi_ok = c.rsi_min <= float(rsi.min()) <= c.rsi_max
    close, open_ = float(bars["close"].iloc[i]), float(bars["open"].iloc[i])
    return touched and rsi_ok and close > float(f["ema20"].iloc[i]) and close > open_


def t2_breakout(bars: pd.DataFrame, f: pd.DataFrame, i: int, c) -> bool:
    dh, rv, ad = f["donchian_high"].iloc[i], f["relvol"].iloc[i], f["ad_slope"].iloc[i]
    if _nan(float(dh), float(rv)):
        return False
    ok = float(bars["close"].iloc[i]) > float(dh) and float(rv) >= c.relvol_min
    if ok and c.breakout_ad_confirm:
        ok = not _nan(float(ad)) and float(ad) > 0
    return ok


def t3_meanrev(bars: pd.DataFrame, f: pd.DataFrame, i: int, c) -> bool:
    if i < 1:
        return False
    bl0, bl1 = f["bb_lower"].iloc[i - 1], f["bb_lower"].iloc[i]
    r0, r1 = f["rsi"].iloc[i - 1], f["rsi"].iloc[i]
    if _nan(float(bl0), float(bl1), float(r0), float(r1)):
        return False
    return (float(bars["close"].iloc[i - 1]) < float(bl0) and float(bars["close"].iloc[i]) > float(bl1)
            and float(r1) < c.rsi_max and float(r1) > float(r0))


def pick_setup(regime: str, bars: pd.DataFrame, f: pd.DataFrame, i: int, setups) -> tuple[str | None, bool]:
    """Returns (setup, gray_zone). Priority T2 > T1 > T3 (one candidate per symbol)."""
    if regime == UP:
        if setups.T2_breakout.enabled and t2_breakout(bars, f, i, setups.T2_breakout):
            return T2, False
        if setups.T1_pullback.enabled and t1_pullback(bars, f, i, setups.T1_pullback):
            return T1, False
    elif regime == RANGE:
        if setups.T3_meanrev.enabled and t3_meanrev(bars, f, i, setups.T3_meanrev):
            return T3, False
    elif regime == GRAY and setups.gray_zone.enabled:
        if setups.T1_pullback.enabled and t1_pullback(bars, f, i, setups.T1_pullback):
            return T1, True
        if setups.T3_meanrev.enabled and t3_meanrev(bars, f, i, setups.T3_meanrev):
            return T3, True
    return None, False


def trigger_inputs(bars: pd.DataFrame, f: pd.DataFrame, i: int) -> dict:
    row = f.iloc[i]

    def r(x):
        x = float(x)
        return None if math.isnan(x) else round(x, 8)

    return {"close": r(bars["close"].iloc[i]), "rsi": r(row["rsi"]), "bb_lower": r(row["bb_lower"]),
            "bb_mid": r(row["bb_mid"]), "donchian_high": r(row["donchian_high"]), "relvol": r(row["relvol"]),
            "ema20_1h": r(row["ema20"]), "atr_1h": r(row["atr"]), "atr_pct_1h": r(row["atr_pct"]),
            "ad_slope": r(row["ad_slope"])}
