"""Acceptance test 6 (09 §13): timeout, invalid JSON/object, mismatched id and expired answers all give
NO_TRADE; no SELL path goes through the judge."""

import inspect
import time
from types import SimpleNamespace

from wallex_agent.judge.base import JudgeDecision, safe_review

CAND = SimpleNamespace(candidate_id="C-1", gray_zone=False)


class J:
    provider = "test"

    def __init__(self, fn):
        self.fn = fn

    def review(self, c, ctx):
        return self.fn(c)


def ok(c, decision="APPROVE", cid=None):
    return JudgeDecision(cid or c.candidate_id, decision, 0.9, ["EVIDENCE_ALIGNED"], [], "m1", time.time())


def run(fn, **kw):
    return safe_review(J(fn), CAND, {}, kw.get("timeout", 1.0), kw.get("ttl", 600), kw.get("created", time.time()))


def test_approve_passes():
    assert run(ok)["approved"]


def test_timeout_is_no_trade():
    r = run(lambda c: (time.sleep(0.5), ok(c))[1], timeout=0.1)
    assert not r["approved"] and r["error"] == "TIMEOUT"


def test_invalid_response_is_no_trade():
    assert run(lambda c: {"decision": "APPROVE"})["error"] == "INVALID_RESPONSE"
    assert run(lambda c: ok(c, decision="BUY_NOW"))["error"] == "INVALID_DECISION"


def test_exception_is_no_trade():
    def boom(c):
        raise ValueError("bad json")

    r = run(boom)
    assert not r["approved"] and r["error"].startswith("ERROR")


def test_mismatched_id_is_no_trade():
    assert run(lambda c: ok(c, cid="C-other"))["error"] == "ID_MISMATCH"


def test_expired_is_no_trade():
    r = run(ok, ttl=10, created=time.time() - 11)
    assert not r["approved"] and r["error"] == "EXPIRED"


def test_reject_and_defer_are_not_approvals():
    assert not run(lambda c: ok(c, "REJECT"))["approved"]
    assert not run(lambda c: ok(c, "DEFER"))["approved"]


def test_no_sell_path_uses_the_judge():
    from wallex_agent.engine import decision_cycle
    from wallex_agent.execution import backtest_sim
    from wallex_agent.positions import manager

    src = inspect.getsource(decision_cycle.run_cycle)
    sell_part = src[src.index("# ---- 2/3."):src.index("# ---- 4.")]
    assert "judge" not in sell_part and "safe_review" not in sell_part
    for mod in (manager, backtest_sim):
        code = [ln for ln in inspect.getsource(mod).splitlines() if ln.startswith(("import", "from"))]
        assert not any("judge" in ln for ln in code), mod.__name__
