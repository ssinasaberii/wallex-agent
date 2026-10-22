import numpy as np
import pandas as pd
import pytest

H1 = 3600
START = 1_760_000_400 // H1 * H1


def hourly_cfg():
    """The v1.2 hourly configuration (4h regime, 1h trigger, fee gate on). Tests that exercise the fee gate or
    use the 270-day snapshot pin it, because the v1.3 default needs a year of daily warm-up."""
    from pathlib import Path

    from wallex_agent.config import load_config

    return load_config(Path(__file__).resolve().parents[1] / "config" / "v1.2_hourly.yaml", apply_env=False)


def make_ohlcv(n: int, seed: int = 7, start: int = START, gaps: tuple[int, ...] = ()) -> pd.DataFrame:
    """Synthetic 1h candles: geometric random walk with realistic wicks. `gaps` = bar indices dropped."""
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, 0.006, n)
    close = 1000.0 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[1000.0], close[:-1]]) * (1 + rng.normal(0, 0.001, n))
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    vol = rng.lognormal(3, 0.6, n)
    t = start + np.arange(n) * H1
    df = pd.DataFrame({"open_time": t, "close_time": t + H1, "open": open_, "high": hi, "low": lo,
                       "close": close, "volume": vol})
    if gaps:
        df = df.drop(index=list(gaps)).reset_index(drop=True)
    return df


@pytest.fixture
def ohlcv():
    return make_ohlcv(3000)
