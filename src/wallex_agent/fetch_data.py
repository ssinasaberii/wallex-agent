"""`python -m wallex_agent fetch-data`: refresh market metadata, update the 1h candle cache for the
eligible universe plus reference pairs, and freeze a hashed snapshot."""

from __future__ import annotations

import logging
from typing import Any

from wallex_agent.config import Config
from wallex_agent.data import market_meta
from wallex_agent.data.candles import CandleStore
from wallex_agent.data.snapshot import make_snapshot
from wallex_agent.data.wallex_rest import WallexRest

log = logging.getLogger(__name__)


def run(cfg: Config, top_n: int | None = None) -> dict[str, Any]:
    api = WallexRest()
    metas, meta_path = market_meta.fetch_and_save(api)
    eligible = market_meta.eligible_tmn_spot(metas, cfg.universe, cfg.data.quote_asset)
    n = top_n or cfg.data.fetch_top_n
    symbols = [m.symbol for m in eligible[:n]]
    refs = [cfg.data.btc_regime_symbol, cfg.data.btc_tmn_symbol, cfg.data.usdt_tmn_symbol]
    symbols += [s for s in refs if s not in symbols]
    store = CandleStore()
    summary = {}
    for i, s in enumerate(symbols, 1):
        df = store.update(api, s, cfg.data.history_days, cfg.data.udf_chunk_bars)
        gaps = int((df["quality_status"] == "gap_before").sum()) if len(df) else 0
        summary[s] = {"bars": len(df), "gaps": gaps}
        log.info("[%d/%d] %s: %d bars, %d gaps", i, len(symbols), s, len(df), gaps)
    api.close()
    digest, path = make_snapshot(symbols, meta_path,
                                 note=f"top {n} eligible TMN spot by 24h volume at fetch time + {refs}")
    short = {s: v for s, v in summary.items() if v["bars"] < (cfg.data.history_days - 1) * 24}
    return {"snapshot": digest, "path": str(path), "symbols": len(symbols),
            "short_history": short, "total_gaps": sum(v["gaps"] for v in summary.values())}
