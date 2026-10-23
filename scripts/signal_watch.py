"""Buy-signal alerts from the shadow test (user request 2026-10-23): when any shadow variant (or the dry-run live
account) decides BUY, print a short Persian note: coin, price, setup and what it expects, stop, target, judge, news,
and how that setup did in the official backtest. Information only; nothing is sent to Wallex.

  python -I scripts/signal_watch.py            # print alerts not shown before; exit 0 if any, 1 if none
  python -I scripts/signal_watch.py --all      # print every BUY so far (does not mark them seen)
Seen candidate ids: reports/shadow/signal_watch_seen.json.
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = ["challenge-v15", "live-dry-1", "live-1"]
SEEN = ROOT / "reports" / "shadow" / "signal_watch_seen.json"
BACKTEST = ROOT / "reports" / "backtest" / "20261023T003238Z" / "trades_continuous.csv"
VARIANT = {"A": "فقط قواعد", "B": "قواعد + هوش مصنوعی", "C": "قواعد + هوش مصنوعی + خبر",
           "LC": "حساب زنده"}
SETUP = {
    "T1_pullback": ("پولبک در روند صعودی", "ارز در روند صعودی روزانه کمی عقب نشسته؛ انتظار: ادامه روند صعودی."),
    "T2_breakout": ("شکست سقف", "قیمت از سقف ۲۰ کندل ۴ ساعته بالاتر رفته؛ انتظار: ادامه حرکت رو به بالا."),
    "T3_meanrev": ("برگشت از کف کانال", "قیمت به کف کانال نوسان رسیده؛ انتظار: برگشت به وسط کانال."),
}
TEHRAN = timedelta(hours=3, minutes=30)
REGIME = {"UPTREND": "صعودی", "DOWNTREND": "نزولی", "RANGE": "رنج (بی‌روند)", "GRAY": "مبهم",
          "HIGH_VOLATILITY": "پرنوسان", "UNKNOWN": "نامشخص"}


def fa_num(x: float, d: int = 0) -> str:
    s = f"{x:,.{d}f}".replace(",", "٬").replace(".", "٫")
    return s.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def setup_stats() -> dict:
    if not BACKTEST.exists():
        return {}
    t = pd.read_csv(BACKTEST)
    t["hold_h"] = (t["exit_t"] - t["entry_t"]) / 3600
    return {s: {"n": len(g), "win": 100 * (g["pnl_tmn"] > 0).mean(), "avg_r": g["r_multiple"].mean(),
                "hold_h": g["hold_h"].median()} for s, g in t.groupby("setup")}


def buys():
    for s in SESSIONS:
        d = ROOT / "reports" / "shadow" / s
        for f in sorted(d.glob("*/decisions.jsonl")) if d.exists() else []:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") == "candidate" and r.get("final_decision") == "BUY":
                    yield s, f.parent.name, r


def note(session: str, variant: str, r: dict, stats: dict) -> str:
    ti, g, j = r.get("trigger_inputs") or {}, r.get("gate") or {}, r.get("judge") or {}
    px = float(ti.get("close_tmn") or 0)
    stop_pct, tgt_pct = float(g.get("stop_dist_pct") or 0), g.get("target_dist_pct")
    name, expect = SETUP.get(r.get("setup"), (r.get("setup"), ""))
    when = datetime.fromtimestamp(int(r["ts_utc"]), UTC) + TEHRAN
    lines = [f"🔔 فرصت خرید: {r['symbol']} — {VARIANT.get(variant, variant)} ({session})",
             f"- زمان تصمیم: {when:%Y-%m-%d %H:%M} تهران",
             f"- قیمت لحظه تصمیم: {fa_num(px)} تومان",
             f"- الگو: {name}. {expect}",
             f"- حد ضرر: {fa_num(px * (1 - stop_pct / 100))} تومان ({fa_num(stop_pct, 1)}٪ پایین‌تر)"]
    if r.get("setup") == "T2_breakout" or tgt_pct is None:
        lines.append("- هدف: هدف ثابت ندارد؛ حد ضرر متحرک (۴ برابر نوسان میانگین) پشت قیمت بالا می‌آید")
    else:
        t = float(tgt_pct)
        lines.append(f"- هدف سود: {fa_num(px * (1 + t / 100))} تومان ({fa_num(t, 1)}٪ بالاتر)، بعد از آن حد ضرر متحرک")
    lines.append("- حداکثر نگه‌داری: ۷ روز")
    risk = r.get("risk") or {}
    if risk.get("notional_tmn"):
        lines.append(f"- مبلغ خرید در حساب ۵ میلیونی: {fa_num(float(risk['notional_tmn']))} تومان "
                     f"(ضرر اگر حد ضرر بخورد: حدود {fa_num(float(risk.get('risk_budget_tmn') or 0))} تومان)")
    ri = r.get("regime_inputs") or {}
    lines.append(f"- روند روزانه ارز: {REGIME.get(r.get('symbol_regime'), r.get('symbol_regime'))}، بیت‌کوین: "
                 f"{REGIME.get(r.get('btc_regime'), r.get('btc_regime'))}، قدرت روند (ADX): "
                 f"{fa_num(float(ri.get('adx') or 0), 0)}")
    if j.get("provider") == "laya":
        lines.append(f"- داور هوش مصنوعی: {j.get('decision')} با اطمینان {fa_num(100 * float(j.get('confidence') or 0))}٪ "
                     f"({', '.join(j.get('reason_codes') or [])})")
    news = (r.get("news") or {}).get("context_items") or []
    if news:
        lines.append("- خبرهای مرتبط: " + " | ".join(str(x.get("title", x))[:80] for x in news[:3]))
    st = stats.get(r.get("setup"))
    if st:
        lines.append(f"- این الگو در بک‌تست ۹٫۶ ماهه: {fa_num(st['n'])} معامله، {fa_num(st['win'])}٪ سودده، میانه نگه‌داری "
                     f"{fa_num(st['hold_h'])} ساعت")
    lines.append("- یادآوری: این فقط تحلیل ربات است، نه توصیه مالی؛ هیچ سفارشی ثبت نشده.")
    return "\n".join(lines)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    show_all = "--all" in sys.argv
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() and not show_all else set()
    stats, new = setup_stats(), []
    for s, v, r in buys():
        key = f"{s}/{v}/{r['candidate_id']}"
        if key not in seen:
            new.append((s, v, r))
            seen.add(key)
    for s, v, r in new:
        print(note(s, v, r, stats), "\n")
    if not show_all:
        SEEN.write_text(json.dumps(sorted(seen)))
    return 0 if new else 1


if __name__ == "__main__":
    raise SystemExit(main())
