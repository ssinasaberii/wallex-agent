"""Technical indicators (spec 09 §6). All are causal: the value at bar i uses bars <= i only.

Conventions (fixed, documented for the judges):
- EMA(n): alpha = 2/(n+1), recursive, seeded with the SMA of the first n values (TA-Lib convention).
- Wilder smoothing (ATR, +DI/-DI, ADX, RSI): alpha = 1/n, seeded with the SMA of the first n values.
- Bollinger uses the population standard deviation (ddof=0).
- Donchian(20) uses the 20 bars *before* the current bar.
- Values before a series has enough data are NaN; the strategy treats NaN as UNKNOWN.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _seeded_recursive(x: np.ndarray, n: int, alpha: float) -> np.ndarray:
    """y[n-1+k] from SMA seed of the first n *valid* values, then y = y_prev + alpha*(x - y_prev)."""
    out = np.full(len(x), np.nan)
    valid = np.flatnonzero(~np.isnan(x))
    if len(valid) < n:
        return out
    start = valid[0]
    if np.isnan(x[start:start + n]).any():  # require a contiguous seed window
        return out
    seed_end = start + n - 1
    y = float(np.mean(x[start:seed_end + 1]))
    out[seed_end] = y
    for i in range(seed_end + 1, len(x)):
        xi = x[i]
        if np.isnan(xi):
            out[i] = np.nan
            continue
        y = y + alpha * (xi - y)
        out[i] = y
    return out


def ema(s: pd.Series, n: int) -> pd.Series:
    return pd.Series(_seeded_recursive(s.to_numpy(dtype=float), n, 2.0 / (n + 1)), index=s.index)


def wilder(s: pd.Series, n: int) -> pd.Series:
    return pd.Series(_seeded_recursive(s.to_numpy(dtype=float), n, 1.0 / n), index=s.index)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
                   axis=1).max(axis=1, skipna=False)
    if len(df):
        tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def adx_di(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    plus_dm.iloc[0] = minus_dm.iloc[0] = np.nan  # undefined on the first bar
    tr = true_range(df)
    tr.iloc[0] = np.nan
    atr_w = wilder(tr, n)
    pdi = 100.0 * wilder(plus_dm, n) / atr_w
    mdi = 100.0 * wilder(minus_dm, n) / atr_w
    denom = (pdi + mdi).replace(0.0, np.nan)
    dx = 100.0 * (pdi - mdi).abs() / denom
    return pd.DataFrame({"pdi": pdi, "mdi": mdi, "adx": wilder(dx, n)}, index=df.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0.0)
    loss = (-d).clip(lower=0.0)
    ag, al = wilder(gain, n), wilder(loss, n)
    rs = ag / al
    out = 100.0 - 100.0 / (1.0 + rs)
    out[(al == 0) & (ag > 0)] = 100.0
    out[(al == 0) & (ag == 0)] = 50.0
    return out


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(n, min_periods=n).mean()
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": mid + k * sd, "bb_lower": mid - k * sd}, index=close.index)


def donchian(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    return pd.DataFrame({
        "donchian_high": df["high"].rolling(n, min_periods=n).max().shift(1),
        "donchian_low": df["low"].rolling(n, min_periods=n).min().shift(1),
    }, index=df.index)


def relvol(volume: pd.Series, n: int = 20) -> pd.Series:
    base = volume.rolling(n, min_periods=n).mean().shift(1)
    return volume / base.replace(0.0, np.nan)


def chaikin_ad(df: pd.DataFrame) -> pd.Series:
    hl = df["high"] - df["low"]
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl.replace(0.0, np.nan)
    return (mfm.fillna(0.0) * df["volume"]).cumsum()


def pct_rank(s: pd.Series, window: int) -> pd.Series:
    """Percent of the last `window` values (current included) that are <= the current value, in 0..100.
    NaN until a full window of valid values exists."""
    x = s.to_numpy(dtype=float)
    out = np.full(len(x), np.nan)
    for i in range(window - 1, len(x)):
        w = x[i - window + 1:i + 1]
        if np.isnan(w).any():
            continue
        out[i] = 100.0 * np.count_nonzero(w <= x[i]) / window
    return pd.Series(out, index=s.index)


def regime_features(df4h: pd.DataFrame, p) -> pd.DataFrame:
    """Features on closed regime-timeframe bars. `p` is config.indicators."""
    c = df4h["close"]
    out = pd.DataFrame(index=df4h.index)
    out["ema20"] = ema(c, p.ema_fast)
    out["ema50"] = ema(c, p.ema_slow)
    out["slope"] = out["ema50"] - out["ema50"].shift(p.ema_slope_lookback)
    out["atr"] = atr(df4h, p.atr_period)
    out["atr_pct"] = out["atr"] / c
    out["atr_pct_rank"] = pct_rank(out["atr_pct"], p.atr_pct_rank_window)
    out = out.join(adx_di(df4h, p.adx_period))
    return out


def trigger_features(df1h: pd.DataFrame, p) -> pd.DataFrame:
    """Features on closed trigger-timeframe bars."""
    c = df1h["close"]
    out = pd.DataFrame(index=df1h.index)
    out["ema20"] = ema(c, p.ema_fast)
    out["atr"] = atr(df1h, p.atr_period)
    out["atr_pct"] = out["atr"] / c
    out["rsi"] = rsi(c, p.rsi_period)
    out = out.join(bollinger(c, p.bb_period, p.bb_std))
    out = out.join(donchian(df1h, p.donchian_period))
    out["relvol"] = relvol(df1h["volume"], p.relvol_period)
    ad = chaikin_ad(df1h)
    out["ad_slope"] = ad - ad.shift(p.ad_slope_bars)
    return out
