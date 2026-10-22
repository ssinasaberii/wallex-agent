"""Acceptance test 3 (09 §13): indicators match reference values.

References are independent, loop-based textbook implementations (Wilder 1978 for ATR/DMI/ADX/RSI,
Bollinger with population sigma, Donchian over the previous n bars), plus analytic cases whose
answer is known in closed form.
"""

import math

import numpy as np
import pandas as pd
import pytest

from wallex_agent.features import indicators as ind

RTOL = 1e-9


# ---------------------------------------------------------------- reference implementations
def ref_ema(x, n):
    a = 2 / (n + 1)
    out = [math.nan] * len(x)
    y = sum(x[:n]) / n
    out[n - 1] = y
    for i in range(n, len(x)):
        y = a * x[i] + (1 - a) * y
        out[i] = y
    return out


def ref_wilder_from(vals, n, first):
    """Wilder smoothing of vals[first:], seed = mean of the first n, then (prev*(n-1)+x)/n."""
    out = [math.nan] * len(vals)
    y = sum(vals[first:first + n]) / n
    out[first + n - 1] = y
    for i in range(first + n, len(vals)):
        y = (y * (n - 1) + vals[i]) / n
        out[i] = y
    return out


def ref_tr(h, lo, c):
    tr = [h[0] - lo[0]]
    for i in range(1, len(c)):
        tr.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
    return tr


def ref_rsi(c, n):
    gains = [math.nan] + [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    losses = [math.nan] + [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag, al = ref_wilder_from(gains, n, 1), ref_wilder_from(losses, n, 1)
    return [math.nan if math.isnan(g) else (100 if lo_ == 0 else 100 - 100 / (1 + g / lo_))
            for g, lo_ in zip(ag, al, strict=True)]


def ref_dmi(h, lo, c, n):
    pdm, mdm, tr = [math.nan], [math.nan], [math.nan]
    for i in range(1, len(c)):
        up, dn = h[i] - h[i - 1], lo[i - 1] - lo[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        tr.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
    s_tr, s_p, s_m = (ref_wilder_from(v, n, 1) for v in (tr, pdm, mdm))
    pdi = [100 * p / t if not math.isnan(t) else math.nan for p, t in zip(s_p, s_tr, strict=True)]
    mdi = [100 * m / t if not math.isnan(t) else math.nan for m, t in zip(s_m, s_tr, strict=True)]
    dx = [100 * abs(p - m) / (p + m) if not math.isnan(p) else math.nan for p, m in zip(pdi, mdi, strict=True)]
    adx = ref_wilder_from(dx, n, n)  # first DX exists at index n
    return pdi, mdi, adx


def close_eq(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    assert a.shape == b.shape
    assert (np.isnan(a) == np.isnan(b)).all(), "NaN pattern differs"
    m = ~np.isnan(a)
    np.testing.assert_allclose(a[m], b[m], rtol=RTOL)


# ---------------------------------------------------------------- against references
def test_ema_matches_reference(ohlcv):
    for n in (20, 50):
        close_eq(ind.ema(ohlcv["close"], n), ref_ema(ohlcv["close"].tolist(), n))


def test_atr_matches_wilder_reference(ohlcv):
    tr = ref_tr(ohlcv["high"].tolist(), ohlcv["low"].tolist(), ohlcv["close"].tolist())
    close_eq(ind.atr(ohlcv, 14), ref_wilder_from(tr, 14, 0))


def test_rsi_matches_wilder_reference(ohlcv):
    close_eq(ind.rsi(ohlcv["close"], 14), ref_rsi(ohlcv["close"].tolist(), 14))


def test_dmi_adx_match_wilder_reference(ohlcv):
    pdi, mdi, adx = ref_dmi(ohlcv["high"].tolist(), ohlcv["low"].tolist(), ohlcv["close"].tolist(), 14)
    got = ind.adx_di(ohlcv, 14)
    close_eq(got["pdi"], pdi)
    close_eq(got["mdi"], mdi)
    close_eq(got["adx"], adx)


def test_bollinger_population_sigma(ohlcv):
    c = ohlcv["close"].to_numpy()
    bb = ind.bollinger(ohlcv["close"], 20, 2.0)
    for i in (19, 500, len(c) - 1):
        w = c[i - 19:i + 1]
        assert bb["bb_mid"].iloc[i] == pytest.approx(w.mean(), rel=RTOL)
        assert bb["bb_lower"].iloc[i] == pytest.approx(w.mean() - 2 * w.std(ddof=0), rel=RTOL)
    assert bb["bb_mid"].iloc[:19].isna().all()


def test_donchian_excludes_current_bar(ohlcv):
    d = ind.donchian(ohlcv, 20)
    h = ohlcv["high"].to_numpy()
    for i in (20, 700, len(h) - 1):
        assert d["donchian_high"].iloc[i] == h[i - 20:i].max()
    assert d["donchian_high"].iloc[:20].isna().all()
    # A new high on the current bar must not raise its own Donchian level.
    df = ohlcv.copy()
    df.loc[100, "high"] = df["high"].max() * 2
    assert ind.donchian(df, 20)["donchian_high"].iloc[100] == d["donchian_high"].iloc[100]


def test_relvol_uses_previous_bars(ohlcv):
    rv = ind.relvol(ohlcv["volume"], 20)
    v = ohlcv["volume"].to_numpy()
    for i in (20, 999):
        assert rv.iloc[i] == pytest.approx(v[i] / v[i - 20:i].mean(), rel=RTOL)


def test_chaikin_ad_zero_range_bar_contributes_zero():
    df = pd.DataFrame({"high": [10, 12, 11], "low": [8, 12, 9], "close": [9.5, 12, 10.5], "volume": [100, 50, 10]},
                      dtype=float)
    ad = ind.chaikin_ad(df)
    assert ad.iloc[0] == pytest.approx(((9.5 - 8) - (10 - 9.5)) / 2 * 100)
    assert ad.iloc[1] == ad.iloc[0]  # H == L -> 0
    assert ad.iloc[2] == pytest.approx(ad.iloc[1] + ((10.5 - 9) - (11 - 10.5)) / 2 * 10)


def test_pct_rank_window():
    s = pd.Series(np.arange(10, dtype=float))
    r = ind.pct_rank(s, 5)
    assert r.iloc[:4].isna().all()
    assert r.iloc[4] == 100.0  # current value is the max of its window
    s2 = pd.Series([5, 4, 3, 2, 1, 0], dtype=float)
    assert ind.pct_rank(s2, 5).iloc[-1] == 20.0  # only itself is <= the minimum


# ---------------------------------------------------------------- analytic cases
def test_constant_series_analytics():
    n = 100
    df = pd.DataFrame({"open": [50.0] * n, "high": [51.0] * n, "low": [49.0] * n, "close": [50.0] * n,
                       "volume": [10.0] * n})
    assert ind.ema(df["close"], 20).dropna().eq(50.0).all()
    assert ind.atr(df, 14).dropna().eq(2.0).all()  # TR = H - L
    assert ind.rsi(df["close"], 14).dropna().eq(50.0).all()  # no gains, no losses
    assert ind.relvol(df["volume"], 20).dropna().eq(1.0).all()
    assert ind.bollinger(df["close"]).dropna()["bb_upper"].eq(50.0).all()


def test_linear_ramp_ema_lag_and_rsi():
    x = pd.Series(np.arange(400, dtype=float))
    n = 20
    e = ind.ema(x, n)
    # For x_t = t, a converged EMA lags by (1 - alpha) / alpha = (n - 1) / 2.
    assert e.iloc[-1] == pytest.approx(x.iloc[-1] - (n - 1) / 2, rel=1e-9)
    assert ind.rsi(x, 14).dropna().eq(100.0).all()


def test_strong_uptrend_dmi_direction():
    t = np.arange(300, dtype=float)
    df = pd.DataFrame({"open": t, "close": t + 0.8, "high": t + 1.0, "low": t - 0.2, "volume": 1.0})
    d = ind.adx_di(df, 14).dropna()
    assert (d["pdi"] > d["mdi"]).all()
    assert d["adx"].iloc[-1] > 90
