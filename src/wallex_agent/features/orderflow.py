"""Order-flow confirmation, shadow/live only (09 §7.7). OBI over depth snapshots near mid; TFI log-only."""

from __future__ import annotations

from collections import deque
from typing import Any


def obi_snapshot(book: dict[str, list], band_pct: float) -> float | None:
    bids, asks = book.get("bid") or [], book.get("ask") or []
    if not bids or not asks:
        return None
    bb, ba = float(bids[0]["price"]), float(asks[0]["price"])
    mid = (bb + ba) / 2
    lo, hi = mid * (1 - band_pct / 100), mid * (1 + band_pct / 100)
    bv = sum(float(x["quantity"]) for x in bids if float(x["price"]) >= lo)
    av = sum(float(x["quantity"]) for x in asks if float(x["price"]) <= hi)
    return None if bv + av == 0 else (bv - av) / (bv + av)


class OrderFlow:
    def __init__(self, cfg_of):
        self.c = cfg_of
        self.obi: dict[str, deque] = {}
        self.trades: dict[str, deque] = {}

    def on_book(self, symbol: str, t: float, book: dict) -> None:
        v = obi_snapshot(book, self.c.obi_band_pct)
        if v is not None:
            self.obi.setdefault(symbol, deque(maxlen=200)).append((t, v))

    def on_trades(self, symbol: str, trades: list[tuple[float, float, float, bool]]) -> None:
        """trades: (t, price, qty, isBuyOrder)."""
        q = self.trades.setdefault(symbol, deque(maxlen=2000))
        q.extend(trades)

    def evaluate(self, symbol: str, H_hours: int, now: float) -> dict[str, Any]:
        c = self.c
        w_obi_min = min(max(c.w_obi_frac_of_h * H_hours * 60, c.w_obi_min_min), c.w_obi_max_min)
        obs = [v for (t, v) in self.obi.get(symbol, ()) if now - t <= w_obi_min * 60]
        out: dict[str, Any] = {"w_obi_min": round(w_obi_min, 2), "obi_updates": len(obs)}
        if len(obs) < c.w_obi_min_updates:
            out.update(obi=None, status="INSUFFICIENT")
        else:
            out["obi"] = round(sum(obs) / len(obs), 4)
            out["status"] = "REJECT" if (c.obi_mode == "confirm" and out["obi"] < c.reject_below) else "OK"
        # TFI: log only (isBuyOrder meaning unverified, V5)
        max_age = c.w_tfi_max_frac_of_h * H_hours * 3600
        tr = [(p, q, b) for (t, p, q, b) in self.trades.get(symbol, ()) if now - t <= max_age]
        val = sum(p * q for p, q, _ in tr)
        if len(tr) >= c.w_tfi_min_trades and val >= c.min_tfi_value_tmn:
            buy = sum(p * q for p, q, b in tr if b)
            out["tfi"] = round((2 * buy - val) / val, 4)
        else:
            out["tfi"] = None
        out["tfi_trades"], out["tfi_mode"] = len(tr), c.tfi_mode
        return out
