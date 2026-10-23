"""LiveBroker and the order client against a mock Wallex server (PLAN_LIVE.md safety checklist). No network."""

import json
import time

import httpx
import pytest

from wallex_agent import secrets
from wallex_agent.config import load_config
from wallex_agent.data import wallex_orders as wo
from wallex_agent.data.market_meta import MarketMeta
from wallex_agent.decision_log import DecisionLog
from wallex_agent.execution.live import LiveBroker, parse_fills
from wallex_agent.risk.portfolio import Order, Portfolio

CFG = load_config(apply_env=False)
META = MarketMeta("AAATMN", "AAA", "TMN", True, (), 50000, 1e-5, 5, 0, True, 999000, 1000000, 0.1, 1e10)
PLAN = {"setup": "T2_breakout", "stop_dist": 0.06, "target_dist": None, "cost_rt": 0.007, "g_be": 0.007, "H": 48,
        "gray_zone": False, "candidate_id": "c1", "scale_out": []}


@pytest.fixture
def env(monkeypatch):
    vals = {"WALLEX_API_KEY": "1|fakekeyfakekey", "WALLEX_SUB_ACCOUNT_CLIENT_ID": "sub-fake"}
    monkeypatch.setattr(secrets, "get", lambda k, d=None: vals.get(k, d))
    return vals


class Mock:
    """Minimal Wallex: orders fill at the limit price with the fee in the base coin (buy) or TMN (sell)."""

    def __init__(self, fill=True, timeout_on_place=0, wallet_aaa=None):
        self.requests, self.orders = [], {}
        self.fill, self.timeouts, self.wallet_aaa = fill, timeout_on_place, wallet_aaa

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        self.requests.append((req.method, req.url.path, body, dict(req.headers)))
        p = req.url.path
        if req.method == "POST" and p == "/v1/account/orders":
            if self.timeouts > 0:
                self.timeouts -= 1
                self.orders[body["client_id"]] = self._result(body)  # it reached the exchange
                raise httpx.ReadTimeout("slow", request=req)
            res = self._result(body)
            self.orders[body["client_id"]] = res
            return httpx.Response(201, json={"success": True, "message": "ok", "result": res})
        if req.method == "GET" and p.startswith("/v1/account/orders/"):
            cid = p.rsplit("/", 1)[1]
            if cid not in self.orders:
                return httpx.Response(404, json={"success": False, "message": "not found"})
            return httpx.Response(200, json={"success": True, "result": self.orders[cid]})
        if req.method == "DELETE" and p.startswith("/v1/account/orders/"):
            cid = p.rsplit("/", 1)[1]
            self.orders[cid] = {**self.orders[cid], "status": "CANCELED", "active": False}
            return httpx.Response(200, json={"success": True, "result": self.orders[cid]})
        if req.method == "GET" and p == "/v1/account/balances":
            aaa = self.wallet_aaa if self.wallet_aaa is not None else 1.0
            return httpx.Response(200, json={"success": True, "result": {"balances": {
                "TMN": {"value": "5000000", "locked": "0"}, "AAA": {"value": str(aaa), "locked": "0"}}}})
        if req.method == "GET" and p == "/v1/account/openOrders":
            return httpx.Response(200, json={"success": True, "result": {"orders": []}})
        return httpx.Response(400, text="Bad Request")

    def _result(self, b):
        q, px = float(b["quantity"]), float(b["price"])
        if not self.fill:
            return {"clientOrderId": b["client_id"], "status": "NEW", "active": True, "executedQty": "0",
                    "executedPercent": 0, "fills": []}
        fee, asset = (q * 0.003, "AAA") if b["side"] == "BUY" else (q * px * 0.003, "TMN")
        return {"clientOrderId": b["client_id"], "status": "FILLED", "active": False, "executedQty": str(q),
                "executedPercent": 100, "fills": [{"quantity": str(q), "price": str(px), "fee": str(fee),
                                                   "feeAsset": asset}]}


def books(ask=1_000_000, bid=999_000):
    return {"AAATMN": {"ask": [{"price": str(ask), "quantity": "10"}], "bid": [{"price": str(bid), "quantity": "10"}]}}


def make(mock, dry=False, **live):
    cl = wo.WallexOrders.__new__(wo.WallexOrders)  # bypass the approval check only inside the mock test
    cl.dry_run, cl.sent, cl._min_interval, cl._last = dry, [], 0.0, 0.0
    import threading

    cl._lock = threading.Lock()
    cl._client = httpx.Client(base_url="https://api.wallex.ir", transport=httpx.MockTransport(mock),
                              headers={"x-api-key": "k", "sub-account-client-id": "s"})
    pf = Portfolio(5_000_000, CFG.risk, int(time.time()), int(time.time()) + 86400)
    lcfg = CFG.live.model_copy(update=live)
    return LiveBroker(pf, 0.003, books(), DecisionLog(None, {"mode": "test"}), cl, {"AAATMN": META}, lcfg), pf


def buy(br, pf, qty=0.3):
    r, why = pf.try_reserve("AAA", "AAATMN", qty * 1_010_000, 1000, 5_000_000, dict(PLAN))
    assert r is not None, why
    o = Order("B-c1", "AAATMN", "AAA", "BUY", "MARKET", qty, int(time.time()), "ENTRY", "c1")
    r.order = o
    return br.submit(o)


# ---------------------------------------------------------------------- client safety
@pytest.mark.parametrize("method,path", [
    ("POST", "/v1/account/otc/orders"), ("POST", "/v1/account/money-withdrawal"), ("POST", "/sub-accounts/transfers"),
    ("GET", "/v1/account/card-numbers"), ("POST", "/v1/account/ibans"), ("POST", "/v1/margin/positions"),
    ("DELETE", "/v1/account/orders"), ("PUT", "/v1/account/orders"), ("GET", "/v1/account/orders/../withdraw"),
])
def test_order_client_refuses_everything_else(method, path):
    with pytest.raises(wo.ForbiddenOrderRoute):
        wo.check_route(method, path)


def test_order_client_allows_only_spot_orders_and_reads():
    for m, p in [("POST", "/v1/account/orders"), ("DELETE", "/v1/account/orders/B_c1"),
                 ("GET", "/v1/account/orders/B_c1"), ("GET", "/v1/account/openOrders"), ("GET", "/v1/account/balances")]:
        wo.check_route(m, p)


def test_client_ids_are_wallex_safe_and_deterministic():
    assert wo.safe_client_id("B-abc-123") == "B_abc_123" == wo.safe_client_id("B-abc-123")


def test_live_needs_user_approval(env):
    with pytest.raises(wo.LiveNotApproved):
        wo.WallexOrders(dry_run=False)
    env["WALLEX_LIVE_APPROVAL"] = "yes"
    with pytest.raises(wo.LiveNotApproved):
        wo.WallexOrders(dry_run=False)
    env["WALLEX_LIVE_APPROVAL"] = wo.APPROVAL_PHRASE
    wo.WallexOrders(dry_run=False).close()


def test_default_config_is_dry_run():
    assert CFG.live.dry_run is True


def test_dry_run_never_sends_orders():
    m = Mock()
    br, pf = make(m, dry=True)
    buy(br, pf)
    assert "AAA" in pf.positions                           # simulated against the live book
    assert not any(r[0] in ("POST", "DELETE") for r in m.requests)
    assert any(s["method"] == "POST" and s["dry_run"] for s in br.client.sent)


# ---------------------------------------------------------------------- broker behaviour
def test_buy_fill_uses_real_fee_in_base_coin_and_cap():
    m = Mock()
    br, pf = make(m)
    o = buy(br, pf, qty=0.3)                              # 300k TMN asked, capped at max_order_tmn
    post = [r for r in m.requests if r[0] == "POST"]
    assert len(post) == 1 and post[0][2]["type"] == "LIMIT" and post[0][2]["client_id"] == "B_c1"
    assert post[0][3]["sub-account-client-id"] == "s"
    q = float(post[0][2]["quantity"])
    assert q * float(post[0][2]["price"]) <= CFG.live.max_order_tmn + 1
    pos = pf.positions["AAA"]
    assert pos.qty == pytest.approx(q * 0.997)            # fee taken in AAA
    assert o.status == "FILLED" and "AAA" not in pf.reservations


def test_stop_exit_sells_held_quantity_once():
    m = Mock()
    br, pf = make(m)
    buy(br, pf)
    pos = pf.positions["AAA"]
    m.wallet_aaa = pos.qty
    br.books.update(books(ask=900_000, bid=899_000))      # below the stop
    br.check_stops()
    br.check_stops()                                      # second tick: no second exit order
    sells = [r for r in m.requests if r[0] == "POST" and r[2]["side"] == "SELL"]
    assert len(sells) == 1
    assert "AAA" not in pf.positions and pf.trades[-1]["exit_type"] == "STOP"


def test_sell_never_exceeds_free_balance():
    m = Mock()
    br, pf = make(m)
    buy(br, pf)
    m.wallet_aaa = pf.positions["AAA"].qty / 2
    br.books.update(books(ask=900_000, bid=899_000))
    br.check_stops()
    sell = [r for r in m.requests if r[0] == "POST" and r[2]["side"] == "SELL"][0]
    assert float(sell[2]["quantity"]) <= m.wallet_aaa + 1e-9


def test_unknown_order_is_never_resent_and_blocks_buys():
    m = Mock(timeout_on_place=1)
    br, pf = make(m)
    o = buy(br, pf)
    assert o.status == "UNKNOWN" and "UNKNOWN_ORDER" in br.halt_reasons
    assert "AAA" in pf.reservations                       # reservation held
    br.retry_pending()                                    # looked up by id, found filled
    assert len([r for r in m.requests if r[0] == "POST"]) == 1
    assert "AAA" in pf.positions and not br.halted


def test_unfilled_buy_is_cancelled_after_ttl_and_released():
    m = Mock(fill=False)
    br, pf = make(m, order_ttl_s=0)
    buy(br, pf)
    assert "AAA" not in pf.positions
    br.retry_pending()
    assert any(r[0] == "DELETE" for r in m.requests)
    assert "AAA" not in pf.positions and "AAA" not in pf.reservations


def test_halted_broker_makes_no_buy():
    m = Mock()
    br, pf = make(m)
    br.halt_reasons.add("RECONCILE_MISMATCH")
    o = buy(br, pf)
    assert o.status == "REJECTED" and not any(r[0] == "POST" for r in m.requests)


def test_parse_fills_handles_totals_without_fill_list():
    f = parse_fills({"executedQty": "2", "executedSum": "200", "fee": "1", "fills": []}, "AAA")
    assert f == {"qty": 2.0, "price": 100.0, "fee_tmn": 1.0, "fee_base": 0.0}


def test_judge_state_uses_configured_timeframes_and_risk_cap():
    from types import SimpleNamespace

    from wallex_agent.judge.laya import build_state

    c = SimpleNamespace(candidate_id="c1", setup="T1_pullback", symbol="AAATMN", symbol_regime="UPTREND",
                        btc_regime="RANGE", gray_zone=False, regime_inputs={"adx": 25.0}, trigger_inputs={"rsi": 45.0},
                        gate={}, order_flow={})
    ctx = {"portfolio": {"open_risk_pct": 0.5, "max_open_risk_pct": CFG.risk.max_open_risk_pct},
           "timeframes": {"regime": "1D", "trigger": "4h"}}
    text = "\n".join(s[1] for s in build_state(c, ctx))
    assert "Symbol regime 1D" in text and "4h RSI" in text and "1D ADX" in text
    assert f"of {CFG.risk.max_open_risk_pct}% cap" in text and "1h" not in text
