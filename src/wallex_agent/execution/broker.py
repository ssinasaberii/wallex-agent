"""Broker interface (spec 09 §8). Implementations: BacktestSimBroker, ShadowSimBroker, LiveBroker (disabled)."""

from __future__ import annotations

from typing import Protocol

from wallex_agent.risk.portfolio import Order


class Broker(Protocol):
    def submit(self, order: Order) -> Order: ...
    def cancel(self, order: Order) -> Order: ...
    def poll(self, order: Order) -> Order: ...
