"""Read-only Wallex REST client.

Safety invariant (spec 09 §0): this client can only issue HTTP GET, and only to an
explicit allowlist of paths. Order, OTC and withdrawal endpoints are refused even for GET.
The API key header is attached only to `/v1/account/fee`.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from wallex_agent import secrets

log = logging.getLogger(__name__)

BASE_URL = "https://api.wallex.ir"

ALLOWED_PATHS = frozenset(
    {
        "/v1/markets",
        "/hector/web/v1/markets",
        "/v1/udf/history",
        "/v1/depth",
        "/v2/depth/all",
        "/v1/trades",
        "/v1/account/fee",
    }
)
AUTH_PATHS = frozenset({"/v1/account/fee"})
FORBIDDEN_FRAGMENTS = ("order", "otc", "withdraw", "transfer", "convert")


class ForbiddenEndpoint(RuntimeError):
    pass


@dataclass
class RateLimitState:
    limit: int | None = None
    remaining: int | None = None
    last_429_at: float | None = None
    count_429: int = 0
    requests: int = 0
    errors: int = 0
    samples: list[dict[str, Any]] = field(default_factory=list)


class WallexRest:
    def __init__(self, base_url: str = BASE_URL, min_interval_s: float = 0.15, timeout_s: float = 20.0,
                 max_retries: int = 3):
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s,
                                    headers={"User-Agent": "wallex-agent/0.1 (read-only)"})
        self._min_interval = min_interval_s
        self._max_retries = max_retries
        self._lock = threading.Lock()
        self._last_call = 0.0
        self.rate = RateLimitState()

    @staticmethod
    def check_path(path: str) -> None:
        low = path.lower()
        if path not in ALLOWED_PATHS or any(f in low for f in FORBIDDEN_FRAGMENTS):
            raise ForbiddenEndpoint(f"path not allowed for read-only client: {path}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        self.check_path(path)
        headers = {}
        if path in AUTH_PATHS:
            key = secrets.get("WALLEX_API_KEY")
            if not key:
                raise RuntimeError("WALLEX_API_KEY missing")
            headers["x-api-key"] = key
        attempt = 0
        while True:
            with self._lock:
                wait = self._min_interval - (time.monotonic() - self._last_call)
                if wait > 0:
                    time.sleep(wait)
                self._last_call = time.monotonic()
            self.rate.requests += 1
            try:
                resp = self._client.get(path, params=params, headers=headers)
            except httpx.HTTPError as e:
                self.rate.errors += 1
                attempt += 1
                if attempt > self._max_retries:
                    raise
                log.warning("GET %s failed (%s), retry %d", path, type(e).__name__, attempt)
                time.sleep(min(2**attempt, 10))
                continue
            self._record_rate(resp)
            if resp.status_code == 429 or resp.status_code >= 500:
                if resp.status_code == 429:
                    self.rate.count_429 += 1
                    self.rate.last_429_at = time.time()
                attempt += 1
                if attempt > self._max_retries:
                    return resp
                retry_after = resp.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt, 15)
                log.warning("GET %s -> %d, retry %d in %.1fs", path, resp.status_code, attempt, delay)
                time.sleep(delay)
                continue
            return resp

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self.get(path, params)
        resp.raise_for_status()
        return resp.json()

    def _record_rate(self, resp: httpx.Response) -> None:
        lim = resp.headers.get("x-ratelimit-limit")
        rem = resp.headers.get("x-ratelimit-remaining")
        if lim and lim.isdigit():
            self.rate.limit = int(lim)
        if rem and rem.lstrip("-").isdigit():
            self.rate.remaining = int(rem)
        if len(self.rate.samples) < 500:
            self.rate.samples.append({"t": time.time(), "path": resp.request.url.path, "status": resp.status_code,
                                      "limit": lim, "remaining": rem,
                                      "retry_after": resp.headers.get("retry-after")})

    def close(self) -> None:
        self._client.close()
