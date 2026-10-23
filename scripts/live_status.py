"""Live account monitor (user request 2026-10-23). Read-only: the bot's status file plus GET /v1/account/balances.

Writes reports/shadow/<session>/status.html (Persian, refreshes itself every 30 s) and prints a short summary.
  python -I scripts/live_status.py --session live-1           # once
  python -I scripts/live_status.py --session live-1 --watch   # keep updating every 30 s
Open the page in a browser: reports/shadow/live-1/status.html
"""

import argparse
import html
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
TEHRAN = timedelta(hours=3, minutes=30)
HALT = {"UNKNOWN_ORDER": "سفارش با وضعیت نامعلوم", "RECONCILE_MISMATCH": "اختلاف موجودی کیف پول با حساب ربات",
        "REPEATED_ERRORS": "چند خطای پشت‌سرهم", "OPEN_ORDERS_AT_START": "سفارش باز از اجرای قبلی"}
EXIT = {"STOP": "حد ضرر", "TRAIL_STOP": "حد ضرر متحرک", "TARGET": "هدف سود", "TIME": "پایان زمان",
        "FINAL_SETTLEMENT": "تسویه پایانی", "SETTLEMENT": "تسویه", "KILL_SWITCH": "توقف اضطراری"}


def n(x, d=0) -> str:
    if x is None:
        return "—"
    return f"{x:,.{d}f}".translate(str.maketrans("0123456789,.", "۰۱۲۳۴۵۶۷۸۹٬٫"))


def ts(t) -> str:
    s = (datetime.fromtimestamp(int(t), UTC) + TEHRAN).strftime("%m/%d %H:%M") if t else "—"
    return s.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def wallet() -> dict:
    try:
        from wallex_agent.data.wallex_orders import WallexOrders

        c = WallexOrders(dry_run=True)  # GET only here
        b = c.balances()
        c.close()
        return b
    except Exception as e:  # noqa: BLE001
        return {"_error": str(type(e).__name__)}


def build(session: str) -> tuple[str, str]:
    d = ROOT / "reports" / "shadow" / session
    f = d / "live_status_LC.json"
    if not f.exists():
        return "<p>هنوز فایل وضعیت ساخته نشده؛ ربات زنده اجرا نشده است.</p>", "no status file yet"
    s = json.loads(f.read_text(encoding="utf-8"))
    age = time.time() - s["t"]
    w = wallet()
    ev = []
    ef = d / "live_events_LC.jsonl"
    if ef.exists():
        ev = [json.loads(x) for x in ef.read_text(encoding="utf-8").splitlines()[-15:] if x.strip()]
    ret = 100 * (s["equity_tmn"] / s["initial_tmn"] - 1) if s["initial_tmn"] else 0
    mode = "آزمایشی (بدون سفارش واقعی)" if s["dry_run"] else "<b style='color:#b3501f'>معامله واقعی</b>"
    alerts = []
    if age > 120:
        alerts.append(f"ربات {n(age / 60)} دقیقه است وضعیت ننوشته؛ شاید متوقف شده باشد.")
    for h in s["halt_reasons"]:
        alerts.append(f"خرید جدید متوقف است: {HALT.get(h, h)}")
    if "_error" in w:
        alerts.append(f"خواندن موجودی والکس ناموفق ({w['_error']})")
    held = {p["symbol"][:-3] for p in s["positions"]}
    extra = [k for k in w if k not in ("TMN", "_error") and k not in held and w[k]["value"] > 0]
    rows_pos = "".join(
        f"<tr><td>{p['symbol']}</td><td>{ts(p['entry_t'])}</td><td>{n(p['entry'])}</td><td>{n(p['bid'])}</td>"
        f"<td>{n(p['stop'])}</td><td>{n(p['target']) if p['target'] else 'متحرک'}</td>"
        f"<td style='color:{'#1f6f66' if p['pnl_tmn'] >= 0 else '#b3501f'}'>{n(p['pnl_tmn'])}</td></tr>"
        for p in s["positions"]) or "<tr><td colspan=7>پوزیشن بازی نیست</td></tr>"
    rows_tr = "".join(
        f"<tr><td>{t['symbol']}</td><td>{ts(t['entry_t'])}</td><td>{ts(t['exit_t'])}</td><td>{n(t['entry_price'])}</td>"
        f"<td>{n(t['exit_price'])}</td><td>{EXIT.get(t['exit_type'], t['exit_type'])}</td>"
        f"<td style='color:{'#1f6f66' if t['pnl_tmn'] >= 0 else '#b3501f'}'>{n(t['pnl_tmn'])}</td></tr>"
        for t in reversed(s["trades"])) or "<tr><td colspan=7>هنوز معامله‌ای بسته نشده</td></tr>"
    rows_ev = "".join(f"<tr><td>{ts(e['t'])}</td><td>{html.escape(e['kind'])}</td>"
                      f"<td style='direction:ltr;text-align:left'>{html.escape(json.dumps({k: v for k, v in e.items() if k not in ('t', 'kind')}, ensure_ascii=False))[:160]}</td></tr>"
                      for e in reversed(ev)) or "<tr><td colspan=3>—</td></tr>"
    lc = s.get("last_cycle") or {}
    cands = "".join(f"<li>{ts(c['ts_utc'])} {c['symbol']} — {html.escape(str(c['final_decision']))} "
                    f"({html.escape(str(c['reason']))})</li>" for c in reversed(s.get("last_candidates") or [])) or "<li>—</li>"
    body = f"""
<h1>حساب زنده ربات — {html.escape(session)}</h1>
<p>حالت: {mode} · به‌روزرسانی: {ts(s['t'])} تهران · پایان مهلت: {ts(s['end_t'])}</p>
{''.join(f"<p class=alert>⚠ {html.escape(a)}</p>" for a in alerts)}
<div class=cards>
<div><span>ارزش حساب (ربات)</span><b>{n(s['equity_tmn'])}</b><i>تومان، {n(ret, 2)}٪</i></div>
<div><span>تومان در کیف پول والکس</span><b>{n((w.get('TMN') or {}).get('value'))}</b><i>آزاد {n((w.get('TMN') or {}).get('free'))}</i></div>
<div><span>پوزیشن باز / سفارش باز</span><b>{n(len(s['positions']))} / {n(len(s['open_orders']))}</b><i>کارمزد تا الان {n(s['fees_tmn'])}</i></div>
<div><span>آخرین تصمیم</span><b>{ts(lc.get('ts_utc'))}</b><i>{n(lc.get('universe'))} ارز بررسی، {n(lc.get('candidates'))} کاندیدا، {n(lc.get('buys'))} خرید</i></div>
</div>
<h2>پوزیشن‌های باز</h2>
<table><tr><th>ارز</th><th>زمان خرید</th><th>قیمت خرید</th><th>قیمت فعلی</th><th>حد ضرر</th><th>هدف</th><th>سود/زیان (تومان)</th></tr>{rows_pos}</table>
<h2>معاملات بسته‌شده</h2>
<table><tr><th>ارز</th><th>خرید</th><th>فروش</th><th>قیمت خرید</th><th>قیمت فروش</th><th>دلیل فروش</th><th>سود/زیان</th></tr>{rows_tr}</table>
<h2>فرصت‌های اخیر</h2><ul>{cands}</ul>
<h2>رویدادهای سفارش</h2><table><tr><th>زمان</th><th>نوع</th><th>جزئیات</th></tr>{rows_ev}</table>
{"<p>ارزهای دیگر در کیف پول: " + html.escape(", ".join(f"{k} {w[k]['value']}" for k in extra)) + "</p>" if extra else ""}
"""
    text = (f"{ts(s['t'])} | {'DRY' if s['dry_run'] else 'LIVE'} | equity {s['equity_tmn']:,.0f} ({ret:+.2f}%) | "
            f"positions {len(s['positions'])} | open orders {len(s['open_orders'])} | halt {s['halt_reasons'] or '-'}"
            + (f" | ALERT: {'; '.join(alerts)}" if alerts else ""))
    return body, text


PAGE = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>حساب زنده ربات</title><style>
body{{font-family:Vazirmatn,Tahoma,sans-serif;background:#f6f4ef;color:#14213d;margin:0;padding:16px 24px;max-width:1200px}}
h1{{font-size:26px}} h2{{font-size:20px;margin-top:28px}} table{{border-collapse:collapse;width:100%;background:#fdfcf9}}
td,th{{border:1px solid #e2ded5;padding:6px 10px;font-size:15px;text-align:right}} th{{background:#e9e4d8}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
.cards div{{background:#14213d;color:#f6f4ef;border-radius:14px;padding:14px}} .cards span{{display:block;color:#b9c2d3;font-size:14px}}
.cards b{{display:block;font-size:26px;margin:6px 0}} .cards i{{font-style:normal;color:#b9c2d3;font-size:13px}}
.alert{{background:#fbe3d6;color:#8a3a14;padding:10px 14px;border-radius:10px}}
@media (prefers-color-scheme: dark){{body{{background:#10182b;color:#e8e6e0}} table{{background:#172240}} th{{background:#22304f}} td,th{{border-color:#2c3a5a}}}}
</style></head><body>{body}</body></html>"""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="live-1")
    ap.add_argument("--watch", action="store_true")
    a = ap.parse_args()
    out = ROOT / "reports" / "shadow" / a.session
    out.mkdir(parents=True, exist_ok=True)
    while True:
        body, text = build(a.session)
        (out / "status.html").write_text(PAGE.format(body=body), encoding="utf-8")
        print(text, flush=True)
        if not a.watch:
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
