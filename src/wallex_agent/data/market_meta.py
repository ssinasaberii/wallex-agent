"""Market metadata snapshot (api-verification V1).

`stepSize`/`tickSize` from the API are decimal counts, so qty_step = 10**-stepSize and
price_tick = 10**-tickSize (verified equal to hector amount/price_precision on 373/373 symbols).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wallex_agent.data.wallex_rest import WallexRest

ROOT = Path(__file__).resolve().parents[3]
META_DIR = ROOT / "data" / "meta"


@dataclass(frozen=True)
class MarketMeta:
    symbol: str
    base: str
    quote: str
    is_spot: bool
    categories: tuple[int, ...]
    min_notional: float
    min_qty: float
    qty_decimals: int
    price_decimals: int
    market_order_enabled: bool
    bid: float | None
    ask: float | None
    spread_pct: float | None
    tmn_volume_24h: float

    @property
    def qty_step(self) -> float:
        return 10.0 ** -self.qty_decimals

    @property
    def price_tick(self) -> float:
        return 10.0 ** -self.price_decimals

    def floor_qty(self, qty: float) -> float:
        f = 10**self.qty_decimals
        return math.floor(qty * f + 1e-9) / f

    def round_price(self, price: float) -> float:
        return round(price, self.price_decimals)


def _f(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_markets(v1: dict[str, Any], hector: dict[str, Any]) -> dict[str, MarketMeta]:
    hmap = {m["symbol"]: m for m in hector["result"]["markets"]}
    out: dict[str, MarketMeta] = {}
    for sym, m in v1["result"]["symbols"].items():
        h = hmap.get(sym, {})
        st = m.get("stats", {})
        bid, ask = _f(st.get("bidPrice")), _f(st.get("askPrice"))
        mid = (bid + ask) / 2 if bid and ask else None
        out[sym] = MarketMeta(
            symbol=sym, base=m["baseAsset"], quote=m["quoteAsset"], is_spot=bool(h.get("is_spot")),
            categories=tuple(h.get("categories") or ()),
            min_notional=float(m["minNotional"]), min_qty=float(m["minQty"]),
            qty_decimals=int(m["stepSize"]), price_decimals=int(m["tickSize"]),
            market_order_enabled=bool(h.get("is_market_type_enable", m.get("isMarketTypeEnable"))),
            bid=bid, ask=ask, spread_pct=(ask - bid) / mid * 100 if mid else None,
            tmn_volume_24h=_f(st.get("24h_tmnVolume")) or _f(h.get("quote_volume_24h")) or 0.0,
        )
    return out


def fetch_and_save(api: WallexRest) -> tuple[dict[str, MarketMeta], Path]:
    v1 = api.get_json("/v1/markets")
    hector = api.get_json("/hector/web/v1/markets")
    metas = parse_markets(v1, hector)
    META_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = META_DIR / f"markets_{stamp}.json"
    payload = {"retrieved_at": stamp, "source": "wallex /v1/markets + /hector/web/v1/markets",
               "markets": {s: asdict(m) for s, m in metas.items()}}
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    (META_DIR / "markets_latest.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return metas, path


def load(path: Path | None = None) -> dict[str, MarketMeta]:
    p = path or (META_DIR / "markets_latest.json")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {s: MarketMeta(**(d | {"categories": tuple(d["categories"])})) for s, d in raw["markets"].items()}


def eligible_tmn_spot(metas: dict[str, MarketMeta], cfg_universe: Any, quote: str = "TMN") -> list[MarketMeta]:
    """Static eligibility (09 §7.1 steps 1-3): spot, quote=TMN, not stable/non-crypto.
    Allowed-markets list, volume rank and spread are applied per cycle by strategy.universe."""
    excl_base = set(cfg_universe.exclude_stablecoins) | set(cfg_universe.exclude_non_crypto)
    excl_cat = set(cfg_universe.exclude_categories)
    out = [m for m in metas.values()
           if m.is_spot and m.quote == quote and m.base not in excl_base and not (set(m.categories) & excl_cat)]
    return sorted(out, key=lambda m: -m.tmn_volume_24h)
