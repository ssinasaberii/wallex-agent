"""Dashboard: safe config edits (whitelist, ranges, confirmation, backup, audit) and the server's access checks."""

import json
import shutil
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest
import yaml

from wallex_agent.dashboard import fields as F
from wallex_agent.dashboard import service as S
from wallex_agent.dashboard.server import make_handler
from wallex_agent.dashboard.yaml_edit import get_path, set_values


@pytest.fixture
def cfg_copy(tmp_path, monkeypatch):
    c = tmp_path / "default.yaml"
    shutil.copy(S.CONFIG, c)
    monkeypatch.setattr(S, "CONFIG", c)
    monkeypatch.setattr(S, "BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(S, "AUDIT", tmp_path / "audit.jsonl")
    return c


def test_every_field_exists_in_config():
    raw = yaml.safe_load(S.CONFIG.read_text(encoding="utf-8"))
    for f in F.FIELDS:
        get_path(raw, f.path)


def test_yaml_edit_keeps_comments_and_other_lines():
    text = S.CONFIG.read_text(encoding="utf-8")
    assert set_values(text, {}) == text
    new = set_values(text, {"risk.risk_per_trade_pct": 0.3, "setups.T3_meanrev.enabled": False})
    changed = [(a, b) for a, b in zip(text.splitlines(), new.splitlines(), strict=True) if a != b]
    assert len(changed) == 2 and all("#" in b for _, b in changed)
    raw = yaml.safe_load(new)
    assert raw["risk"]["risk_per_trade_pct"] == 0.3 and raw["setups"]["T3_meanrev"]["enabled"] is False
    with pytest.raises(KeyError):
        set_values(text, {"risk.nope": 1})


def test_unknown_or_out_of_range_values_are_refused(cfg_copy):
    before = cfg_copy.read_text(encoding="utf-8")
    r = S.apply_changes({"fees.taker_pct": 0.01, "risk.risk_per_trade_pct": 9})
    assert len(r["errors"]) == 2 and not r["ok"]
    assert cfg_copy.read_text(encoding="utf-8") == before


def test_risky_change_needs_confirmation_then_saves_with_backup_and_audit(cfg_copy):
    r = S.apply_changes({"risk.risk_per_trade_pct": 0.75})
    assert r["needs_confirm"] and not r["ok"]
    assert yaml.safe_load(cfg_copy.read_text(encoding="utf-8"))["risk"]["risk_per_trade_pct"] == 0.5
    r = S.apply_changes({"risk.risk_per_trade_pct": 0.75}, confirm=True)
    assert r["ok"] and yaml.safe_load(cfg_copy.read_text(encoding="utf-8"))["risk"]["risk_per_trade_pct"] == 0.75
    assert len(list(S.BACKUPS.glob("default_*.yaml"))) == 1
    a = json.loads(S.AUDIT.read_text(encoding="utf-8").splitlines()[-1])
    assert a["changes"]["risk.risk_per_trade_pct"] == [0.5, 0.75]


def test_safe_change_saves_without_confirmation(cfg_copy):
    r = S.apply_changes({"risk.risk_per_trade_pct": 0.3})
    assert r["ok"] and not r["warnings"]


def test_cross_checks(cfg_copy):
    r = S.apply_changes({"live.max_order_tmn": 300000, "live.max_notional_tmn": 200000}, confirm=True)
    assert any("سقف هر خرید" in e for e in r["errors"])
    r = S.apply_changes({f"setups.{s}.enabled": False for s in ("T1_pullback", "T2_breakout", "T3_meanrev")}, confirm=True)  # noqa: E501
    assert any("الگوی ورود" in e for e in r["errors"])


def test_dashboard_cannot_touch_env_or_fees():
    assert not any(f.path.startswith(("fees.", "challenge.", "data.regime", "data.trigger")) for f in F.FIELDS)
    src = (S.ROOT / "src" / "wallex_agent" / "dashboard").rglob("*.py")
    assert not any("WALLEX_LIVE_APPROVAL" in p.read_text(encoding="utf-8") or '".env"' in p.read_text(encoding="utf-8")
                   for p in src)


def test_server_requires_token_and_localhost():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("tok", 0))
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler("tok", port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    client = httpx.Client(timeout=30, trust_env=False)  # no proxy; the machine may be busy with the bots
    try:
        assert client.get(base + "/").status_code == 200
        assert client.get(base + "/api/config").status_code == 403
        assert client.get(base + "/api/config", headers={"X-Token": "bad"}).status_code == 403
        assert client.get(base + "/api/config", headers={"X-Token": "tok", "Host": "evil.example"}).status_code == 403
        assert client.post(base + "/api/config", headers={"X-Token": "tok", "Origin": "http://evil.example"},
                          json={"changes": {}}).status_code == 403
        assert client.get(base + "/api/config", headers={"X-Token": "tok"}).status_code == 200
        r = client.post(base + "/api/control", headers={"X-Token": "tok"}, json={"action": "stop_live"})
        assert r.status_code == 400  # control needs an explicit confirm
    finally:
        client.close()
        httpd.shutdown()
