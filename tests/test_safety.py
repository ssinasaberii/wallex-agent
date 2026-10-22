"""Safety invariants (spec 09 §0, CLAUDE.md): read-only client, no order paths in code, secrets redacted."""

import logging
import re
from pathlib import Path

import pytest

from wallex_agent.data.wallex_rest import ALLOWED_PATHS, ForbiddenEndpoint, WallexRest
from wallex_agent.secrets import RedactingFilter

SRC = Path(__file__).resolve().parents[1] / "src" / "wallex_agent"
# Modules allowed to POST, and only to a model server (local Laya or the LLM provider), never to Wallex.
MODEL_CLIENT_FILES = {"verify_laya.py", "laya.py", "llm_judge.py", "news_interpreter.py"}


@pytest.mark.parametrize("path", [
    "/v1/account/orders", "/v1/account/otc/orders", "/v1/account/otc/price",
    "/v1/account/withdraw", "/v1/account/balances", "/v2/account/orders", "/v1/markets/../account/orders",
])
def test_client_refuses_non_allowlisted_paths(path):
    with pytest.raises(ForbiddenEndpoint):
        WallexRest.check_path(path)


def test_allowlist_has_no_order_like_paths():
    for p in ALLOWED_PATHS:
        assert not any(f in p.lower() for f in ("order", "otc", "withdraw", "transfer"))


def test_client_has_no_write_methods():
    for name in ("post", "put", "patch", "delete"):
        assert not hasattr(WallexRest, name)


_WRITE_CALL = re.compile(r"\.(post|put|patch|delete)\s*\(", re.IGNORECASE)
_BAD_PATH = re.compile(r"/v\d/account/(orders|otc)|/v\d/[\w/]*withdraw", re.IGNORECASE)


def test_no_write_http_calls_or_order_endpoints_in_source():
    offenders = []
    for f in SRC.rglob("*.py"):
        if f.name == "live.py":  # LiveBroker: interface only, raises NotImplementedError
            continue
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if _WRITE_CALL.search(line) and "httpx" not in line and "requests" not in line:
                if f.name in MODEL_CLIENT_FILES and "wallex" not in line.lower():
                    continue
                offenders.append(f"{f.name}:{i}: {line.strip()}")
            if _BAD_PATH.search(line) and f.name not in ("wallex_rest.py",):
                offenders.append(f"{f.name}:{i}: {line.strip()}")
    assert not offenders, offenders


def test_redacting_filter_masks_registered_secret(caplog):
    secret = "box-ai-TESTSECRET0123456789"
    RedactingFilter.register(secret)
    logger = logging.getLogger("redaction-test")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="redaction-test"):
        logger.info("calling with key=%s", secret)
    assert secret not in caplog.text
    assert "***" in caplog.text
