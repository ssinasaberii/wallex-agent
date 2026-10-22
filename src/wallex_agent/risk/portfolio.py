"""Portfolio state, slots and account-level risk (spec 05 §2-4, §12, §15; 09 §7.10).

- A slot is one base asset with an open position or a live BUY reservation (CREATED..UNKNOWN).
  Reservation is atomic (lock) and happens *before* the order is sent.
- An order in UNKNOWN keeps its reservation (cash, risk, slot) and blocks a duplicate order.
- Fees are charged in TMN on both sides (simplification; Wallex may take the buy fee in the base asset).
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

LIVE_STATES = {"CREATED", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "CANCEL_REQUESTED", "UNKNOWN"}
FINAL_STATES = {"FILLED", "CANCELED", "REJECTED"}


@dataclass
class Order:
    client_id: str
    symbol: str
    base: str
    side: str            # BUY | SELL
    type: str            # MARKET | LIMIT
    qty: float
    created_t: int
    reason: str
    candidate_id: str | None = None
    price: float | None = None
    status: str = "CREATED"
    filled_qty: float = 0.0
    fills: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Reservation:
    base: str
    symbol: str
    cash_tmn: float
    risk_tmn: float
    order: Order | None = None
    plan: dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    base: str
    symbol: str
    setup: str
    qty: float
    entry_price: float
    entry_t: int
    stop: float
    initial_stop: float
    target: float | None
    stop_dist: float
    cost_rt: float
    g_be: float
    H: int | None
    gray_zone: bool
    candidate_id: str
    entry_fee_tmn: float
    highest_high: float
    bars_held: int = 0
    exit_order: Order | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def R(self) -> float:
        return self.entry_price * self.stop_dist

    def risk_tmn(self) -> float:
        """Money lost if the stop hits now, including the round-trip cost estimate."""
        return self.qty * (max(0.0, self.entry_price - self.stop) + self.entry_price * self.cost_rt)

    def raise_stop(self, new_stop: float, why: str, t: int) -> None:
        if new_stop > self.stop:  # the stop never moves down (09 §7.11)
            self.events.append({"t": t, "type": why, "from": self.stop, "to": new_stop})
            self.stop = new_stop


class Portfolio:
    def __init__(self, cash_tmn: float, risk_cfg, start_t: int, end_t: int):
        self.cash = float(cash_tmn)
        self.initial = float(cash_tmn)
        self.cfg = risk_cfg
        self.start_t, self.end_t = start_t, end_t
        self.positions: dict[str, Position] = {}
        self.reservations: dict[str, Reservation] = {}
        self.trades: list[dict[str, Any]] = []
        self.fees_paid = 0.0
        self.peak_equity = float(cash_tmn)
        self.equity_hist: deque[tuple[int, float]] = deque()
        self.killed = False
        self.kill_reason = ""
        self._lock = threading.Lock()

    def __getstate__(self):  # picklable for the shadow state store
        d = self.__dict__.copy()
        d.pop("_lock", None)
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ state
    def slots_used(self) -> int:
        return len(set(self.positions) | set(self.reservations))

    def free_cash(self) -> float:
        return self.cash - sum(r.cash_tmn for r in self.reservations.values())

    def open_risk(self) -> float:
        return sum(p.risk_tmn() for p in self.positions.values()) + sum(r.risk_tmn for r in self.reservations.values())

    def equity(self, marks: dict[str, float]) -> float:
        return self.cash + sum(p.qty * marks.get(p.symbol, p.entry_price) for p in self.positions.values())

    def equity_base(self, marks: dict[str, float]) -> float:
        return min(self.equity(marks), self.cfg.equity_cap_tmn)

    def holds(self, base: str) -> bool:
        return base in self.positions or base in self.reservations

    # ------------------------------------------------------------------ entry reservation (atomic)
    def try_reserve(self, base: str, symbol: str, cash_need: float, risk_tmn: float, equity_base: float,
                    plan: dict[str, Any]) -> tuple[Reservation | None, str]:
        with self._lock:
            if self.killed:
                return None, "KILL_SWITCH"
            if self.holds(base):
                return None, "ASSET_ALREADY_HELD"
            if self.slots_used() >= self.cfg.max_slots:
                return None, "NO_FREE_SLOT"
            if self.open_risk() + risk_tmn > self.cfg.max_open_risk_pct / 100 * equity_base + 1e-6:
                return None, "OPEN_RISK_CAP"
            if cash_need > self.free_cash() + 1e-6:
                return None, "INSUFFICIENT_CASH"
            r = Reservation(base, symbol, cash_need, risk_tmn, plan=plan)
            self.reservations[base] = r
            return r, ""

    def release(self, base: str) -> None:
        with self._lock:
            r = self.reservations.get(base)
            if r is not None and (r.order is None or r.order.status in {"CANCELED", "REJECTED"}):
                del self.reservations[base]

    # ------------------------------------------------------------------ fills
    def on_buy_fill(self, base: str, qty: float, price: float, fee_tmn: float, t: int) -> Position:
        with self._lock:
            r = self.reservations.pop(base)
            plan = r.plan
            self.cash -= qty * price + fee_tmn
            self.fees_paid += fee_tmn
            stop = price * (1 - plan["stop_dist"])
            target = price * (1 + plan["target_dist"]) if plan.get("target_dist") is not None else None
            pos = Position(base=base, symbol=r.symbol, setup=plan["setup"], qty=qty, entry_price=price, entry_t=t,
                           stop=stop, initial_stop=stop, target=target, stop_dist=plan["stop_dist"],
                           cost_rt=plan["cost_rt"], g_be=plan["g_be"], H=plan.get("H"),
                           gray_zone=plan.get("gray_zone", False), candidate_id=plan["candidate_id"],
                           entry_fee_tmn=fee_tmn, highest_high=price)
            self.positions[base] = pos
            return pos

    def on_sell_fill(self, base: str, qty: float, price: float, fee_tmn: float, t: int, exit_type: str) -> dict:
        with self._lock:
            pos = self.positions[base]
            qty = min(qty, pos.qty)
            proceeds = qty * price - fee_tmn
            self.cash += proceeds
            self.fees_paid += fee_tmn
            cost_share = (pos.entry_price * qty) + pos.entry_fee_tmn * (qty / pos.qty)
            pnl = proceeds - cost_share
            pos.entry_fee_tmn -= pos.entry_fee_tmn * (qty / pos.qty)
            pos.qty -= qty
            trade = {"symbol": pos.symbol, "setup": pos.setup, "candidate_id": pos.candidate_id,
                     "gray_zone": pos.gray_zone, "entry_t": pos.entry_t, "exit_t": t,
                     "entry_price": pos.entry_price, "exit_price": price, "qty": qty,
                     "pnl_tmn": pnl, "fee_exit_tmn": fee_tmn, "exit_type": exit_type,
                     "r_multiple": pnl / (pos.R * qty) if pos.R > 0 else None,
                     "bars_held": pos.bars_held, "initial_stop": pos.initial_stop, "final_stop": pos.stop}
            self.trades.append(trade)
            if pos.qty <= 1e-12:
                del self.positions[base]
            return trade

    # ------------------------------------------------------------------ account risk
    def mark(self, t: int, marks: dict[str, float]) -> float:
        eq = self.equity(marks)
        self.peak_equity = max(self.peak_equity, eq)
        self.equity_hist.append((t, eq))
        while self.equity_hist and self.equity_hist[0][0] < t - 24 * 3600:
            self.equity_hist.popleft()
        if not self.killed and (self.peak_equity - eq) >= self.cfg.kill_switch_drawdown_pct / 100 * self.peak_equity:
            self.killed, self.kill_reason = True, f"drawdown {(self.peak_equity - eq) / self.peak_equity:.2%}"
        return eq

    def daily_loss_breached(self, eq: float, equity_base: float) -> bool:
        if not self.equity_hist:
            return False
        ref = max(e for _, e in self.equity_hist)  # worst loss vs any point in the last 24h
        return (ref - eq) >= self.cfg.max_daily_loss_pct / 100 * equity_base
