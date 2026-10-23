"""MarketView: the only door through which the engine sees market data (spec 09 §3, §5.3).

`HistoricalMarketView.as_of(t)` exposes only bars with close_time <= t. Higher-timeframe bars are
aggregated once over the full 1h series and then sliced by close_time; a bucket whose close_time is
<= t contains only 1h bars that are themselves closed by t, so this equals aggregating the visible
prefix (checked by tests/test_lookahead.py). Order book, trades and news are absent historically;
gates that need them check the capability flags.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd

from wallex_agent.data.candles import aggregate


class MarketView(Protocol):
    now: int
    has_orderbook: bool
    has_trades: bool
    has_news: bool

    def symbols(self) -> list[str]: ...
    def bars(self, symbol: str, hours: int) -> pd.DataFrame: ...


class _Series:
    """Full-history frames for one symbol and timeframe, plus close_time index for slicing."""

    __slots__ = ("frame", "close_times")

    def __init__(self, frame: pd.DataFrame):
        self.frame = frame.reset_index(drop=True)
        self.close_times = self.frame["close_time"].to_numpy(dtype="int64")

    def upto(self, t: int) -> pd.DataFrame:
        return self.frame.iloc[: int(np.searchsorted(self.close_times, t, side="right"))]


class HistoricalMarketView:
    has_orderbook = False
    has_trades = False
    has_news = False

    def __init__(self, candles_1h: dict[str, pd.DataFrame], now: int = 0, _shared: dict | None = None):
        self.now = int(now)
        if _shared is None:
            _shared = {(s, 1): _Series(df.sort_values("open_time")) for s, df in candles_1h.items()}
        self._shared = _shared  # (symbol, hours) -> _Series, shared by every as_of() view

    def as_of(self, t: int) -> HistoricalMarketView:
        return HistoricalMarketView({}, now=t, _shared=self._shared)

    def symbols(self) -> list[str]:
        return sorted({s for s, h in self._shared if h == 1})

    def full(self, symbol: str, hours: int = 1) -> pd.DataFrame:
        """Full series without the as-of cut. For precomputing causal features only."""
        return self._series(symbol, hours).frame

    def _series(self, symbol: str, hours: int) -> _Series:
        key = (symbol, hours)
        if key not in self._shared:
            self._shared[key] = _Series(aggregate(self._shared[(symbol, 1)].frame, hours))
        return self._shared[key]

    def bars(self, symbol: str, hours: int = 1) -> pd.DataFrame:
        if (symbol, 1) not in self._shared:
            return pd.DataFrame()
        return self._series(symbol, hours).upto(self.now)
