"""LayaJudge (ADR-026; 09 §7.9.1): local laya-serve, Jev-compatible POST /v1/systemone.

Input is a compact English state (<= ~300 tokens). Sections are dropped in a fixed priority order until
the state fits; if the server still reports truncation, the result is NO_TRADE (CONTEXT_OVERFLOW).
Token budget is enforced with a conservative character estimate before the call and verified exactly
with the server's `usage.truncated` after it (api-verification V10).
The decision is composed deterministically from atomic questions with thresholds from config.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from wallex_agent.judge.base import JudgeDecision

CHARS_PER_TOKEN = 3.6  # conservative for this English/number mix (sample: 1046 chars -> 266 tokens)

QUESTIONS: dict[str, Any] = {
    "decision": {"type": "choice",
                 "instructions": "Are the evidence items consistent and sufficient to approve this spot entry?",
                 "criteria": {"APPROVE": "evidence is aligned and sufficient for entry",
                              "REJECT": "evidence conflicts or is weak",
                              "DEFER": "information is missing or stale; wait for fresh data"}},
    "signals_conflict": {"type": "noul", "instructions": "Do the main signals contradict each other?"},
    "news_risk": {"type": "noul", "instructions": "Does recent news make a new entry dangerous right now?"},
    "insufficient_data": {"type": "noul", "instructions": "Is important information missing or stale?"},
}
GRAY_Q = {"gray_zone_strong_enough": {
    "type": "noul", "instructions": "The regime is ambiguous. Is the evidence strong enough to enter at half size?"}}


def _fmt(x, nd=2):
    return "na" if x is None else f"{x:.{nd}f}"


def build_state(c, ctx: dict[str, Any]) -> list[tuple[str, str, int]]:
    """Sections as (name, text, drop_priority). Lower priority number = dropped first; 99 = never dropped."""
    ri, ti, g, of = c.regime_inputs, c.trigger_inputs, c.gate, c.order_flow or {}
    pf = ctx.get("portfolio", {})
    news = ctx.get("news_headlines") or []
    sec = [
        ("core", f"Entry candidate {c.candidate_id}. Spot BUY only. Setup {c.setup} on {c.symbol}. "
                 f"Symbol regime 4h: {c.symbol_regime}. BTC regime (BTCUSDT 4h): {c.btc_regime}. "
                 f"gray_zone={str(c.gray_zone).lower()}.", 99),
        ("key", f"4h ADX {_fmt(ri.get('adx'), 1)}, +DI {_fmt(ri.get('pdi'), 1)}, -DI {_fmt(ri.get('mdi'), 1)}. "
                f"1h RSI {_fmt(ti.get('rsi'), 1)}, RelVol {_fmt(ti.get('relvol'))}.", 99),
        ("fee", f"Fee gate: economic_viability={g.get('economic_viability')}, horizon H={g.get('H')}h, "
                f"net reward:risk {_fmt(g.get('rr_net'))}, stop {_fmt(g.get('stop_dist_pct'))}%, "
                f"target {_fmt(g.get('target_dist_pct'))}%.", 99),
        ("of", f"Order flow: OBI {_fmt(of.get('obi'))} ({of.get('status', 'na')}), "
               f"TFI {_fmt(of.get('tfi'))} (log only, direction field unverified).", 4),
        ("secondary", f"4h EMA20 {'>' if (ri.get('ema20') or 0) > (ri.get('ema50') or 0) else '<'} EMA50, "
                      f"ATR% rank {_fmt(ri.get('atr_pct_rank'), 0)}. 1h close vs BB lower/mid: "
                      f"{_fmt(ti.get('close'), 6)} / {_fmt(ti.get('bb_lower'), 6)} / {_fmt(ti.get('bb_mid'), 6)}.", 3),
        ("context", f"Fear&Greed {ctx.get('fng', 'na')}. Macro: {ctx.get('macro_state', 'normal')}. "
                    f"Portfolio: {pf.get('slots_used', 0)} of {pf.get('max_slots', 10)} slots, "
                    f"open risk {pf.get('open_risk_pct', 0)}% of 1.0% cap.", 2),
    ]
    for k, h in enumerate(news[:5]):
        sec.append((f"news{k}", f"News: {h}", 1 if k >= 2 else 5))
    return sec


def fit_state(sections: list[tuple[str, str, int]], max_tokens: int) -> tuple[str | None, list[str]]:
    budget = int(max_tokens * CHARS_PER_TOKEN)
    keep = list(sections)
    dropped: list[str] = []
    while len("\n".join(s[1] for s in keep)) > budget:
        droppable = [s for s in keep if s[2] < 99]
        if not droppable:
            return None, dropped
        victim = min(droppable, key=lambda s: (s[2], -keep.index(s)))
        keep.remove(victim)
        dropped.append(victim[0])
    return "\n".join(s[1] for s in keep), dropped


class LayaJudge:
    provider = "laya"

    def __init__(self, url: str, cfg_judge, timeout_s: float = 20.0):
        self.url = url.rstrip("/")
        self.cfg = cfg_judge
        self.th = cfg_judge.laya.thresholds
        self.client = httpx.Client(base_url=self.url, timeout=timeout_s)
        self.model_version = "laya-unknown"
        try:
            h = self.client.get("/health").json()
            self.model_version = f"laya-0.4.13/{h.get('revisions', {}).get('english', '?')[:12]}"
        except Exception:  # health is informative only; review() fails closed on its own
            pass

    def review(self, c, ctx: dict[str, Any]) -> JudgeDecision:
        state, dropped = fit_state(build_state(c, ctx), self.cfg.laya.max_state_tokens)
        if state is None:
            return JudgeDecision(c.candidate_id, "REJECT", None, ["CONTEXT_OVERFLOW"], [], self.model_version,
                                 time.time(), {"dropped": dropped})
        qs = dict(QUESTIONS) | (GRAY_Q if c.gray_zone else {})
        req = {"state": state, "questions": qs, "model": self.cfg.laya.checkpoint}
        r = self.client.post("/v1/systemone", json=req)
        r.raise_for_status()
        resp = r.json()
        usage = resp.get("usage", {})
        call = {"request": req, "response": resp, "dropped_sections": dropped}
        if usage.get("truncated"):
            return JudgeDecision(c.candidate_id, "REJECT", None, ["CONTEXT_OVERFLOW"], list(qs),
                                 self.model_version, time.time(), {"_call": call})
        a = resp["answers"]
        p = {k: float(a[k]["noul"]) for k in qs if a[k]["type"] == "noul"}
        probs = a["decision"]["probabilities"]
        th = self.th
        if p["insufficient_data"] >= th.insufficient:
            d, rc = "DEFER", ["INSUFFICIENT_DATA"]
        elif p["signals_conflict"] >= th.conflict:
            d, rc = "REJECT", ["SIGNAL_CONFLICT"]
        elif p["news_risk"] >= th.news:
            d, rc = "REJECT", ["EVENT_RISK"]
        elif c.gray_zone and p.get("gray_zone_strong_enough", 0.0) < th.gray:
            d, rc = "REJECT", ["WEAK_CONTEXT"]
        elif probs["APPROVE"] >= th.approve:
            d, rc = "APPROVE", ["EVIDENCE_ALIGNED"]
        else:
            best = max(probs, key=probs.get)
            d, rc = ("DEFER", ["MODEL_UNCERTAIN"]) if probs[best] < th.min else (best, ["MODEL_UNCERTAIN"])
            if d == "APPROVE":  # argmax APPROVE below the approve threshold is still not an approval
                d = "DEFER"
        conf = float(probs.get(d, 0.0)) if d in probs else None
        return JudgeDecision(c.candidate_id, d, conf, rc, [s.split(":")[0] for s in state.split("\n")],
                             self.model_version, time.time(),
                             {"probs": probs, "noul": p, "usage": usage, "_call": call})
