"""Feature provider shared by every mode. Features are computed on the view's full available series and
sliced to the bars visible at view.now. Because every indicator is causal this equals computing on the
visible prefix (tests/test_lookahead*.py), and it keeps backtests linear in time.

Signal prices (team decision Q-33): with `data.signal_price = usdt_adjusted`, every TMN pair is divided
by the USDT/TMN close of the same hour before any indicator is computed, so the dollar's own moves do not
create signals. The USDT/TMN close of hour h is known when hour h closes, so this stays causal; a missing
USDT/TMN hour uses the previous hour (forward fill only). Execution, stops, targets and P&L remain in TMN:
`trigger()` returns the *signal* bars and features, and `tmn_bars()` the tradable TMN bars.

Timeframes: trigger bars are `data.trigger_hours` long and regime bars `data.regime_hours` long, both
aggregated from the (signal-price) 1h series on UTC boundaries.
"""

from __future__ import annotations

import pandas as pd

from wallex_agent.data.candles import aggregate
from wallex_agent.features.indicators import regime_features, trigger_features
from wallex_agent.strategy.regime import regime_series

PRICE_COLS = ("open", "high", "low", "close")
_EMPTY_REG = {"close_time": pd.Series(dtype="int64"), "raw": [], "confirmed": []}


def dollar_adjust(df1h: pd.DataFrame, usdt1h: pd.DataFrame) -> pd.DataFrame:
    """TMN candles divided by the USDT/TMN close of the same open_time (forward-filled from the past)."""
    if len(df1h) == 0:
        return df1h
    rate = usdt1h.set_index("open_time")["close"].sort_index()
    idx = df1h["open_time"].to_numpy()
    r = rate.reindex(rate.index.union(idx)).ffill().reindex(idx)
    out = df1h.copy()
    valid = r.notna().to_numpy()
    for c in PRICE_COLS:
        out[c] = df1h[c].to_numpy() / r.to_numpy()
    return out[valid].reset_index(drop=True)


class FeatureProvider:
    def __init__(self, cfg):
        self.cfg = cfg
        self._cache: dict[tuple, tuple] = {}
        self.adjust = cfg.data.signal_price == "usdt_adjusted"

    def _adjusted(self, symbol: str) -> bool:
        d = self.cfg.data
        return self.adjust and symbol.endswith(d.quote_asset) and symbol != d.usdt_tmn_symbol

    def signal_1h(self, view, symbol: str) -> pd.DataFrame:
        full = view.full(symbol, 1)
        if not self._adjusted(symbol):
            return full
        usdt = self.cfg.data.usdt_tmn_symbol
        if (usdt, 1) not in view._shared:  # no dollar rate -> no signal prices -> no entries (fail closed)
            return full.iloc[:0]
        return dollar_adjust(full, view.full(usdt, 1))

    def _full(self, view, symbol: str, hours: int, kind: str):
        base = view.full(symbol, 1)
        usdt = self.cfg.data.usdt_tmn_symbol
        extra = len(view.full(usdt, 1)) if self._adjusted(symbol) and (usdt, 1) in view._shared else 0
        key = (symbol, hours, kind, id(view._shared), len(base),
               int(base["close_time"].iloc[-1]) if len(base) else 0, extra)
        hit = self._cache.get((symbol, hours, kind))
        if hit is not None and hit[0] == key:
            return hit[1]
        sig1 = self.signal_1h(view, symbol)
        bars = sig1 if hours == 1 else (aggregate(sig1, hours) if len(sig1) else sig1)
        if len(bars) == 0:  # no data (or no dollar rate): empty features -> UNKNOWN / stale downstream
            val = (bars, pd.DataFrame(index=bars.index), pd.DataFrame(_EMPTY_REG))
        elif kind == "trig":
            val = (bars, trigger_features(bars, self.cfg.indicators), None)
        else:
            feat = regime_features(bars, self.cfg.indicators)
            if "complete" not in bars:
                bars = bars.assign(complete=True)
            val = (bars, feat, regime_series(feat, bars, self.cfg))
        self._cache[(symbol, hours, kind)] = (key, val)
        return val

    @staticmethod
    def _n(full: pd.DataFrame, now: int) -> int:
        return int((full["close_time"] <= now).sum()) if len(full) else 0

    def trigger(self, view, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Signal-price trigger bars and features visible at view.now."""
        full, feat, _ = self._full(view, symbol, self.cfg.data.trigger_hours, "trig")
        n = self._n(full, view.now)
        return full.iloc[:n], feat.iloc[:n]

    def regime(self, view, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        full, feat, reg = self._full(view, symbol, self.cfg.data.regime_hours, "reg")
        n = self._n(full, view.now)
        return full.iloc[:n], feat.iloc[:n], reg.iloc[:n]

    def tmn_bars(self, view, symbol: str) -> pd.DataFrame:
        """Tradable TMN bars of the trigger timeframe visible at view.now (execution prices)."""
        return view.bars(symbol, self.cfg.data.trigger_hours)
