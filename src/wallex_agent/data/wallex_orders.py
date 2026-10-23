"""Wallex spot order client for the challenge's credit sub-account (PLAN_LIVE.md, api-verification V7/V8).

The only module allowed to place or cancel orders. Safety invariants (tests/test_live.py):
- an exact allowlist: place / cancel / status of spot orders, open orders, balances. OTC, withdrawal, transfer,
  card, IBAN and margin paths are refused before any request is built;
- dry run by default: POST and DELETE are logged and answered locally, nothing is sent;
- sending needs BOTH `live.dry_run: false` in config AND `.env` WALLEX_LIVE_APPROVAL == APPROVAL_PHRASE, which
  only the user sets;
- keys come from `.env` only (`x-api-key`, `sub-account-client-id`) and are redacted from every log record.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import httpx

from wallex_agent import secrets

log = logging.getLogger(__name__)

BASE_URL = "https://api.wallex.ir"
APPROVAL_PHRASE = "I-APPROVE-LIVE-ORDERS"
_CID = re.compile(r"^[A-Za-z0-9_]{1,64}$")
# (method, path pattern) -> exact allowlist; {cid} is a client order id
_ROUTES = {
    ("POST", "/v1/account/orders"),
    ("DELETE", "/v1/account/orders/{cid}"),
    ("GET", "/v1/account/orders/{cid}"),
    ("GET", "/v1/account/openOrders"),
    ("GET", "/v1/account/balances"),
}
_FORBIDDEN = ("otc", "withdraw", "transfer", "card", "iban", "margin", "prop", "sub-accounts", "convert", "..")


class ForbiddenOrderRoute(RuntimeError):
    pass


class LiveNotApproved(RuntimeError):
    pass


def safe_client_id(raw: str) -> str:
    """Wallex accepts letters, digits and '_' only; ids stay deterministic so a retry never makes a duplicate."""
    cid = re.sub(r"[^A-Za-z0-9_]", "_", raw)[:64]
    if not _CID.match(cid):
        raise ValueError("bad client id")
    return cid


def check_route(method: str, path: str) -> None:
    low = path.lower()
    if any(f in low for f in _FORBIDDEN):
        raise ForbiddenOrderRoute(f"{method} {path}")
    if (method, path) in _ROUTES:
        return
    m = re.fullmatch(r"/v1/account/orders/([A-Za-z0-9_]{1,64})", path)
    if m and (method, "/v1/account/orders/{cid}") in _ROUTES:
        return
    raise ForbiddenOrderRoute(f"{method} {path}")


def fmt(x: float, decimals: int) -> str:
    """Number as Wallex expects it: a string, floored to the market's decimals, no exponent."""
    f = 10 ** max(decimals, 0)
    v = int(x * f + 1e-9) / f
    return f"{v:.{max(decimals, 0)}f}"


class WallexOrders:
    def __init__(self, dry_run: bool = True, base_url: str = BASE_URL, timeout_s: float = 15.0,
                 transport: httpx.BaseTransport | None = None, min_interval_s: float = 0.6):
        if not dry_run and secrets.get("WALLEX_LIVE_APPROVAL") != APPROVAL_PHRASE:
            raise LiveNotApproved("live orders need .env WALLEX_LIVE_APPROVAL set by the user (PLAN_LIVE.md)")
        key, sub = secrets.get("WALLEX_API_KEY"), secrets.get("WALLEX_SUB_ACCOUNT_CLIENT_ID")
        if not key or not sub:
            raise LiveNotApproved("WALLEX_API_KEY and WALLEX_SUB_ACCOUNT_CLIENT_ID must be set in .env")
        self.dry_run = dry_run
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s, transport=transport,
                                    headers={"x-api-key": key, "sub-account-client-id": sub,
                                             "Content-Type": "application/json", "Accept": "application/json",
                                             "User-Agent": "wallex-agent/0.2 (live)"})
        self._lock = threading.Lock()
        self._last = 0.0
        self._min_interval = min_interval_s  # Wallex: at most 20 order requests per 10 s
        self.sent: list[dict[str, Any]] = []  # every request (dry or real), without headers

    def _request(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
        check_route(method, path)
        rec = {"t": time.time(), "method": method, "path": path, "body": body, "dry_run": self.dry_run}
        self.sent.append(rec)
        if self.dry_run and method in ("POST", "DELETE"):
            log.info("DRY RUN %s %s %s", method, path, body)
            return {"dry_run": True}
        with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        r = self._client.request(method, path, json=body, params=params)
        rec["status"] = r.status_code
        if r.status_code >= 500:
            raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
        try:
            j = r.json()
        except ValueError:
            j = {"success": False, "message": r.text[:200]}
        j["_http"] = r.status_code
        return j

    # ------------------------------------------------------------------ orders
    def place(self, symbol: str, side: str, qty: str, price: str, client_id: str) -> dict:
        body = {"symbol": symbol, "side": side, "type": "LIMIT", "price": price, "quantity": qty,
                "client_id": safe_client_id(client_id)}
        return self._request("POST", "/v1/account/orders", body)

    def cancel(self, client_id: str) -> dict:
        return self._request("DELETE", f"/v1/account/orders/{safe_client_id(client_id)}")

    def status(self, client_id: str) -> dict:
        return self._request("GET", f"/v1/account/orders/{safe_client_id(client_id)}")

    def open_orders(self) -> dict:
        return self._request("GET", "/v1/account/openOrders", params={"per_page": 100})

    def balances(self) -> dict[str, dict[str, float]]:
        j = self._request("GET", "/v1/account/balances")
        if j.get("_http") != 200:
            raise RuntimeError(f"balances failed: {j.get('_http')}")
        out = {}
        for k, v in (j.get("result") or {}).get("balances", {}).items():
            val, locked = float(v.get("value") or 0), float(v.get("locked") or 0)
            if val > 0:
                out[k] = {"value": val, "locked": locked, "free": val - locked}
        return out

    def close(self) -> None:
        self._client.close()
