"""Candle download, Parquet cache and aggregation (spec 09 §5.2-5.3, api-verification V2/V3).

- Only resolution 60 is fetched; 4h (and 1D for comparisons) are aggregated from closed 1h bars on
  UTC boundaries. A higher-timeframe bar is `complete` only if all its 1h bars exist.
- The API returns the forming bar; every bar with open_time + period > now is dropped.
- Responses are capped at ~695 bars, so requests are chunked by `udf_chunk_bars`.
- Times are int epoch seconds (UTC). `open_time` is the bar start, `close_time` = open_time + period.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from wallex_agent.data.wallex_rest import WallexRest

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
CANDLE_DIR = ROOT / "data" / "candles"
H1 = 3600
COLUMNS = ["open_time", "close_time", "open", "high", "low", "close", "volume",
           "source", "event_time", "received_time", "processed_time", "quality_status"]


def parse_udf(d: dict, period_s: int, received_time: int, now: int, source: str) -> pd.DataFrame:
    t = d.get("t") or []
    if not t:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame({
        "open_time": np.asarray(t, dtype="int64"),
        "open": pd.to_numeric(d["o"]), "high": pd.to_numeric(d["h"]),
        "low": pd.to_numeric(d["l"]), "close": pd.to_numeric(d["c"]), "volume": pd.to_numeric(d["v"]),
    })
    df["close_time"] = df["open_time"] + period_s
    df = df[df["close_time"] <= now]  # closed bars only (V2: last bar returned is still forming)
    df["source"] = source
    df["event_time"] = df["close_time"]
    df["received_time"] = received_time
    df["processed_time"] = int(time.time())
    df["quality_status"] = "ok"
    return df[COLUMNS].astype({"open": "float64", "high": "float64", "low": "float64",
                               "close": "float64", "volume": "float64"})


def fetch_range(api: WallexRest, symbol: str, start: int, end: int, chunk_bars: int = 500) -> pd.DataFrame:
    now = int(time.time())
    end = min(end, now)
    parts = []
    t0 = start
    while t0 < end:
        t1 = min(t0 + chunk_bars * H1, end)
        resp = api.get("/v1/udf/history", {"symbol": symbol, "resolution": "60", "from": t0, "to": t1})
        resp.raise_for_status()
        parts.append(parse_udf(resp.json(), H1, int(time.time()), now, "wallex:udf/history:60"))
        t0 = t1
    if not parts:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(parts, ignore_index=True)
    return finalize(df)


def finalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.drop_duplicates("open_time", keep="last").sort_values("open_time").reset_index(drop=True)
    if len(df):
        gap = df["open_time"].diff().fillna(H1) > H1
        df.loc[gap, "quality_status"] = "gap_before"
    return df


class CandleStore:
    def __init__(self, root: Path = CANDLE_DIR):
        self.root = root

    def path(self, symbol: str) -> Path:
        return self.root / symbol / "60.parquet"

    def load(self, symbol: str) -> pd.DataFrame:
        p = self.path(symbol)
        return pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=COLUMNS)

    def update(self, api: WallexRest, symbol: str, history_days: int, chunk_bars: int = 500) -> pd.DataFrame:
        """Incremental: fetch only what is missing at the head and/or tail of the cached range."""
        now = int(time.time())
        want_start = (now - history_days * 86400) // H1 * H1
        cur = self.load(symbol)
        parts = [cur] if len(cur) else []
        if len(cur) == 0:
            parts.append(fetch_range(api, symbol, want_start, now, chunk_bars))
        else:
            first, last = int(cur["open_time"].min()), int(cur["open_time"].max())
            if want_start < first:
                parts.append(fetch_range(api, symbol, want_start, first, chunk_bars))
            parts.append(fetch_range(api, symbol, last + H1, now, chunk_bars))
        parts = [p for p in parts if len(p)]
        df = finalize(pd.concat(parts, ignore_index=True)) if parts else pd.DataFrame(columns=COLUMNS)
        p = self.path(symbol)
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p, index=False)
        return df


def aggregate(df1h: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Aggregate closed 1h bars into `hours`-hour bars aligned to UTC multiples of `hours`.
    `complete` is False when any of the constituent 1h bars is missing."""
    if len(df1h) == 0:
        return pd.DataFrame(columns=["open_time", "close_time", "open", "high", "low", "close", "volume",
                                     "n_bars", "complete"])
    period = hours * H1
    g = df1h.assign(bucket=df1h["open_time"] // period * period).groupby("bucket", sort=True)
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(), "n_bars": g["open_time"].count(),
    }).reset_index().rename(columns={"bucket": "open_time"})
    out["close_time"] = out["open_time"] + period
    out["complete"] = out["n_bars"] == hours
    return out[["open_time", "close_time", "open", "high", "low", "close", "volume", "n_bars", "complete"]]
