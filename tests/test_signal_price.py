"""Dollar-adjusted signal prices (team decision Q-33)."""

import numpy as np
import pandas as pd

from wallex_agent.engine.features import dollar_adjust

from .conftest import H1, make_ohlcv


def test_adjusted_price_is_tmn_over_usdt_rate():
    df = make_ohlcv(100)
    usdt = df[["open_time", "close_time"]].copy()
    usdt["close"] = 200_000.0 + np.arange(100) * 100
    adj = dollar_adjust(df, usdt)
    np.testing.assert_allclose(adj["close"], df["close"] / usdt["close"])
    np.testing.assert_allclose(adj["high"], df["high"] / usdt["close"])


def test_missing_rate_hour_uses_previous_hour_only():
    df = make_ohlcv(10)
    usdt = df[["open_time", "close_time"]].copy()
    usdt["close"] = np.arange(1, 11, dtype=float) * 1000
    usdt = usdt.drop(index=[4]).reset_index(drop=True)  # hour 4 missing
    adj = dollar_adjust(df, usdt)
    assert adj["close"].iloc[4] == df["close"].iloc[4] / 4000.0  # hour 3's rate, never hour 5's


def test_rows_before_first_rate_are_dropped():
    df = make_ohlcv(10)
    usdt = df[["open_time", "close_time"]].iloc[3:].copy()
    usdt["close"] = 1000.0
    adj = dollar_adjust(df, usdt)
    assert len(adj) == 7 and int(adj["open_time"].iloc[0]) == int(df["open_time"].iloc[3])


def test_dollar_move_alone_creates_no_price_change():
    df = make_ohlcv(50)
    rate = pd.Series(np.linspace(200_000, 260_000, 50))
    tmn = df.copy()
    for c in ("open", "high", "low", "close"):
        tmn[c] = 10.0 * rate.to_numpy()  # coin flat in USD, toman price driven only by the dollar
    usdt = df[["open_time", "close_time"]].copy()
    usdt["close"] = rate.to_numpy()
    adj = dollar_adjust(tmn, usdt)
    assert np.allclose(adj["close"], 10.0)
    assert int(adj["open_time"].iloc[1] - adj["open_time"].iloc[0]) == H1
