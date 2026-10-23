"""Dashboard logic: read status files, change allowed config values safely, start/stop bots, quick backtests.

Everything is local files and local processes; the only network access is the read-only wallet balance that
scripts/live_status.py already does. `.env` is never read or written here.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from wallex_agent.dashboard import fields as F
from wallex_agent.dashboard.yaml_edit import get_path, set_values

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config" / "default.yaml"
BACKUPS = ROOT / "config" / "backups"
AUDIT = ROOT / "reports" / "config_changes.jsonl"
SHADOW = ROOT / "reports" / "shadow"
BT_DIR = ROOT / "reports" / "dashboard_bt"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
_lock = threading.Lock()

REASONS = {
    "RANK_CUTOFF": "رتبه کافی نداشت", "NEWS_BLOCK": "خبر بد جلوی خرید را گرفت", "JUDGE:REJECT": "داور رد کرد",
    "JUDGE:DEFER": "داور گفت صبر کن", "RISK:OPEN_RISK_CAP": "سقف ریسک کل پر بود", "RISK:NO_FREE_SLOT": "جای خالی نبود",
    "RISK:INSUFFICIENT_CASH": "پول نقد کافی نبود", "RISK:ASSET_ALREADY_HELD": "این ارز از قبل خریده شده",
    "RISK:KILL_SWITCH": "توقف اضطراری فعال بود", "RISK:BELOW_MIN_BUY": "مبلغ خرید کمتر از حداقل",
    "ORDER_FLOW:REJECT": "فروشنده‌ها در دفتر سفارش غالب بودند", "ORDER_FLOW:INSUFFICIENT": "داده دفتر سفارش کافی نبود",
}
SETUPS = {"T1_pullback": "پولبک در روند صعودی", "T2_breakout": "شکست سقف", "T3_meanrev": "برگشت از کف کانال"}
REGIMES = {"UPTREND": "صعودی", "DOWNTREND": "نزولی", "RANGE": "بی‌روند", "GRAY": "مبهم", "HIGH_VOLATILITY": "پرنوسان",
           "UNKNOWN": "نامشخص"}
VARIANTS = {"A": "فقط قواعد", "B": "قواعد + هوش مصنوعی", "C": "قواعد + هوش مصنوعی + خبر", "LC": "حساب واقعی"}


# ---------------------------------------------------------------------- config
def config_values() -> dict[str, Any]:
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return {f.path: get_path(raw, f.path) for f in F.FIELDS}


def config_view() -> dict:
    vals = config_values()
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    info = {"version": raw.get("strategy_version"), "end_utc": str(raw["challenge"]["end_utc"]),
            "regime_hours": raw["data"]["regime_hours"], "trigger_hours": raw["data"]["trigger_hours"],
            "taker_pct": raw["fees"]["taker_pct"], "maker_pct": raw["fees"]["maker_pct"]}
    groups = [{"id": g, "title": t, "help": h, "fields": [
        {"path": f.path, "label": f.label, "help": f.help, "kind": f.kind, "min": f.min, "max": f.max, "step": f.step,
         "options": [list(o) for o in f.options], "unit": f.unit, "tested": f.tested, "value": vals[f.path]}
        for f in F.FIELDS if f.group == g]} for g, t, h in F.GROUPS]
    return {"groups": groups, "info": info}


def apply_changes(changes: dict[str, Any], confirm: bool = False, dry: bool = False) -> dict:
    """Validate and (unless dry) write. Returns {ok, errors, warnings, needs_confirm, changed}."""
    errors, warnings, clean = [], [], {}
    cur = config_values()
    for path, v in changes.items():
        f = F.BY_PATH.get(path)
        if f is None:
            errors.append(f"{path}: این مقدار از داشبورد قابل تغییر نیست")
            continue
        try:
            nv = F.coerce(f, v)
        except (TypeError, ValueError) as e:
            errors.append(f"{f.label}: {e}")
            continue
        if nv != cur[path]:
            clean[path] = nv
            w = F.risky(f, cur[path], nv)
            if w:
                warnings.append(w)
    merged = {**cur, **clean}
    errors += F.cross_checks(merged)
    out = {"ok": False, "errors": errors, "warnings": warnings, "changed": {p: [cur[p], v] for p, v in clean.items()},
           "needs_confirm": bool(warnings) and not confirm}
    if errors or not clean or out["needs_confirm"] or dry:
        out["ok"] = not errors and bool(clean) and not out["needs_confirm"]
        return out
    with _lock:
        text = CONFIG.read_text(encoding="utf-8")
        new = set_values(text, clean)
        raw = yaml.safe_load(new)
        for p, v in clean.items():
            if get_path(raw, p) != v:
                raise RuntimeError(f"verification failed for {p}")
        from wallex_agent.config import Config

        Config.model_validate(raw)  # the whole file must still be a valid config
        BACKUPS.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        (BACKUPS / f"default_{stamp}.yaml").write_text(text, encoding="utf-8")
        tmp = CONFIG.with_suffix(".yaml.tmp")
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(CONFIG)
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": stamp, "by": "dashboard", "changes": out["changed"], "warnings": warnings,
                                 "backup": f"config/backups/default_{stamp}.yaml"}, ensure_ascii=False) + "\n")
    out["ok"] = True
    out["backup"] = f"config/backups/default_{stamp}.yaml"
    return out


def history(n: int = 20) -> list[dict]:
    if not AUDIT.exists():
        return []
    rows = [json.loads(x) for x in AUDIT.read_text(encoding="utf-8").splitlines() if x.strip()]
    return rows[-n:][::-1]


# ---------------------------------------------------------------------- status
def _jsonl(p: Path, last: int | None = None) -> list[dict]:
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    out = []
    for x in lines[-last:] if last else lines:
        try:
            out.append(json.loads(x))
        except ValueError:
            continue
    return out


def _equity_csv(p: Path) -> list[list[float]]:
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            t, e = line.split(",")[:2]
            rows.append([int(float(t)), float(e)])
        except ValueError:
            continue
    return rows


def _decisions(d: Path, n: int = 40) -> dict:
    recs = _jsonl(d / "decisions.jsonl")
    cycles = [{"t": r["ts_utc"], "btc": REGIMES.get(r.get("btc_regime"), r.get("btc_regime")), "universe": r.get("universe"),  # noqa: E501
               "candidates": r.get("candidates"), "buys": r.get("buys"), "sells": r.get("sells")}
              for r in recs if r.get("kind") == "cycle"][-n:][::-1]
    cands = []
    for r in [x for x in recs if x.get("kind") == "candidate"][-n:][::-1]:
        j, g, ti = r.get("judge") or {}, r.get("gate") or {}, r.get("trigger_inputs") or {}
        reason = r.get("reason") or ""
        cands.append({"t": r["ts_utc"], "symbol": r.get("symbol"), "setup": SETUPS.get(r.get("setup"), r.get("setup")),
                      "regime": REGIMES.get(r.get("symbol_regime"), r.get("symbol_regime")), "decision": r.get("final_decision"),  # noqa: E501
                      "reason": REASONS.get(reason, reason), "judge": j.get("decision"), "judge_conf": j.get("confidence"),  # noqa: E501
                      "price": ti.get("close_tmn"), "stop_pct": g.get("stop_dist_pct"), "target_pct": g.get("target_dist_pct"),  # noqa: E501
                      "news": [x.get("title") for x in ((r.get("news") or {}).get("context_items") or [])][:3]})
    exits = [{"t": r["ts_utc"], "symbol": r.get("symbol"), "reason": r.get("reason"), "pnl": r.get("pnl_tmn_net")}
             for r in recs if r.get("kind") == "exit"][-n:][::-1]
    return {"cycles": cycles, "candidates": cands, "exits": exits}


def _csv_trades(p: Path) -> list[dict]:
    if not p.exists() or p.stat().st_size < 5:
        return []
    import pandas as pd

    try:
        return pd.read_csv(p).tail(50).iloc[::-1].to_dict("records")
    except Exception:  # noqa: BLE001
        return []


def status(live_session: str = "live-1", shadow_session: str = "challenge-v15") -> dict:
    now = time.time()
    live_dir, sh_dir = SHADOW / live_session, SHADOW / shadow_session
    ls = None
    f = live_dir / "live_status_LC.json"
    if f.exists():
        ls = json.loads(f.read_text(encoding="utf-8"))
        ls["age_s"] = round(now - ls["t"])
    events = _jsonl(live_dir / "live_events_LC.jsonl", 60)[::-1]
    wf = live_dir / "wallet.json"
    wallet = json.loads(wf.read_text(encoding="utf-8")) if wf.exists() else None
    shadow = {}
    for v in ("A", "B", "C"):
        eq = _equity_csv(sh_dir / f"equity_{v}.csv")
        shadow[v] = {"name": VARIANTS[v], "equity": eq[-1][1] if eq else None, "trades": _csv_trades(sh_dir / f"trades_{v}.csv")}  # noqa: E501
    sm = sh_dir / "summary.md"
    news = []
    for x in _jsonl(sh_dir / "news.jsonl")[-40:][::-1]:
        i = x.get("interp") or {}
        news.append({"t": x.get("published_at"), "source": x.get("source"), "title": x.get("title"), "assets": x.get("assets"),  # noqa: E501
                     "direction": i.get("direction"), "severity": i.get("severity"), "systemic": i.get("systemic")})
    nxt = (int(now) // 14400 + 1) * 14400 + 20
    return {"now": int(now), "next_decision": nxt, "live": ls, "live_events": events, "wallet": wallet,
            "live_equity": _equity_csv(live_dir / "equity_LC.csv"), "live_decisions": _decisions(live_dir / "LC"),
            "shadow": shadow, "shadow_decisions": _decisions(sh_dir / "C"),
            "shadow_age_s": round(now - sm.stat().st_mtime) if sm.exists() else None, "news": news,
            "running": {"live": bool(ls) and ls["age_s"] < 120,
                        "shadow": sm.exists() and now - sm.stat().st_mtime < 75 * 60},
            "variants": VARIANTS}


# ---------------------------------------------------------------------- control
def control(action: str) -> dict:
    """Start/stop the bots with the project's own scripts. Starting live still needs the user's .env approval."""
    ps = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    if action == "stop_live":
        r = subprocess.run(ps + [str(ROOT / "scripts" / "start_live.ps1"), "-Stop"], capture_output=True, text=True,
                           cwd=ROOT, timeout=60, creationflags=flags)
        return {"ok": r.returncode == 0, "message": (r.stdout or r.stderr).strip()[-300:]}
    if action in ("start_live", "restart_live"):
        if action == "restart_live":
            subprocess.run(ps + [str(ROOT / "scripts" / "start_live.ps1"), "-Stop"], capture_output=True, cwd=ROOT,
                           timeout=60, creationflags=flags)
        subprocess.Popen(ps + [str(ROOT / "scripts" / "start_live.ps1")], cwd=ROOT, creationflags=flags)
        return {"ok": True, "message": "در حال راه‌اندازی؛ اگر قفل .env باز نباشد، ربات واقعی روشن نمی‌شود"}
    if action == "restart_shadow":
        stop = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'challenge-v15' -and "
                "$_.CommandLine -match 'run_shadow_forever|wallex_agent shadow' } | ForEach-Object { "
                "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", stop], cwd=ROOT, timeout=60, creationflags=flags)
        subprocess.Popen(ps + [str(ROOT / "scripts" / "run_shadow_forever.ps1"), "-Session", "challenge-v15"], cwd=ROOT,
                         creationflags=flags)
        return {"ok": True, "message": "تست سایه با تنظیمات جدید دوباره راه افتاد"}
    return {"ok": False, "message": "دستور نامعتبر"}


# ---------------------------------------------------------------------- quick backtest
_jobs: dict[str, dict] = {}


def start_backtest(changes: dict[str, Any]) -> dict:
    chk = apply_changes(changes, confirm=True, dry=True)
    if chk["errors"]:
        return {"ok": False, "errors": chk["errors"]}
    overrides = {p: v[1] for p, v in chk["changed"].items()}
    jid = uuid.uuid4().hex[:10]
    BT_DIR.mkdir(parents=True, exist_ok=True)
    out = BT_DIR / f"{jid}.json"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    p = subprocess.Popen([str(PY), "-I", str(ROOT / "scripts" / "quick_backtest.py"), "--overrides",
                          json.dumps(overrides), "--out", str(out)], cwd=ROOT, creationflags=flags)
    _jobs[jid] = {"proc": p, "out": out, "started": time.time(), "overrides": overrides}
    return {"ok": True, "id": jid}


def backtest_result(jid: str) -> dict:
    j = _jobs.get(jid)
    if j is None:
        return {"status": "unknown"}
    if j["out"].exists():
        return {"status": "done", **json.loads(j["out"].read_text(encoding="utf-8"))}
    if j["proc"].poll() is not None:
        return {"status": "failed"}
    return {"status": "running", "elapsed_s": round(time.time() - j["started"])}
