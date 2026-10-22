import json

import pandas as pd
import pytest

from wallex_agent.data import snapshot as snap
from wallex_agent.data.candles import H1, CandleStore, aggregate, finalize, parse_udf
from wallex_agent.data.market_meta import MarketMeta

from .conftest import make_ohlcv

T0 = 1_792_670_400  # 2026-10-22 12:00 UTC, a 4h boundary


def udf(ts):
    return {"s": "ok", "t": ts, "o": ["1"] * len(ts), "h": ["2"] * len(ts), "l": ["0.5"] * len(ts),
            "c": ["1.5"] * len(ts), "v": ["10"] * len(ts)}


def test_parse_udf_drops_forming_bar():
    now = T0 + 2 * H1 + 120  # the bar opened at T0+2h is still forming
    df = parse_udf(udf([T0, T0 + H1, T0 + 2 * H1]), H1, now, now, "test")
    assert df["open_time"].tolist() == [T0, T0 + H1]
    assert (df["close_time"] <= now).all()
    assert df["close"].dtype == "float64"
    assert set(["source", "event_time", "received_time", "processed_time", "quality_status"]) <= set(df.columns)


def test_finalize_dedupes_and_flags_gaps():
    now = T0 + 10 * H1
    df = parse_udf(udf([T0, T0 + H1, T0 + H1, T0 + 4 * H1]), H1, now, now, "test")
    out = finalize(df)
    assert out["open_time"].tolist() == [T0, T0 + H1, T0 + 4 * H1]
    assert out["quality_status"].tolist() == ["ok", "ok", "gap_before"]


def test_aggregate_4h_alignment_and_completeness():
    df = make_ohlcv(12, start=T0, gaps=(5,))  # bars 12:00..23:00, 17:00 missing
    agg = aggregate(df, 4)
    assert (agg["open_time"] % (4 * H1) == 0).all()
    assert agg["complete"].tolist() == [True, False, True]
    first = agg.iloc[0]
    src = df.iloc[:4]
    assert first["open"] == src["open"].iloc[0] and first["close"] == src["close"].iloc[-1]
    assert first["high"] == src["high"].max() and first["low"] == src["low"].min()
    assert first["volume"] == pytest.approx(src["volume"].sum())


def test_market_meta_decimal_steps():
    m = MarketMeta("BTCTMN", "BTC", "TMN", True, (44,), 50000, 1e-8, 8, 0, True, 1.0, 1.1, 0.05, 1e11)
    assert m.qty_step == 1e-8 and m.price_tick == 1.0
    assert m.floor_qty(0.123456789) == 0.12345678
    m2 = MarketMeta("PEPETMN", "PEPE", "TMN", True, (4,), 50000, 1, 0, 4, False, 1.2, 1.21, 0.1, 1e10)
    assert m2.floor_qty(12345.9) == 12345 and m2.round_price(1.234567) == 1.2346


def test_snapshot_hash_is_content_addressed(tmp_path, monkeypatch):
    monkeypatch.setattr(snap, "SNAP_DIR", tmp_path / "snaps")
    store = CandleStore(tmp_path / "candles")
    for s, seed in (("AAATMN", 1), ("BBBTMN", 2)):
        p = store.path(s)
        p.parent.mkdir(parents=True)
        make_ohlcv(100, seed=seed).to_parquet(p, index=False)
    meta = tmp_path / "markets.json"
    meta.write_text(json.dumps({"markets": {}}))
    h1, d1 = snap.make_snapshot(["AAATMN", "BBBTMN"], meta, store)
    h2, _ = snap.make_snapshot(["BBBTMN", "AAATMN"], meta, store)
    assert h1 == h2
    candles, _, manifest = snap.load_snapshot(h1)
    assert set(candles) == {"AAATMN", "BBBTMN"} and manifest["hash"] == h1
    # Tampering is detected.
    df = pd.read_parquet(d1 / "candles" / "AAATMN.parquet")
    df.loc[0, "close"] = 0
    df.to_parquet(d1 / "candles" / "AAATMN.parquet", index=False)
    with pytest.raises(ValueError):
        snap.load_snapshot(h1)
