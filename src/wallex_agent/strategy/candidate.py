"""Entry candidate record. One per symbol per cycle; logged even when rejected (Article 13)."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any


def candidate_id(symbol: str, t: int, setup: str, config_hash: str) -> str:
    return "C-" + hashlib.sha256(f"{symbol}|{t}|{setup}|{config_hash}".encode()).hexdigest()[:14]


@dataclass
class Candidate:
    candidate_id: str
    ts: int
    symbol: str
    base: str
    setup: str
    gray_zone: bool
    symbol_regime: str
    btc_regime: str
    price: float                      # last closed 1h close (decision price)
    atr_pct_1h: float
    regime_inputs: dict[str, Any]
    trigger_inputs: dict[str, Any]
    stop_dist: float = float("nan")   # fractions, not percent
    target_dist: float = float("nan")
    gate: dict[str, Any] = field(default_factory=dict)
    fee: dict[str, Any] = field(default_factory=dict)
    rank_inputs: dict[str, Any] = field(default_factory=dict)
    rank_score: float | None = None
    order_flow: dict[str, Any] = field(default_factory=dict)
    judge: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)
    final_decision: str = "PENDING"   # BUY | NO_TRADE | DEFER
    reason: str = ""
    order: dict[str, Any] = field(default_factory=dict)

    def reject(self, reason: str, decision: str = "NO_TRADE") -> Candidate:
        self.final_decision, self.reason = decision, reason
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
