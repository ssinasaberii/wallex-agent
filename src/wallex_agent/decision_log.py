"""Decision log (spec 09 §11; challenge deliverable). One JSON line per candidate, rejected ones included,
plus one line per exit. Judge request/response bodies go to judge_calls/<candidate_id>.json. No secrets."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def _clean(x: Any) -> Any:
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else x
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_clean(v) for v in x]
    if hasattr(x, "item"):
        return _clean(x.item())
    return x


class DecisionLog:
    def __init__(self, out_dir: Path | None, header: dict[str, Any]):
        self.header = header  # mode, variant, strategy_version, config_hash, git_commit
        self.records: list[dict[str, Any]] = []
        self.out_dir = out_dir
        self._fh = None
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "judge_calls").mkdir(exist_ok=True)
            self._fh = (out_dir / "decisions.jsonl").open("a", encoding="utf-8")

    def candidate(self, c, extra: dict[str, Any]) -> None:
        rec = {"kind": "candidate", "candidate_id": c.candidate_id, "ts_utc": c.ts, **self.header,
               "symbol": c.symbol, "setup": c.setup, "gray_zone": c.gray_zone,
               "btc_regime": c.btc_regime, "symbol_regime": c.symbol_regime,
               "regime_inputs": c.regime_inputs, "trigger_inputs": c.trigger_inputs, "fee": c.fee, "gate": c.gate,
               "rank_score": c.rank_score, "rank_inputs": c.rank_inputs, "order_flow": c.order_flow,
               "news": extra.get("news", {"blocking_items": [], "context_items": []}),
               "macro_state": extra.get("macro_state"), "fng": extra.get("fng"),
               "judge": c.judge, "risk": c.risk, "final_decision": c.final_decision, "reason": c.reason,
               "order": c.order, "exit": None, "pnl_tmn_net": None}
        self._emit(rec)

    def exit(self, t: int, trade: dict[str, Any] | None, reason: str, symbol: str, extra: dict | None = None) -> None:
        rec = {"kind": "exit", "ts_utc": t, **self.header, "symbol": symbol, "reason": reason,
               "candidate_id": trade.get("candidate_id") if trade else None,
               "exit": {"type": reason, "ts": t, "price": trade.get("exit_price") if trade else None},
               "pnl_tmn_net": trade.get("pnl_tmn") if trade else None, **(extra or {})}
        self._emit(rec)

    def cycle(self, t: int, summary: dict[str, Any]) -> None:
        self._emit({"kind": "cycle", "ts_utc": t, **self.header, **summary})

    def judge_call(self, candidate_id: str, payload: dict[str, Any]) -> None:
        if self.out_dir is not None:
            (self.out_dir / "judge_calls" / f"{candidate_id}.json").write_text(
                json.dumps(_clean(payload), indent=1, ensure_ascii=False), encoding="utf-8")

    def _emit(self, rec: dict[str, Any]) -> None:
        rec = _clean(rec)
        self.records.append(rec)
        if self._fh is not None:
            self._fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
