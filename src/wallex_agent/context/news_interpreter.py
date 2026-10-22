"""Headline interpretation with Laya (ADR-026, 09 §7.8): three choice questions and one yes/no question on the
English headline. Laya classifies text; it cannot follow instructions inside a headline, which is treated
as data only. An answer whose best probability is below `min_choice_prob` is recorded as "unclear"."""

from __future__ import annotations

from typing import Any

import httpx

EVENT_TYPES = {"security_incident": "a hack, exploit, theft or security breach",
               "network_outage": "a blockchain or service outage, halt or major bug",
               "regulation": "laws, regulators, enforcement, lawsuits, bans or approvals",
               "project_decision": "an official decision of a crypto project (upgrade, listing, partnership)",
               "tokenomics": "token supply, unlocks, burns, emissions or airdrops",
               "institutional": "funds, ETFs, banks, companies or governments buying, selling or adopting crypto",
               "exchange_wallex": "news about the Wallex exchange or Iranian crypto markets",
               "other": "anything else, including price commentary and analysis"}
QUESTIONS: dict[str, Any] = {
    "event_type": {"type": "choice", "instructions": "What type of event does this crypto headline describe?",
                   "criteria": EVENT_TYPES},
    "direction": {"type": "choice", "instructions": "What is the likely price impact for the crypto assets named?",
                  "criteria": {"negative": "likely to push prices down", "positive": "likely to push prices up",
                               "neutral": "little price impact", "unclear": "impact cannot be judged"}},
    "severity": {"type": "choice", "instructions": "How severe is this event for crypto markets?",
                 "criteria": {"1": "minor, routine news", "2": "moderate, notable for the assets named",
                              "3": "major, market-moving"}},
    "systemic": {"type": "noul", "instructions": "Does this event threaten the whole crypto market, such as a major "
                                                 "exchange hack, a stablecoin losing its peg or a sweeping ban?"},
}


class LayaNewsInterpreter:
    def __init__(self, url: str, min_choice_prob: float, timeout_s: float = 20.0, checkpoint: str = "english"):
        self.client = httpx.Client(base_url=url.rstrip("/"), timeout=timeout_s)
        self.min_p = min_choice_prob
        self.checkpoint = checkpoint

    def interpret(self, title: str) -> dict[str, Any]:
        state = "Headline: " + title[:300]
        r = self.client.post("/v1/systemone", json={"state": state, "questions": QUESTIONS, "model": self.checkpoint})
        r.raise_for_status()
        a = r.json()["answers"]
        out: dict[str, Any] = {"model": "laya-0.4.13/english"}
        for q in ("event_type", "direction", "severity"):
            probs = a[q]["probabilities"]
            best = max(probs, key=probs.get)
            out[q] = best if probs[best] >= self.min_p else "unclear"
            out[q + "_prob"] = round(float(probs[best]), 4)
        p_sys = float(a["systemic"]["noul"])
        out["systemic"] = p_sys >= 0.5
        out["systemic_prob"] = round(p_sys, 4)
        return out
