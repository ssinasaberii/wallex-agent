"""Turn a backtest report into readable tables for the team's decision dashboard (Persian labels).

Reads reports/backtest/<run_id>/continuous/decisions.jsonl (one account over Validation+Test) and results.json,
writes reports/dashboard/{candidates,trades,funnel,summary}.json.
Usage: python -I scripts/export_dashboard.py <run_id>
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SETUP = {"T1_pullback": "پولبک در روند صعودی", "T2_breakout": "شکست سقف ۲۰ کندل", "T3_meanrev": "برگشت از کف کانال"}
REGIME = {"UPTREND": "صعودی", "DOWNTREND": "نزولی", "RANGE": "رنج", "GRAY": "مبهم", "HIGH_VOLATILITY": "پرنوسان",
          "UNKNOWN": "نامشخص"}
DECISION = {"BUY": "خرید", "NO_TRADE": "رد", "DEFER": "تعویق"}
REASON = {
    "FEE_GATE:STOP_TOO_WIDE": "حد ضرر لازم بیش از ۸٪ می‌شد (نوسان خیلی زیاد)",
    "FEE_GATE:NO_TARGET": "هدف (خط میانی بولینگر) بالاتر از قیمت نبود",
    "FEE_GATE:NO_ATR": "داده نوسان (ATR) کافی نبود",
    "FEE_GATE:RR_BELOW_MIN": "نسبت سود به ضرر بعد از کارمزد کم بود",
    "FEE_GATE:TARGET_BELOW_REQUIRED_MOVE": "هدف کمتر از هزینه کارمزد و لغزش بود",
    "FEE_GATE:NO_HORIZON": "رسیدن به هدف در زمان مجاز بعید بود",
    "FEE_GATE:COST_TO_MOVE": "هزینه نسبت به حرکت مورد انتظار زیاد بود",
    "RANK_CUTOFF": "جزو ۳ فرصت برتر همان ساعت نبود",
    "JUDGE:REJECT": "روند مبهم (منطقه خاکستری)؛ داور رد کرد",
    "JUDGE:DEFER": "داور منتظر داده تازه ماند",
    "RISK:BELOW_MIN_BUY": "حجم سفارش زیر حداقل ۱۰۰ هزار تومان می‌شد",
    "RISK:BELOW_MIN_QTY": "مقدار سفارش زیر حداقل صرافی می‌شد",
    "RISK:OPEN_RISK_CAP": "سقف ریسک کل (۱٪) پر بود",
    "RISK:NO_FREE_SLOT": "هر ۱۰ جایگاه پر بود",
    "RISK:INSUFFICIENT_CASH": "نقدینگی کافی نبود",
    "RISK:ASSET_ALREADY_HELD": "همین ارز از قبل در سبد بود",
    "RISK:KILL_SWITCH": "توقف اضطراری فعال بود",
    "ORDER_FLOW_CONFLICT": "دفتر سفارش فشار فروش نشان می‌داد",
    "ORDER_FLOW_INSUFFICIENT": "داده دفتر سفارش کافی نبود",
    "NEWS_BLOCK": "خبر منفی مهم",
    "FEE_SNAPSHOT_CHANGED": "کارمزد تغییر کرده بود",
    "CANDIDATE_EXPIRED": "فرصت منقضی شد",
}
EXIT = {"STOP": "حد ضرر", "TRAIL_STOP": "حد ضرر متحرک (حفظ سود)", "TARGET": "رسیدن به هدف",
        "TIME_EXIT": "پایان زمان نگهداری", "REGIME_EXIT": "تغییر روند", "KILL_SWITCH": "توقف اضطراری",
        "SETTLEMENT": "فروش پایان دوره", "FINAL_SETTLEMENT": "فروش پایان دوره"}
FUNNEL = [  # (funnel key prefix, label) in pipeline order
    ("halt_BTC", "توقف کل بازار: روند بیت‌کوین نزولی/نامشخص"),
    ("halt_KILL_SWITCH", "توقف اضطراری (افت ۳٪)"),
    ("halt_DAILY_LOSS", "توقف: زیان ۲۴ ساعته"),
    ("universe_quality", "ارز حذف شد: داده ناقص"),
    ("universe_spread", "ارز حذف شد: اسپرد بیش از ۰٫۳۰٪"),
    ("regime_warmup", "ارز حذف شد: تاریخچه کافی نیست"),
    ("skip_held", "ارز در سبد بود"),
    ("no_trigger", "بدون سیگنال خرید"),
    ("triggered", "سیگنال خرید دیده شد"),
    ("gate_", "رد در بررسی حد ضرر/هدف"),
    ("rank_cutoff", "رد در رتبه‌بندی"),
    ("judge_", "رد توسط داور"),
    ("risk_", "رد در کنترل ریسک"),
    ("buy", "خرید انجام شد"),
]


def iso(t):
    return datetime.fromtimestamp(int(t), UTC).strftime("%Y-%m-%d %H:%M")


def r(x, n=2):
    return None if x is None else round(float(x), n)


def main(run_id):
    rep = ROOT / "reports" / "backtest" / run_id
    res = json.loads((rep / "results.json").read_text(encoding="utf-8"))
    split = res["split_utc"]
    test_start = datetime.strptime(split["test"][0], "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp()
    recs = [json.loads(line) for line in (rep / "continuous" / "decisions.jsonl").open(encoding="utf-8")]

    def period(t):
        return "تست" if t >= test_start else "اعتبارسنجی"

    cands = []
    for x in recs:
        if x.get("kind") != "candidate":
            continue
        g, ti, ri = x.get("gate") or {}, x.get("trigger_inputs") or {}, x.get("regime_inputs") or {}
        reason = x["reason"]
        if x["final_decision"] == "BUY":
            why = "تأیید شد و خرید انجام شد"
        else:
            key = reason if reason in REASON else reason.split(":")[0] + ":" + reason.split(":")[-1]
            why = REASON.get(reason) or REASON.get(key) or reason
        cands.append({
            "candidate_id": x["candidate_id"], "time_utc": iso(x["ts_utc"]), "period": period(x["ts_utc"]),
            "coin": x["symbol"].replace("TMN", ""), "setup": SETUP.get(x["setup"], x["setup"]),
            "regime": REGIME.get(x["symbol_regime"], x["symbol_regime"]),
            "btc_regime": REGIME.get(x["btc_regime"], x["btc_regime"]),
            "gray_zone": "بله" if x["gray_zone"] else "خیر",
            "decision": DECISION.get(x["final_decision"], x["final_decision"]), "reason": why,
            "price_tmn": r(ti.get("close_tmn"), 2), "stop_pct": r(g.get("stop_dist_pct")),
            "target_pct": r(g.get("target_dist_pct")), "cost_pct": r(g.get("cost_rt_pct")),
            "rsi": r(ti.get("rsi"), 1), "relvol": r(ti.get("relvol")), "adx_daily": r(ri.get("adx"), 1),
            "rank_score": r(x.get("rank_score"), 3),
        })
    trades = []
    exits = [x for x in recs if x.get("kind") == "exit" and x.get("pnl_tmn_net") is not None]
    by_cid = {c["candidate_id"]: c for c in cands}
    tr_path = rep / "trades_continuous.csv"
    import csv

    with tr_path.open(encoding="utf-8") as fh:
        for k, t in enumerate(csv.DictReader(fh)):
            c = by_cid.get(t["candidate_id"], {})
            hold = (int(float(t["exit_t"])) - int(float(t["entry_t"]))) / 3600
            trades.append({
                "trade_id": f"T{k + 1:03d}", "coin": t["symbol"].replace("TMN", ""),
                "setup": SETUP.get(t["setup"], t["setup"]), "entry_utc": iso(float(t["entry_t"])),
                "exit_utc": iso(float(t["exit_t"])), "period": period(float(t["entry_t"])),
                "hold_hours": round(hold, 1), "entry_price": r(t["entry_price"], 4), "exit_price": r(t["exit_price"], 4),
                "pnl_tmn": round(float(t["pnl_tmn"])), "r_multiple": r(t["r_multiple"]),
                "exit_reason": EXIT.get(t["exit_type"], t["exit_type"]), "entry_regime": c.get("regime"),
            })
    _ = exits
    funnel_raw = {}
    for x in recs:
        if x.get("kind") == "cycle" and x.get("halt"):
            pass
    fun = json.loads(json.dumps(res.get("continuous_funnel", {}))) if "continuous_funnel" in res else None
    if fun is None:
        import csv as _csv

        with (rep / "funnel.csv").open(encoding="utf-8") as fh:
            fun = {row["stage"]: int(row["count"]) for row in _csv.DictReader(fh)}
    for prefix, label in FUNNEL:
        n = sum(v for k, v in fun.items() if (k == prefix or k.startswith(prefix)))
        if n:
            funnel_raw[label] = n
    # Market-wide halts are counted per decision cycle; every other stage per coin per cycle.
    funnel = [{"step": i + 1, "stage": k, "count": v,
               "unit": "چرخه تصمیم (هر ۴ ساعت)" if k.startswith("توقف") else "ارز در هر چرخه"}
              for i, (k, v) in enumerate(funnel_raw.items())]

    cont, test = res["continuous"], res["test"]
    bm = res["benchmarks_test_median_return_pct"]
    summary = [
        {"metric_id": "cont_return", "metric": "بازده اجرای پیوسته (اعتبارسنجی+تست)", "value": r(cont["return_pct"], 3),
         "unit": "%"},
        {"metric_id": "cont_trades", "metric": "تعداد معاملات اجرای پیوسته", "value": cont["trades"], "unit": ""},
        {"metric_id": "cont_win", "metric": "نرخ برد", "value": r(cont["win_rate_pct"], 1), "unit": "%"},
        {"metric_id": "cont_pf", "metric": "ضریب سود (Profit Factor)", "value": r(cont["profit_factor"], 2), "unit": ""},
        {"metric_id": "cont_dd", "metric": "بیشترین افت سرمایه", "value": r(cont["max_drawdown_pct"], 2), "unit": "%"},
        {"metric_id": "cont_fees", "metric": "کل کارمزد پرداختی", "value": round(cont["fees_tmn"]), "unit": "تومان"},
        {"metric_id": "test_mean", "metric": "میانگین بازده هر پنجره ۳۰ روزه در تست",
         "value": r(test.get("mean_return_pct"), 3), "unit": "%"},
        {"metric_id": "test_pos", "metric": "پنجره‌های سودده در تست", "value": r(test["positive_pct"], 0), "unit": "%"},
        {"metric_id": "bm_btc", "metric": "نگهداری بیت‌کوین (میانه هر پنجره تست)",
         "value": r(bm.get("buy_hold_BTCTMN"), 2), "unit": "%"},
        {"metric_id": "bm_usdt", "metric": "نگهداری تتر (میانه هر پنجره تست)",
         "value": r(bm.get("hold_USDT (toman devaluation)"), 2), "unit": "%"},
    ]
    out = ROOT / "reports" / "dashboard"
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("candidates", cands), ("trades", trades), ("funnel", funnel), ("summary", summary)):
        (out / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"candidates": len(cands), "trades": len(trades), "funnel": funnel, "split": split},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
