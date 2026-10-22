"""RulesBaselineJudge: approves every candidate; gray-zone candidates follow `baseline_gray_policy`
(default reject). The only judge used in backtests (09 §7.9)."""

from __future__ import annotations

import time

from wallex_agent.judge.base import JudgeDecision


class RulesBaselineJudge:
    provider = "rules_baseline"

    def __init__(self, gray_policy: str = "reject"):
        self.gray_policy = gray_policy

    def review(self, candidate, context) -> JudgeDecision:
        if candidate.gray_zone and self.gray_policy == "reject":
            return JudgeDecision(candidate.candidate_id, "REJECT", 1.0, ["GRAY_ZONE_POLICY"], ["gray_zone"],
                                 "rules-baseline-1", time.time())
        return JudgeDecision(candidate.candidate_id, "APPROVE", 1.0, ["EVIDENCE_ALIGNED"], ["setup", "gate"],
                             "rules-baseline-1", time.time())
