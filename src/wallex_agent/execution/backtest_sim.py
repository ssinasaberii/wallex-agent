"""Pessimistic backtest execution (spec 09 §8.1).

- Decisions are taken at a 1h close; orders fill at the *next* bar's open x (1 +/- slip), taker fee.
- Standing stops: if low <= stop, exit at min(stop, open) x (1 - slip) (gap-aware).
- If stop and target are both touched in one bar, the stop is assumed first.
- Target: if high >= target, exit at target x (1 - slip).
"""

from __future__ import annotations

import pandas as pd

from wallex_agent.risk.portfolio import Order, Portfolio


class BacktestSimBroker:
    def __init__(self, portfolio: Portfolio, fee_taker: float, slip_of, scale_breakeven: bool = True):
        self.pf = portfolio
        self.fee = fee_taker
        self.scale_breakeven = scale_breakeven
        self.slip_of = slip_of            # symbol -> slippage fraction per side
        self.queue: list[Order] = []
        self.fills: list[dict] = []

    # Broker interface ---------------------------------------------------------------
    def submit(self, order: Order) -> Order:
        order.status = "ACKNOWLEDGED"
        self.queue.append(order)
        return order

    def cancel(self, order: Order) -> Order:
        if order in self.queue:
            self.queue.remove(order)
        order.status = "CANCELED"
        if order.side == "BUY":
            self.pf.release(order.base)
        elif order.base in self.pf.positions:
            self.pf.positions[order.base].exit_order = None
        return order

    def poll(self, order: Order) -> Order:
        return order

    # Simulation ----------------------------------------------------------------------
    def on_bar(self, symbol: str, bar: pd.Series) -> list[dict]:
        """Process one closed 1h bar for `symbol`: queued orders at its open, then intrabar stop/target."""
        out: list[dict] = []
        t_open, t_close = int(bar["open_time"]), int(bar["close_time"])
        o, h, lo = float(bar["open"]), float(bar["high"]), float(bar["low"])
        slip = self.slip_of(symbol)
        for order in [q for q in self.queue if q.symbol == symbol]:
            self.queue.remove(order)
            if order.side == "BUY" and order.reason == "PYRAMID":  # backtest experiment only
                if order.base not in self.pf.positions:
                    order.status = "CANCELED"
                    continue
                px = o * (1 + slip)
                fee = order.qty * px * self.fee
                self.pf.on_add_fill(order.base, order.qty, px, fee, t_open)
                order.status, order.filled_qty = "FILLED", order.qty
                out.append({"t": t_open, "symbol": symbol, "side": "BUY", "price": px, "qty": order.qty})
            elif order.side == "BUY":
                px = o * (1 + slip)
                fee = order.qty * px * self.fee
                pos = self.pf.on_buy_fill(order.base, order.qty, px, fee, t_open)
                order.status, order.filled_qty = "FILLED", order.qty
                order.fills.append({"t": t_open, "price": px, "qty": order.qty, "fee_tmn": fee})
                pos.highest_high = max(px, h)
                out.append({"t": t_open, "symbol": symbol, "side": "BUY", "price": px, "qty": order.qty})
            else:
                pos = self.pf.positions.get(order.base)
                if pos is None:
                    order.status = "CANCELED"
                    continue
                qty = min(order.qty, pos.qty)  # never more than the free balance
                px = o * (1 - slip)
                fee = qty * px * self.fee
                tr = self.pf.on_sell_fill(order.base, qty, px, fee, t_open, order.reason)
                order.status, order.filled_qty = "FILLED", qty
                order.fills.append({"t": t_open, "price": px, "qty": qty, "fee_tmn": fee})
                out.append({"t": t_open, "symbol": symbol, "side": "SELL", "price": px, "qty": qty, "trade": tr})
        # Standing stop / target for positions still open on this symbol.
        for base, pos in list(self.pf.positions.items()):
            if pos.symbol != symbol or pos.exit_order is not None:
                continue
            exit_px, why = None, None
            if lo <= pos.stop:  # pessimistic: the stop comes before any partial target in the same bar
                exit_px, why = min(pos.stop, o) * (1 - slip), "STOP" if pos.stop < pos.entry_price else "TRAIL_STOP"
            else:
                for lvl in pos.scale_plan:  # staged selling (reduce only)
                    if not lvl["done"] and h >= lvl["price"] and pos.qty > 0:
                        lvl["done"] = True
                        q = min(pos.qty, pos.qty0 * lvl["fraction"])
                        px = lvl["price"] * (1 - slip)
                        fee = q * px * self.fee
                        tr = self.pf.on_sell_fill(base, q, px, fee, t_close, "SCALE_OUT")
                        out.append({"t": t_close, "symbol": symbol, "side": "SELL", "price": px, "trade": tr})
                        if base in self.pf.positions and self.scale_breakeven:
                            pos.raise_stop(pos.entry_price * (1 + pos.g_be), "SCALE_OUT_BREAKEVEN", t_close)
                if base not in self.pf.positions:
                    continue
                if pos.target is not None and h >= pos.target:
                    exit_px, why = pos.target * (1 - slip), "TARGET"
            if exit_px is not None:
                fee = pos.qty * exit_px * self.fee
                tr = self.pf.on_sell_fill(base, pos.qty, exit_px, fee, t_close, why)
                out.append({"t": t_close, "symbol": symbol, "side": "SELL", "price": exit_px, "trade": tr})
        self.fills.extend(out)
        return out

    def liquidate_all(self, closes: dict[str, float], t: int, why: str) -> list[dict]:
        """End of episode: everything sold at close x (1 - slip) with fee (09 §8.1)."""
        out = []
        for base, pos in list(self.pf.positions.items()):
            px = closes.get(pos.symbol, pos.entry_price) * (1 - self.slip_of(pos.symbol))
            fee = pos.qty * px * self.fee
            out.append({"t": t, "symbol": pos.symbol, "side": "SELL", "price": px,
                        "trade": self.pf.on_sell_fill(base, pos.qty, px, fee, t, why)})
        for q in list(self.queue):
            self.cancel(q)
        self.fills.extend(out)
        return out
