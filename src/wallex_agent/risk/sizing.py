"""Position sizing and entry risk gates (spec 09 §7.10, 05 §5-6). All money in TMN."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SizeResult:
    ok: bool
    reason: str
    size_mult: float
    risk_budget_tmn: float
    qty: float
    notional_tmn: float
    gates_passed: list[str]

    def as_log(self) -> dict:
        return {"size_mult": self.size_mult, "risk_budget_tmn": round(self.risk_budget_tmn, 2),
                "qty": self.qty, "notional_tmn": round(self.notional_tmn, 2),
                "gates_passed": self.gates_passed, "gate_failed": None if self.ok else self.reason}


def size_position(price: float, stop_dist: float, cost_rt: float, equity_base: float, size_mult: float,
                  meta, risk_cfg) -> SizeResult:
    """qty = risk_budget / (entry * (stop_dist + cost_rt)); capped at max_position_pct; floored to step.
    The cap only lowers risk (A16 in PLAN.md)."""
    passed: list[str] = []
    budget = risk_cfg.risk_per_trade_pct / 100 * equity_base * size_mult
    per_unit = price * (stop_dist + cost_rt)
    if per_unit <= 0 or price <= 0:
        return SizeResult(False, "BAD_PRICE", size_mult, budget, 0.0, 0.0, passed)
    qty = budget / per_unit
    cap = risk_cfg.max_position_pct / 100 * equity_base
    if qty * price > cap:
        qty = cap / price
    passed.append("max_position_pct")
    qty = meta.floor_qty(qty)
    notional = qty * price
    if qty < meta.min_qty:
        return SizeResult(False, "BELOW_MIN_QTY", size_mult, budget, qty, notional, passed)
    passed.append("min_qty")
    floor = max(risk_cfg.min_buy_tmn, meta.min_notional)
    if notional < floor:
        return SizeResult(False, "BELOW_MIN_BUY", size_mult, budget, qty, notional, passed)
    passed.append("min_buy")
    return SizeResult(True, "", size_mult, budget, qty, notional, passed)
