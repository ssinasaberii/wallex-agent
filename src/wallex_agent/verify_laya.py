"""Phase 0 V10: local Laya server (`laya-serve`, Jev-compatible POST /v1/systemone).

Records: health/model metadata, exact request/response shape for choice + noul questions,
token usage and truncation, and CPU latency p50/p95 over N calls. Talks only to LAYA_URL
(127.0.0.1 by default). Sample data below is synthetic, not a real candidate.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

from wallex_agent import secrets

ROOT = Path(__file__).resolve().parents[2]

SAMPLE_STATE = (
    "Entry candidate C-TEST-0001. Spot BUY only. Strategy v1.2-regime3, setup T1_PULLBACK on ETHTMN.\n"
    "Symbol regime (4h, confirmed): UPTREND. BTC regime (BTCUSDT 4h): UPTREND. gray_zone=false.\n"
    "4h: ADX 29.4, +DI 27.1, -DI 14.8, EMA20>EMA50, EMA50 slope +0.6%, ATR% rank 55.\n"
    "1h: close above EMA20 by 0.4%, last 3 bars low touched EMA20+0.3ATR, RSI min 44 now 53, "
    "RelVol 1.3, bullish candle.\n"
    "Fee gate: economic_viability=PASS, horizon H=8h, net reward:risk 1.9, stop 1.8%, target 3.6%.\n"
    "Order flow: OBI +0.12 (5 snapshots), TFI unavailable (direction field unverified).\n"
    "News: none blocking. Fear&Greed 58 (greed). Macro: no event within 12h.\n"
    "Portfolio: 2 of 10 slots used, open risk 0.4% of 1.0% cap."
)

JUDGE_QUESTIONS: dict[str, Any] = {
    "decision": {
        "type": "choice",
        "instructions": "Are the evidence items consistent and sufficient to approve this spot entry candidate?",
        "criteria": {
            "APPROVE": "evidence is aligned and sufficient for entry",
            "REJECT": "evidence conflicts or is weak",
            "DEFER": "information is missing or stale; wait for fresh data",
        },
    },
    "signals_conflict": {"type": "noul", "instructions": "Do the main signals contradict each other?"},
    "news_risk": {"type": "noul", "instructions": "Does recent news make a new entry dangerous right now?"},
    "insufficient_data": {"type": "noul", "instructions": "Is important information missing or stale?"},
}
GRAY_QUESTION = {"gray_zone_strong_enough": {
    "type": "noul",
    "instructions": "The regime is ambiguous (gray zone). Is the evidence strong enough to enter at half size?"}}

NEWS_STATE = "Headline: Major exchange halts withdrawals after suspected hot wallet exploit"
NEWS_QUESTIONS: dict[str, Any] = {
    "event_type": {"type": "choice", "instructions": "What type of event does this crypto headline describe?",
                   "criteria": {"security_incident": "", "network_outage": "", "regulation": "",
                                "project_decision": "", "tokenomics": "", "institutional": "",
                                "exchange_wallex": "", "other": ""}},
    "direction": {"type": "choice", "instructions": "What is the likely price impact for the affected assets?",
                  "criteria": {"negative": "", "positive": "", "neutral": "", "unclear": ""}},
    "severity": {"type": "choice", "instructions": "How severe is this event for crypto markets?",
                 "criteria": {"1": "minor", "2": "moderate", "3": "major"}},
}


def _post(c: httpx.Client, state: str, questions: dict[str, Any]) -> tuple[dict[str, Any], float, str | None]:
    t0 = time.perf_counter()
    r = c.post("/v1/systemone", json={"state": state, "questions": questions, "model": "english"})
    ms = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    return r.json(), ms, r.headers.get("x-inference-time-ms")


def run(n: int = 50) -> dict[str, Any]:
    url = secrets.get("LAYA_URL") or "http://127.0.0.1:8000"
    out_dir = ROOT / "data" / "snapshots" / "verify_laya"
    out_dir.mkdir(parents=True, exist_ok=True)
    res: dict[str, Any] = {"url": url}
    with httpx.Client(base_url=url, timeout=60) as c:
        h = c.get("/health")
        res["health_status"] = h.status_code
        res["health"] = h.json() if h.headers.get("content-type", "").startswith("application/json") else h.text[:300]

        first, ms, srv = _post(c, SAMPLE_STATE, JUDGE_QUESTIONS | GRAY_QUESTION)
        (out_dir / "judge_request.json").write_text(json.dumps(
            {"state": SAMPLE_STATE, "questions": JUDGE_QUESTIONS | GRAY_QUESTION, "model": "english"}, indent=1))
        (out_dir / "judge_response.json").write_text(json.dumps(first, indent=1))
        res["judge_first_call_ms"] = round(ms)
        res["judge_response_top_keys"] = sorted(first.keys())
        res["judge_model"] = first.get("model")
        res["judge_usage"] = first.get("usage")
        res["judge_answers"] = first.get("answers")

        news, ms_n, _ = _post(c, NEWS_STATE, NEWS_QUESTIONS)
        (out_dir / "news_response.json").write_text(json.dumps(news, indent=1))
        res["news_answers"] = {k: {kk: v.get(kk) for kk in ("choice", "confidence", "probabilities")}
                               for k, v in news.get("answers", {}).items()}
        res["news_call_ms"] = round(ms_n)

        lat, srv_lat = [], []
        for _ in range(n):
            _, ms, srv = _post(c, SAMPLE_STATE, JUDGE_QUESTIONS)
            lat.append(ms)
            if srv:
                srv_lat.append(float(srv))
        q = statistics.quantiles(lat, n=20)
        res["latency_judge_4q_ms"] = {"n": n, "p50": round(statistics.median(lat)), "p95": round(q[18]),
                                     "max": round(max(lat))}
        if srv_lat:
            res["server_inference_ms"] = {"p50": round(statistics.median(srv_lat)),
                                          "p95": round(statistics.quantiles(srv_lat, n=20)[18])}
        # Determinism: same input twice -> same probabilities?
        a, _, _ = _post(c, SAMPLE_STATE, JUDGE_QUESTIONS)
        b, _, _ = _post(c, SAMPLE_STATE, JUDGE_QUESTIONS)
        res["deterministic"] = a.get("answers") == b.get("answers")
        # Truncation: a state far above 512 tokens.
        big, _, _ = _post(c, SAMPLE_STATE * 8, {"decision": JUDGE_QUESTIONS["decision"]})
        res["oversize_usage"] = big.get("usage")
    (out_dir / "summary.json").write_text(json.dumps(res, indent=1, default=str))
    return res
