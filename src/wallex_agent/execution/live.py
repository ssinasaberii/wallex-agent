"""LiveBroker: interface only. Live orders are out of scope (spec 09 §0, §8.3; team decision Q-00).
Nothing in backtest or shadow can reach this class (tests/test_no_live.py)."""

from __future__ import annotations

from wallex_agent.risk.portfolio import Order


class LiveBroker:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Live trading is disabled in this version (spec 09 §8.3).")

    def submit(self, order: Order) -> Order:
        raise NotImplementedError

    def cancel(self, order: Order) -> Order:
        raise NotImplementedError

    def poll(self, order: Order) -> Order:
        raise NotImplementedError
