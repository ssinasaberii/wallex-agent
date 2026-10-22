"""Crypto Fear & Greed index (alternative.me, free, no key). Context for the judge only, never a gate."""

from __future__ import annotations

import time
from typing import Any

import httpx


class FearGreed:
    def __init__(self, url: str, ttl_s: int = 3600):
        self.url, self.ttl = url, ttl_s
        self._val: dict[str, Any] | None = None
        self._at = 0.0

    def get(self) -> dict[str, Any] | None:
        if self._val is None or time.time() - self._at > self.ttl:
            try:
                d = httpx.get(self.url, timeout=10).json()["data"][0]
                self._val = {"value": int(d["value"]), "label": d["value_classification"],
                             "timestamp": int(d["timestamp"])}
                self._at = time.time()
            except Exception:
                return self._val  # keep the last known value; context only
        return self._val
