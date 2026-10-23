"""Challenge market eligibility from config/allowed_markets.yaml (BLOCK-002)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = ROOT / "config" / "allowed_markets.yaml"


class MarketRules:
    def __init__(self, mode: str, symbols: set[str], source: str):
        if mode not in ("denylist", "allowlist", "all"):
            raise ValueError(f"unknown allowed_markets mode {mode!r}")
        self.mode, self.symbols, self.source = mode, symbols, source

    def is_allowed(self, symbol: str) -> bool:
        if self.mode == "all":
            return True
        inside = symbol in self.symbols
        return not inside if self.mode == "denylist" else inside


def load_rules(path: Path = DEFAULT_PATH) -> MarketRules:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mode = raw.get("mode", "all")
    key = "excluded" if mode == "denylist" else "allowed"
    syms = {s.replace("/", "").upper() for s in (raw.get(key) or [])}
    return MarketRules(mode, syms, raw.get("source", ""))
