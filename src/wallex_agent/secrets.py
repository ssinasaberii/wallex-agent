"""Secret loading and log redaction.

Secrets come only from `.env` (never from config files). Every value loaded here is
registered with `RedactingFilter`, which replaces it with `***` in all log records.
Nothing in this module prints or returns secrets in reprs.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import dotenv_values

SECRET_KEYS = ("WALLEX_API_KEY", "LLM_API_KEY", "JEV_API_KEY")
_ROOT = Path(__file__).resolve().parents[2]
_loaded: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _loaded
    if _loaded is None:
        values = {k: v for k, v in dotenv_values(_ROOT / ".env").items() if v}
        # Process environment overrides .env (useful for CI / judges).
        for k in list(values) + list(SECRET_KEYS):
            if os.environ.get(k):
                values[k] = os.environ[k]
        _loaded = values
        for k in SECRET_KEYS:
            if values.get(k):
                RedactingFilter.register(values[k])
    return _loaded


def get(name: str, default: str | None = None) -> str | None:
    return _load().get(name, default)


def has(name: str) -> bool:
    return bool(_load().get(name))


class RedactingFilter(logging.Filter):
    _secrets: set[str] = set()

    @classmethod
    def register(cls, value: str) -> None:
        if value and len(value) >= 8:
            cls._secrets.add(value)

    @classmethod
    def redact(cls, text: str) -> str:
        for s in cls._secrets:
            text = text.replace(s, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        red = self.redact(msg)
        if red != msg:
            record.msg, record.args = red, None
        return True


def install_log_redaction() -> None:
    _load()
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for f in root.filters):
        root.addFilter(RedactingFilter())
    for h in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in h.filters):
            h.addFilter(RedactingFilter())
