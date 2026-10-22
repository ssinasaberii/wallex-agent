"""Phase 0 V11: one short BoxAPI chat completion (OpenAI-compatible). Records model, latency,
JSON-mode support and token usage. Never logs the key."""

from __future__ import annotations

import json
import time
from typing import Any

from wallex_agent import secrets

PRICE_PER_1M = {"gpt-6-luna": (0.115, 0.575)}  # USD in/out, boxapi.ir/docs/ai/models, 2026-10-22, assumed


def run() -> dict[str, Any]:
    from openai import OpenAI

    secrets.install_log_redaction()
    if not secrets.has("LLM_API_KEY"):
        return {"status": "pending", "reason": "LLM_API_KEY not set"}
    model = secrets.get("LLM_MODEL", "gpt-6-luna")
    client = OpenAI(api_key=secrets.get("LLM_API_KEY"), base_url=secrets.get("LLM_BASE_URL"), timeout=20)
    msgs = [{"role": "system", "content": "Reply with JSON only."},
            {"role": "user", "content": 'Return {"decision": "DEFER", "ok": true}.'}]
    out: dict[str, Any] = {"model_requested": model}
    for mode in ("json_object", "plain"):
        # gpt-6-luna rejects temperature=0 ("only the default (1) value is supported"), see api-verification V11.
        kw: dict[str, Any] = {"model": model, "messages": msgs, "max_completion_tokens": 300}
        if mode == "json_object":
            kw["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        try:
            r = client.chat.completions.create(**kw)
            ms = round((time.perf_counter() - t0) * 1000)
            txt = r.choices[0].message.content or ""
            try:
                parsed: Any = json.loads(txt)
            except ValueError:
                parsed = None
            u = r.usage
            pin, pout = PRICE_PER_1M.get(model, (None, None))
            cost = (u.prompt_tokens * pin + u.completion_tokens * pout) / 1e6 if u and pin else None
            out[mode] = {"status": "ok", "latency_ms": ms, "model_returned": r.model, "content": txt[:200],
                         "json_parsed": parsed is not None,
                         "usage": u.model_dump() if u else None, "est_cost_usd": cost}
        except Exception as e:  # noqa: BLE001
            out[mode] = {"status": "error", "error": secrets.RedactingFilter.redact(f"{type(e).__name__}: {e}")[:300]}
    return out
