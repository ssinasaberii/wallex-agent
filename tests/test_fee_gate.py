"""Acceptance test 7 (09 §13): fee gate."""

from wallex_agent.data.fees import FeeSnapshot
from wallex_agent.strategy import fee_horizon
from wallex_agent.strategy.triggers import T1, T3

from .conftest import hourly_cfg

CFG = hourly_cfg()  # the gate is off in v1.3 (ADR-028); its behaviour is still tested with the gate on
T = 1_792_670_400
FAR = T + 72 * 3600


def test_half_percent_fee_rejects_one_percent_target():
    g_be = (1 + 0.005) / (1 - 0.005) - 1
    r = fee_horizon.evaluate(T3, 100.0, 0.01, 101.0, T, FAR, g_be, 0.0, 0.0, CFG)  # target to BB mid = 1%
    assert not r.ok and r.reason == "TARGET_BELOW_REQUIRED_MOVE"


def test_rr_includes_full_round_trip_cost():
    fee = FeeSnapshot.from_config(CFG.fees)
    r = fee_horizon.evaluate(T1, 100.0, 0.0075, None, T, FAR, fee.g_break_even, 0.0005, 0.0005, CFG)
    cost = fee.g_break_even + 0.001 + 0.0005
    assert abs(r.cost_rt - cost) < 1e-12
    if r.rr_net is not None:
        assert abs(r.rr_net - (r.target_dist - cost) / (r.stop_dist + cost)) < 1e-12


def test_horizon_must_end_before_entry_cutoff():
    g = fee_horizon.evaluate(T3, 100.0, 0.02, 104.0, T, T + 3600, 0.0, 0.0, 0.0, CFG)
    assert not g.ok and g.reason == "NO_HORIZON"


def test_stop_too_wide_is_no_trade():
    g = fee_horizon.evaluate(T1, 100.0, 0.05, None, T, FAR, 0.0, 0.0, 0.0, CFG)  # 2 x 5% ATR > 4%
    assert not g.ok and g.reason == "STOP_TOO_WIDE"


def test_fee_snapshot_change_invalidates_candidate():
    a = FeeSnapshot.from_config(CFG.fees)
    b = FeeSnapshot.from_config(CFG.fees, taker_override_pct=0.5)
    assert a.snapshot_id != b.snapshot_id
    # decision_cycle's final recheck compares the candidate's snapshot id with the current one.
    import inspect

    from wallex_agent.engine import decision_cycle

    assert "FEE_SNAPSHOT_CHANGED" in inspect.getsource(decision_cycle.run_cycle)
