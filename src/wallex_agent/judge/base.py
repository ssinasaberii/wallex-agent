"""Entry judge contract and fail-closed wrapper (spec 09 §7.9, 03 §5 and §8).

The judge sees only entry candidates. It never sees or controls a SELL. Any error, timeout, invalid
output, mismatched id or an answer received after the candidate expired becomes NO_TRADE.
Confidence never changes position size.
"""

from __future__ import annotations

import concurrent.futures as cf
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

DECISIONS = {"APPROVE", "REJECT", "DEFER"}
REASON_CODES = {"EVIDENCE_ALIGNED", "SIGNAL_CONFLICT", "EVENT_RISK", "WEAK_CONTEXT", "INSUFFICIENT_DATA",
                "STALE_INFORMATION", "MODEL_UNCERTAIN", "CONTEXT_OVERFLOW", "GRAY_ZONE_POLICY"}


@dataclass
class JudgeDecision:
    candidate_id: str
    decision: str
    confidence: float | None
    reason_codes: list[str]
    evidence_references: list[str]
    model_version: str
    decision_timestamp: float
    extra: dict[str, Any] = field(default_factory=dict)


class EntryJudge(Protocol):
    provider: str

    def review(self, candidate, context: dict[str, Any]) -> JudgeDecision: ...


_POOL = cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="judge")


def safe_review(judge: EntryJudge, candidate, context: dict[str, Any], timeout_s: float, ttl_s: float,
                created_wall: float, now_fn=time.time) -> dict[str, Any]:
    """Run the judge with fail-closed rules. Returns the judge section of the decision log."""
    t0 = time.perf_counter()
    out: dict[str, Any] = {"provider": judge.provider, "model_version": None, "decision": None, "confidence": None,
                           "reason_codes": [], "latency_ms": None, "error": None, "approved": False}
    try:
        fut = _POOL.submit(judge.review, candidate, context)
        d = fut.result(timeout=timeout_s)
    except cf.TimeoutError:
        out["error"] = "TIMEOUT"
        return out
    except Exception as e:  # any judge failure -> NO_TRADE
        out["error"] = f"ERROR:{type(e).__name__}"
        return out
    finally:
        out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    if not isinstance(d, JudgeDecision):
        out["error"] = "INVALID_RESPONSE"
        return out
    out.update({"model_version": d.model_version, "decision": d.decision, "confidence": d.confidence,
                "reason_codes": list(d.reason_codes), "evidence_references": list(d.evidence_references),
                "extra": d.extra})
    if d.candidate_id != candidate.candidate_id:
        out["error"] = "ID_MISMATCH"
    elif d.decision not in DECISIONS:
        out["error"] = "INVALID_DECISION"
    elif any(rc not in REASON_CODES for rc in d.reason_codes):
        out["error"] = "INVALID_REASON_CODE"
    elif now_fn() - created_wall > ttl_s:
        out["error"] = "EXPIRED"
    else:
        out["approved"] = d.decision == "APPROVE"
    return out


def decision_dict(d: JudgeDecision) -> dict[str, Any]:
    return asdict(d)
