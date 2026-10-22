"""Deterministic position management (spec 09 §7.11). No judge is involved in any exit (ADR-006).

Each exit condition only produces a SELL intent; execution goes through the broker (05 §10).
Stops only move up. Intrabar protective stops/targets are standing orders handled by the broker.
"""

from __future__ import annotations

from wallex_agent.risk.portfolio import Position
from wallex_agent.strategy.regime import DOWN, HIGH_VOL, RANGE, UNKNOWN, UP
from wallex_agent.strategy.triggers import T1, T2, T3


def update_and_check(pos: Position, last_bar, feat_1h_row, symbol_regime: str, btc_regime: str, t: int,
                     cfg) -> str | None:
    """Called once per closed 1h bar while the position is open. Returns an exit reason or None."""
    ex = cfg.exits
    pos.bars_held += 1
    pos.highest_high = max(pos.highest_high, float(last_bar["high"]))
    atr = float(feat_1h_row["atr"])
    gain = pos.highest_high - pos.entry_price
    if pos.setup in (T1, T2) and gain >= ex.breakeven_at_r * pos.R:
        pos.raise_stop(pos.entry_price * (1 + pos.g_be), "BREAKEVEN", t)
    trail_on = pos.setup == T2 or (pos.setup == T1 and gain >= ex.trail_t1_after_r * pos.R)
    if trail_on and atr == atr:
        pos.raise_stop(pos.highest_high - ex.trail_atr_mult * atr, "TRAIL", t)
    if pos.setup == T3:
        mid = float(feat_1h_row["bb_mid"])
        if mid == mid:
            pos.target = mid  # target is "touch BB_mid"; follows the current mid
    # Time exit (limits are in hours; bars_held counts trigger-timeframe bars)
    limit_h = ex.time_exit_t3_bars if pos.setup == T3 else min(2 * (pos.H or 12), ex.time_exit_t12_max_bars)
    if pos.bars_held * cfg.data.trigger_hours >= limit_h:
        return "TIME_EXIT"
    # Regime exit
    if symbol_regime in (DOWN, HIGH_VOL, UNKNOWN) or btc_regime == DOWN:
        return "REGIME_EXIT"
    if pos.setup == T3 and symbol_regime not in (RANGE, UP) and not pos.gray_zone:
        return "REGIME_EXIT"
    return None
