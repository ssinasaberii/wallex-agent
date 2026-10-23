"""Fee snapshot (spec 08 §3, 04 §15). Every candidate records the snapshot id it was evaluated with;
a different snapshot at final recheck invalidates the candidate (08 §10)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeSnapshot:
    snapshot_id: str
    maker: float  # fraction, e.g. 0.0025
    taker: float
    assumed: bool
    source: str

    @classmethod
    def from_config(cls, fees, taker_override_pct: float | None = None) -> FeeSnapshot:
        taker = (taker_override_pct if taker_override_pct is not None else fees.taker_pct) / 100
        sid = fees.snapshot_id if taker_override_pct is None else f"{fees.snapshot_id}-taker{taker_override_pct}"
        return cls(sid, fees.maker_pct / 100, taker, fees.assumed, fees.source)

    @property
    def g_break_even(self) -> float:
        """Conservative taker-in / taker-out break-even growth (08 §5)."""
        return (1 + self.taker) / (1 - self.taker) - 1
