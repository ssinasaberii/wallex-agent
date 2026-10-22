"""Stop, target and the fee-aware horizon gate (spec 09 §7.5, spec 08). All values are fractions.

cost_rt       = g_be + slip_rt + spread_cost       (full round trip; team answer Q-08)
required_move = cost_rt + safety_buffer + min_net_edge
rr_net        = (target - cost_rt) / (stop + cost_rt)
H             = smallest h in horizons with c_move*ATR%*sqrt(h) >= target, h <= H_max, t + h <= entry cutoff
cost_to_move  = (required_move - min_net_edge) / expected_move(H) <= max_cost_to_move
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from wallex_agent.strategy.triggers import T1, T2, T3


@dataclass
class GateResult:
    ok: bool
    reason: str
    stop_dist: float
    target_dist: float
    cost_rt: float
    required_move: float
    rr_net: float | None
    H: int | None
    expected_move: float | None
    cost_to_move: float | None

    def as_log(self) -> dict:
        def p(x):
            return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x * 100, 4)

        return {"stop_dist_pct": p(self.stop_dist), "target_dist_pct": p(self.target_dist),
                "cost_rt_pct": p(self.cost_rt), "required_move_pct": p(self.required_move),
                "rr_net": None if self.rr_net is None else round(self.rr_net, 4), "H": self.H,
                "expected_move_pct": p(self.expected_move),
                "cost_to_move": None if self.cost_to_move is None else round(self.cost_to_move, 4),
                "economic_viability": "PASS" if self.ok else "FAIL", "reason": self.reason}


def stop_distance(atr_pct: float, stops) -> tuple[float, str]:
    d = stops.k_atr * atr_pct
    d = max(d, stops.min_stop_pct / 100)
    if d > stops.max_stop_pct / 100:
        return d, "STOP_TOO_WIDE"
    return d, ""


def evaluate(setup: str, price: float, atr_pct: float, bb_mid: float | None, t: int, entry_cutoff: int,
             g_be: float, slip_per_side: float, spread_cost: float, cfg) -> GateResult:
    fg, st = cfg.fee_gate, cfg.setups
    nan = float("nan")
    if math.isnan(atr_pct) or atr_pct <= 0:
        return GateResult(False, "NO_ATR", nan, nan, nan, nan, None, None, None, None)
    stop, why = stop_distance(atr_pct, cfg.stops)
    cost_rt = g_be + 2 * slip_per_side + spread_cost
    required = cost_rt + fg.safety_buffer_pct / 100 + fg.min_net_edge_pct / 100
    if why:
        return GateResult(False, why, stop, nan, cost_rt, required, None, None, None, None)
    if setup == T1:
        target, rr_min, h_max = st.T1_pullback.target_r * stop, st.T1_pullback.rr_min, st.T1_pullback.h_max
    elif setup == T2:
        target, rr_min, h_max = st.T2_breakout.target_r * stop, st.T2_breakout.rr_min, st.T2_breakout.h_max
    elif setup == T3:
        if bb_mid is None or math.isnan(bb_mid) or bb_mid <= price:
            return GateResult(False, "NO_TARGET", stop, nan, cost_rt, required, None, None, None, None)
        target, rr_min, h_max = (bb_mid - price) / price, st.T3_meanrev.rr_min, st.T3_meanrev.h_max
    else:
        raise ValueError(setup)
    if not fg.enabled:
        return GateResult(True, "GATE_DISABLED", stop, target, cost_rt, required, None, h_max, None, None)
    if target < required:
        return GateResult(False, "TARGET_BELOW_REQUIRED_MOVE", stop, target, cost_rt, required, None, None, None, None)
    rr = (target - cost_rt) / (stop + cost_rt)
    if rr < rr_min:
        return GateResult(False, "RR_BELOW_MIN", stop, target, cost_rt, required, rr, None, None, None)
    tf = cfg.data.trigger_hours  # ATR% is per trigger bar; H is in hours
    for h in sorted(fg.horizons_h):
        exp = fg.c_move * atr_pct * math.sqrt(h / tf)
        if exp >= target and h <= h_max and t + h * 3600 <= entry_cutoff:
            ctm = (required - fg.min_net_edge_pct / 100) / exp if exp > 0 else float("inf")
            if not math.isfinite(ctm) or ctm > fg.max_cost_to_move:
                return GateResult(False, "COST_TO_MOVE", stop, target, cost_rt, required, rr, h, exp, ctm)
            return GateResult(True, "PASS", stop, target, cost_rt, required, rr, h, exp, ctm)
    return GateResult(False, "NO_HORIZON", stop, target, cost_rt, required, rr, None, None, None)
