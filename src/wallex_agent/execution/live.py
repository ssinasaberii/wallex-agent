"""LiveBroker: real spot orders on the challenge credit sub-account (PLAN_LIVE.md, 05 §10-§15).

Same interface and the same stop / target / trailing logic as ShadowSimBroker (it subclasses it); only how an
order is executed differs. Rules:
- orders are marketable LIMITs (best ask + offset to buy, best bid - offset to sell), never MARKET;
- deterministic client ids (`B_<candidate>`, `S_<candidate>_<t>`); an order whose result is unknown (timeout,
  5xx) is never sent again: it is looked up by its id, its reservation is held and no new buy is made meanwhile;
- only real fills create positions and P&L, with the fee Wallex reports (a buy fee in the base coin lowers the
  quantity held); a sell never exceeds the free balance; one exit order per position;
- caps: `live.max_order_tmn` per buy and `live.max_notional_tmn` for all open positions;
- operational kill switch (05 §15): an unknown order, a balance mismatch or `live.max_consecutive_errors` errors
  in a row stop new buys (exits keep working) until the cause clears;
- dry run (`live.dry_run`, the default): every request is built and logged but not sent; fills are simulated
  against the live book exactly like the shadow test.
Backtest and shadow never construct this class (tests/test_no_live_and_trailing.py).
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import httpx

from wallex_agent.data.wallex_orders import WallexOrders, fmt, safe_client_id
from wallex_agent.risk.portfolio import Order
from wallex_agent.shadow.runner import ShadowSimBroker, walk_book

log = logging.getLogger(__name__)
FINAL = {"FILLED", "FINISHED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}


def parse_fills(res: dict, base: str) -> dict[str, float]:
    """Executed quantity, average price, fee in TMN and fee taken in the base coin, from an order result."""
    qty = cost = fee_tmn = fee_base = 0.0
    for f in res.get("fills") or []:
        q, p = float(f.get("quantity") or 0), float(f.get("price") or 0)
        qty += q
        cost += q * p
        fee, asset = float(f.get("fee") or 0), str(f.get("feeAsset") or "").upper()
        if asset == base.upper():
            fee_base += fee
            fee_tmn += fee * p
        else:
            fee_tmn += fee
    if qty == 0 and float(res.get("executedQty") or 0) > 0:  # no fill list: use the order's totals
        qty = float(res["executedQty"])
        cost = float(res.get("executedSum") or 0) or qty * float(res.get("executedPrice") or 0)
        fee_tmn = float(res.get("fee") or 0)
    return {"qty": qty, "price": cost / qty if qty else 0.0, "fee_tmn": fee_tmn, "fee_base": fee_base}


def is_final(res: dict) -> bool:
    st = str(res.get("status") or "").upper()
    return st in FINAL or float(res.get("executedPercent") or 0) >= 100 or res.get("active") is False


class LiveBroker(ShadowSimBroker):
    def __init__(self, pf, fee: float, books: dict[str, dict], dlog, client: WallexOrders, metas: dict, live_cfg):
        super().__init__(pf, fee, books, dlog)
        self.client, self.metas, self.cfg = client, metas, live_cfg
        self.open: dict[str, dict[str, Any]] = {}   # client id -> {"order", "t", "n"}
        self.halt_reasons: set[str] = set()
        self.errors = 0
        self.next_reconcile = 0.0
        self.events: list[dict] = []

    # ------------------------------------------------------------------ helpers
    @property
    def halted(self) -> bool:
        return bool(self.halt_reasons)

    def _event(self, kind: str, **kw) -> None:
        e = {"t": int(time.time()), "kind": kind, **kw}
        self.events.append(e)
        log.info("LIVE %s %s", kind, kw)

    def _error(self, what: str) -> None:
        self.errors += 1
        self._event("ERROR", what=what, consecutive=self.errors)
        if self.errors >= self.cfg.max_consecutive_errors:
            self.halt_reasons.add("REPEATED_ERRORS")

    def _ok(self) -> None:
        self.errors = 0
        self.halt_reasons.discard("REPEATED_ERRORS")

    def _price(self, symbol: str, side: str) -> float | None:
        book = self.books.get(symbol) or {}
        lv = book.get("ask" if side == "BUY" else "bid") or []
        if not lv:
            return None
        best = float(lv[0]["price"])
        meta = self.metas[symbol]
        off = self.cfg.limit_offset_pct / 100
        f = 10 ** meta.price_decimals
        return math.ceil(best * (1 + off) * f) / f if side == "BUY" else math.floor(best * (1 - off) * f) / f

    def _open_notional(self) -> float:
        held = sum(p.qty * p.entry_price for p in self.pf.positions.values())
        pending = sum(x["order"].qty * (x["order"].price or 0) for x in self.open.values()
                      if x["order"].side == "BUY")
        return held + pending

    # ------------------------------------------------------------------ submit
    def submit(self, order: Order) -> Order:
        order.client_id = safe_client_id(order.client_id)
        if order.side == "BUY":
            return self._buy(order)
        self._try_sell(order)
        return order

    def _reject(self, order: Order, why: str) -> Order:
        order.status = "REJECTED"
        self.pf.release(order.base)
        self._event("BUY_REJECTED", cid=order.client_id, why=why)
        return order

    def _buy(self, order: Order) -> Order:
        if self.halted:
            return self._reject(order, "HALTED:" + ",".join(sorted(self.halt_reasons)))
        meta, px = self.metas.get(order.symbol), self._price(order.symbol, "BUY")
        if meta is None or px is None:
            return self._reject(order, "NO_BOOK")
        room = self.cfg.max_notional_tmn - self._open_notional()
        qty = meta.floor_qty(min(order.qty, self.cfg.max_order_tmn / px, max(room, 0) / px))
        floor = max(self.pf.cfg.min_buy_tmn, meta.min_notional)
        if qty * px < floor:
            return self._reject(order, f"CAP:{qty * px:.0f}<{floor:.0f}")
        r = self.pf.reservations.get(order.base)
        if r is not None:
            r.order = order
        order.qty, order.price, order.type = qty, px, "LIMIT"
        return self._send(order)

    def _send(self, order: Order) -> Order:
        meta = self.metas[order.symbol]
        order.status = "SUBMITTED"
        try:
            j = self.client.place(order.symbol, order.side, fmt(order.qty, meta.qty_decimals),
                                  fmt(order.price, meta.price_decimals), order.client_id)
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as e:
            order.status = "UNKNOWN"   # never resend: look it up by id (05 §13)
            self.open[order.client_id] = {"order": order, "t": time.time(), "n": 0}
            self.halt_reasons.add("UNKNOWN_ORDER")
            self._error(f"place {order.client_id}: {type(e).__name__}")
            return order
        if j.get("dry_run"):
            return self._simulate(order)
        if j.get("_http") not in (200, 201) or not j.get("success", True) or not j.get("result"):
            self._error(f"place {order.client_id}: {j.get('_http')} {str(j.get('message'))[:120]}")
            if order.side == "BUY":
                return self._reject(order, "EXCHANGE_REJECTED")
            order.status = "ACKNOWLEDGED"  # an exit is retried on the next tick with a new id
            self.open.pop(order.client_id, None)
            if order not in self.pending_sells:
                self.pending_sells.append(order)
            return order
        self._ok()
        self._event("PLACED", cid=order.client_id, side=order.side, symbol=order.symbol, qty=order.qty,
                    price=order.price)
        self._apply(order, j["result"])
        return order

    def _simulate(self, order: Order) -> Order:
        """Dry run: the request was logged; fill against the live book like the shadow test."""
        if order.side == "BUY":
            book = self.books.get(order.symbol) or {}
            px = walk_book(book.get("ask", []), order.qty)
            if px is None or px > order.price:
                return self._reject(order, "DRY_NO_FILL")
            fee = order.qty * px * self.fee
            pos = self.pf.on_buy_fill(order.base, order.qty, px, fee, int(time.time()))
            pos.highest_high = px
            order.status, order.filled_qty = "FILLED", order.qty
            order.fills.append({"t": int(time.time()), "price": px, "qty": order.qty, "fee_tmn": fee, "dry": True})
            self._event("DRY_BUY", cid=order.client_id, symbol=order.symbol, qty=order.qty, price=px)
            return order
        super()._try_sell(order)
        return order

    # ------------------------------------------------------------------ fills
    def _apply(self, order: Order, res: dict) -> None:
        if order.side == "BUY" and order.base in self.pf.positions:
            return  # already applied
        f = parse_fills(res, order.base)
        if not is_final(res):
            self.open[order.client_id] = self.open.get(order.client_id) or {"order": order, "t": time.time(), "n": 0}
            order.status = "PARTIALLY_FILLED" if f["qty"] > 0 else "ACKNOWLEDGED"
            return
        self.open.pop(order.client_id, None)
        now = int(time.time())
        if order.side == "BUY":
            net = f["qty"] - f["fee_base"]
            if net <= 0:
                order.status = "CANCELED"
                self.pf.release(order.base)
                self._event("BUY_NOT_FILLED", cid=order.client_id)
                return
            pos = self.pf.on_buy_fill(order.base, net, f["price"], f["fee_tmn"], now)
            pos.highest_high = f["price"]
            order.status, order.filled_qty = "FILLED", f["qty"]
            order.fills.append({"t": now, **f})
            self._event("BUY_FILLED", cid=order.client_id, symbol=order.symbol, qty=net, price=f["price"],
                        fee_tmn=f["fee_tmn"])
            return
        if f["qty"] > 0 and order.base in self.pf.positions:
            pos, meta = self.pf.positions[order.base], self.metas[order.symbol]
            qty, px = f["qty"], f["price"]
            if meta.floor_qty(pos.qty - qty) <= 0:  # dust below the market's step (buy fee in coin): close it here
                qty, px = pos.qty, f["qty"] * f["price"] / pos.qty
            tr = self.pf.on_sell_fill(order.base, qty, px, f["fee_tmn"], now, order.reason)
            self.log.exit(now, tr, order.reason, order.symbol)
            self._event("SELL_FILLED", cid=order.client_id, symbol=order.symbol, qty=f["qty"], price=f["price"])
        order.filled_qty += f["qty"]
        if order.base in self.pf.positions:  # rest not sold: retry with a new id on the next tick
            order.status = "ACKNOWLEDGED"
            if order not in self.pending_sells:
                self.pending_sells.append(order)
        else:
            order.status = "FILLED"
            if order in self.pending_sells:
                self.pending_sells.remove(order)

    def _try_sell(self, order: Order) -> None:
        pos = self.pf.positions.get(order.base)
        if pos is None:
            order.status = "CANCELED"
            return
        if any(x["order"] is order for x in self.open.values()):
            return  # this exit is already on the book (one exit order per position)
        px, meta = self._price(order.symbol, "SELL"), self.metas.get(order.symbol)
        if px is None or meta is None:
            order.status = "ACKNOWLEDGED"
            if order not in self.pending_sells:
                self.pending_sells.append(order)
            return
        qty = pos.qty
        if not self.client.dry_run:
            try:
                free = self.client.balances().get(order.base.upper(), {}).get("free", 0.0)
                qty = min(qty, free)
            except Exception as e:  # noqa: BLE001
                self._error(f"balances before sell: {type(e).__name__}")
        qty = meta.floor_qty(qty)
        if qty <= 0:
            self._error(f"nothing free to sell for {order.base}")
            return
        # a repost after a partial fill or a cancel gets a new deterministic id: <exit id>_r<n>
        base_id = order.client_id.split("_r")[0]
        tries = sum(1 for e in self.events if e.get("kind") == "PLACED" and str(e.get("cid", "")).startswith(base_id))
        order.client_id = safe_client_id(f"{base_id}_r{tries}") if tries else base_id
        order.qty, order.price, order.type = qty, px, "LIMIT"
        self._send(order)

    # ------------------------------------------------------------------ polling
    def retry_pending(self) -> None:
        now = time.time()
        for cid, x in list(self.open.items()):
            o = x["order"]
            try:
                j = self.client.status(cid)
            except Exception as e:  # noqa: BLE001
                self._error(f"status {cid}: {type(e).__name__}")
                continue
            if j.get("_http") == 404 or (j.get("_http") == 200 and not j.get("result")):
                x["n"] += 1
                if o.status == "UNKNOWN" and x["n"] >= 3:   # never reached the exchange
                    self.open.pop(cid)
                    if o.side == "BUY":
                        self._reject(o, "UNKNOWN_NOT_FOUND")
                    else:
                        o.status = "ACKNOWLEDGED"
                        self.pending_sells.append(o)
                continue
            if j.get("_http") != 200:
                self._error(f"status {cid}: {j.get('_http')}")
                continue
            self._ok()
            res = j["result"]
            if not is_final(res) and now - x["t"] >= self.cfg.order_ttl_s:
                try:
                    c = self.client.cancel(cid)
                    res = (c.get("result") or res) if c.get("_http") == 200 else res
                    res = {**res, "status": "CANCELED"} if c.get("_http") == 200 else res
                except Exception as e:  # noqa: BLE001
                    self._error(f"cancel {cid}: {type(e).__name__}")
                    continue
            self._apply(o, res)
        if not any(x["order"].status == "UNKNOWN" for x in self.open.values()):
            self.halt_reasons.discard("UNKNOWN_ORDER")
        for o in list(self.pending_sells):
            if not any(x["order"] is o for x in self.open.values()):
                self.pending_sells.remove(o)
                self._try_sell(o)

    def check_stops(self) -> None:
        for pos in self.pf.positions.values():
            pos.scale_plan = []  # staged selling is a backtest experiment; live exits are whole-position
        super().check_stops()

    def reconcile(self, now: float | None = None) -> dict | None:
        """Wallet vs internal positions (05 §15). A coin we think we hold but the wallet lacks stops new buys."""
        now = now or time.time()
        if now < self.next_reconcile:
            return None
        self.next_reconcile = now + self.cfg.reconcile_s
        try:
            bal = self.client.balances()
        except Exception as e:  # noqa: BLE001
            self._error(f"reconcile balances: {type(e).__name__}")
            return None
        self._ok()
        bad = []
        if not self.client.dry_run:
            for p in self.pf.positions.values():
                have = bal.get(p.base.upper(), {}).get("value", 0.0)
                if have < p.qty * 0.98:
                    bad.append({"coin": p.base, "internal": p.qty, "wallet": have})
        if bad:
            self.halt_reasons.add("RECONCILE_MISMATCH")
        else:
            self.halt_reasons.discard("RECONCILE_MISMATCH")
        out = {"tmn": bal.get("TMN", {}), "mismatch": bad, "halted": sorted(self.halt_reasons)}
        self._event("RECONCILE", **out)
        return out
