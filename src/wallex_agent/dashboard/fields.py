# ruff: noqa: E501  (a table of Persian labels; wrapping would hurt readability)
"""The only config values the dashboard may change, with Persian labels, ranges and what the backtests showed.

`risky(old, new)` returns a Persian warning when a change loosens a safety rule; the server then requires an explicit
confirmation. Nothing here can touch `.env` (the live-order approval stays with the user, ADR-033).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Field:
    path: str
    group: str
    label: str
    help: str
    kind: str                      # number | int | bool | select
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple[tuple[str, str], ...] = ()
    unit: str = ""
    tested: str = ""               # what the backtests showed (Persian)
    warn_above: float | None = None
    warn_below: float | None = None
    warn_if: Any = None            # bool / option value that needs confirmation
    meta: dict = field(default_factory=dict)


GROUPS = [
    ("capital", "سرمایه و ریسک", "چقدر از پول در هر معامله و در مجموع به خطر بیفتد."),
    ("live", "معامله واقعی", "سقف‌های حساب واقعی والکس. قفل نهایی معامله واقعی فقط در فایل .env و دست شماست."),
    ("entry", "استراتژی ورود", "کدام الگوهای خرید فعال باشند و چطور انتخاب شوند."),
    ("exit", "حد ضرر و فروش", "فاصله حد ضرر، حد ضرر متحرک، هدف سود و حداکثر نگه‌داری."),
    ("market", "بازار و خبر", "کدام ارزها بررسی شوند و خبرها چقدر جلوی خرید را بگیرند."),
]

FIELDS: list[Field] = [
    # ---------------------------------------------------------------- capital and risk
    Field("risk.risk_per_trade_pct", "capital", "ریسک هر معامله", "اگر حد ضرر بخورد، این درصد از کل پول از دست می‌رود؛ مبلغ خرید از روی همین حساب می‌شود.",
          "number", 0.1, 2.0, 0.05, unit="٪ سرمایه", warn_above=0.5,
          tested="۰٫۲۵ ← ۳٫۳٪+، ۰٫۵ ← ۷٫۰٪+ (افت ۶٪)، ۰٫۷۵ ← ۴٫۱٪+ (افت ۱۱٪)، ۱ ← ۲٫۳٪+ (افت ۱۳٪) در ۹٫۶ ماه"),
    Field("risk.max_open_risk_pct", "capital", "سقف ریسک همه خریدهای باز", "جمع ضرر احتمالی همه پوزیشن‌های باز با هم از این بیشتر نشود. معمولاً ۴ برابر ریسک هر معامله.",
          "number", 0.5, 8.0, 0.25, unit="٪ سرمایه", warn_above=2.0),
    Field("risk.max_position_pct", "capital", "سقف هر پوزیشن", "هیچ خریدی بزرگ‌تر از این درصد سرمایه نمی‌شود.",
          "number", 5, 100, 1, unit="٪ سرمایه", warn_above=15,
          tested="۲۵ و ۳۵ با ریسک ۰٫۵٪ تفاوتی نداشت؛ «همه پول» با توقف‌ها ۱۵٪- در یک دوره"),
    Field("risk.max_slots", "capital", "حداکثر ارز هم‌زمان", "چند ارز مختلف هم‌زمان می‌توانند خریده شده باشند.", "int", 1, 10, 1, unit="ارز"),
    Field("risk.min_buy_tmn", "capital", "حداقل مبلغ خرید", "خریدهای کوچک‌تر از این انجام نمی‌شوند (حداقل والکس ۵۰ هزار است).",
          "int", 50000, 2000000, 10000, unit="تومان"),
    Field("risk.max_daily_loss_pct", "capital", "حد زیان ۲۴ ساعته", "اگر در ۲۴ ساعت این‌قدر ضرر شد، خرید جدید متوقف می‌شود.",
          "number", 0.5, 10, 0.25, unit="٪", warn_above=1.5),
    Field("risk.kill_switch_drawdown_pct", "capital", "توقف اضطراری", "اگر حساب از اوج روز این‌قدر پایین بیاید، ربات خرید را تا شروع دوباره متوقف می‌کند.",
          "number", 1, 20, 0.5, unit="٪", warn_above=3.0),
    Field("risk.kill_switch_reset", "capital", "شروع دوباره توقف اضطراری", "بعد از فعال شدن توقف، کی دوباره اجازه خرید داده شود.",
          "select", options=(("daily", "هر روز"), ("weekly", "هر هفته"), ("monthly", "هر ماه"), ("never", "هرگز (تصمیم دستی)")),
          tested="هرگز: ۰٫۷۳٪- ؛ روزانه/هفتگی/ماهانه: ۳٫۳۱٪+ (حساب ۹٫۶ ماهه با ریسک ۰٫۲۵٪)"),
    # ---------------------------------------------------------------- live
    Field("live.max_order_tmn", "live", "سقف هر خرید واقعی", "هیچ سفارش خرید واقعی بزرگ‌تر از این نیست.",
          "int", 100000, 5000000, 10000, unit="تومان", warn_above=120000),
    Field("live.max_notional_tmn", "live", "سقف مجموع خریدهای باز واقعی", "جمع ارزش همه پوزیشن‌های باز واقعی از این بیشتر نشود.",
          "int", 100000, 5000000, 10000, unit="تومان", warn_above=250000),
    Field("live.limit_offset_pct", "live", "حاشیه قیمت سفارش", "خرید حداکثر این‌قدر بالاتر از بهترین قیمت فروش، فروش حداکثر این‌قدر پایین‌تر از بهترین قیمت خرید.",
          "number", 0.05, 1.5, 0.05, unit="٪", warn_above=0.5),
    Field("live.order_ttl_s", "live", "مهلت پر شدن خرید", "اگر سفارش خرید در این مدت پر نشد، لغو می‌شود.", "int", 15, 600, 5, unit="ثانیه"),
    Field("live.dry_run", "live", "حالت آزمایشی", "روشن = همه چیز اجرا می‌شود ولی هیچ سفارشی به والکس نمی‌رود. خاموش = معامله واقعی (به‌شرط قفل .env).",
          "bool", warn_if=False),
    # ---------------------------------------------------------------- entry
    Field("setups.T1_pullback.enabled", "entry", "الگوی پولبک در روند صعودی", "خرید وقتی ارز در روند صعودی روزانه کمی عقب نشسته.", "bool", warn_if=False,
          tested="مهم‌ترین الگو: ۵۳ از ۶۹ معامله؛ حذفش نتیجه اعتبارسنجی را منفی کرد"),
    Field("setups.T2_breakout.enabled", "entry", "الگوی شکست سقف", "خرید وقتی قیمت از سقف ۲۰ کندل ۴ ساعته بالاتر رفته.", "bool"),
    Field("setups.T3_meanrev.enabled", "entry", "الگوی برگشت از کف کانال", "خرید وقتی قیمت به کف کانال نوسان رسیده.", "bool"),
    Field("setups.T1_pullback.target_r", "entry", "هدف سود الگوی پولبک", "هدف سود چند برابر فاصله حد ضرر باشد.", "number", 1.0, 6.0, 0.5, unit="برابر",
          tested="۳ برابر بهترین بود (v1.4)"),
    Field("setups.gray_zone.enabled", "entry", "خرید در روند مبهم", "اجازه بررسی کاندیداهای «منطقه خاکستری» با نصف حجم (تصمیم با داور هوش مصنوعی).", "bool"),
    Field("ranking.max_candidates_per_cycle", "entry", "حداکثر کاندیدا در هر تصمیم", "فقط این تعداد از بهترین فرصت‌های هر تصمیم بررسی می‌شوند.", "int", 1, 10, 1,
          tested="۳ و ۵ تفاوتی نداشت"),
    Field("btc_filter.enabled", "entry", "فیلتر بیت‌کوین", "وقتی بیت‌کوین پرنوسان است، حجم خریدها نصف شود.", "bool", warn_if=False),
    Field("data.signal_price", "entry", "قیمت تحلیل", "تحلیل روی قیمت دلاری (بدون نوسان تتر) یا تومانی.", "select",
          options=(("usdt_adjusted", "دلاری‌شده (فعلی)"), ("tmn", "تومانی")), tested="تومانی نتیجه را منفی کرد (۰٫۲۷٪- در برابر ۰٫۳۵٪+)"),
    # ---------------------------------------------------------------- exits
    Field("stops.k_atr", "exit", "فاصله حد ضرر", "حد ضرر چند برابر نوسان میانگین کندل ۴ ساعته زیر قیمت خرید باشد.", "number", 1.0, 6.0, 0.25, unit="برابر نوسان",
          tested="۳ برابر در همه سبک‌ها بهتر از ۲ برابر بود"),
    Field("stops.min_stop_pct", "exit", "کمترین فاصله حد ضرر", "حد ضرر هیچ‌وقت نزدیک‌تر از این نیست.", "number", 0.5, 5.0, 0.25, unit="٪"),
    Field("stops.max_stop_pct", "exit", "بیشترین فاصله حد ضرر", "اگر حد ضرر لازم دورتر از این باشد، خرید انجام نمی‌شود.", "number", 2.0, 20.0, 0.5, unit="٪"),
    Field("exits.trail_atr_mult", "exit", "فاصله حد ضرر متحرک", "بعد از سود، حد ضرر این‌قدر (چند برابر نوسان) پشت قیمت بالا می‌آید.", "number", 1.0, 8.0, 0.5,
          unit="برابر نوسان", tested="۴ برابر بهترین بود (v1.4)"),
    Field("exits.time_exit_t12_max_bars", "exit", "حداکثر نگه‌داری (پولبک و شکست)", "بعد از این مدت، پوزیشن بسته می‌شود.", "int", 12, 504, 12, unit="ساعت"),
    Field("exits.time_exit_t3_bars", "exit", "حداکثر نگه‌داری (برگشت از کف)", "بعد از این مدت، پوزیشن بسته می‌شود.", "int", 12, 504, 12, unit="ساعت"),
    # ---------------------------------------------------------------- market and news
    Field("universe.max_spread_pct", "market", "حداکثر فاصله قیمت خرید و فروش", "ارزهایی با فاصله بیشتر اصلاً بررسی نمی‌شوند (هزینه پنهان هر معامله).",
          "number", 0.1, 3.0, 0.05, unit="٪", warn_above=0.3, tested="۰٫۳ ← ۵٫۴٪+، ۰٫۵ ← ۳٫۶٪+، ۱ ← ۳٫۶٪+، بدون فیلتر ← ۴٫۲٪+ (با هزینه اسپرد)"),
    Field("universe.top_n_by_volume", "market", "تعداد ارزهای پرحجم", "فقط این تعداد از پرمعامله‌ترین ارزها بررسی می‌شوند.", "int", 5, 45, 1, tested="۲۵ و ۴۰ تفاوتی نداشت"),
    Field("news.max_age_hours", "market", "اثر خبر بد یک ارز", "خبر منفی جدی تا این مدت جلوی خرید همان ارز را می‌گیرد.", "number", 1, 48, 1, unit="ساعت", warn_below=6),
    Field("news.block_all_hours", "market", "توقف خرید بعد از رویداد بزرگ", "بعد از خبر بد برای کل بازار، این مدت هیچ خریدی نمی‌شود.", "number", 1, 48, 1, unit="ساعت", warn_below=6),
    Field("news.max_feed_silence_min", "market", "سکوت مجاز خبرها", "اگر این مدت هیچ سایت خبری خوانده نشد، خرید جدید متوقف می‌شود.", "int", 15, 240, 5, unit="دقیقه", warn_above=45),
]
BY_PATH = {f.path: f for f in FIELDS}


def coerce(f: Field, v: Any) -> Any:
    if f.kind == "bool":
        if isinstance(v, bool):
            return v
        if str(v).lower() in ("true", "1", "on"):
            return True
        if str(v).lower() in ("false", "0", "off"):
            return False
        raise ValueError("بله/خیر لازم است")
    if f.kind == "select":
        if v not in [o[0] for o in f.options]:
            raise ValueError("گزینه نامعتبر")
        return v
    x = float(v)
    if f.min is not None and x < f.min or f.max is not None and x > f.max:
        raise ValueError(f"باید بین {f.min:g} و {f.max:g} باشد")
    return int(round(x)) if f.kind == "int" else round(x, 6)


def risky(f: Field, old: Any, new: Any) -> str | None:
    if f.warn_if is not None and new == f.warn_if and old != new:
        return f"«{f.label}» تغییر حساسی است"
    if f.warn_above is not None and isinstance(new, (int, float)) and new > f.warn_above and new > old:
        return f"«{f.label}» از مقدار امن {f.warn_above:g} بیشتر می‌شود"
    if f.warn_below is not None and isinstance(new, (int, float)) and new < f.warn_below and new < old:
        return f"«{f.label}» از مقدار امن {f.warn_below:g} کمتر می‌شود"
    return None


def cross_checks(v: dict[str, Any]) -> list[str]:
    errs = []
    if v["risk.max_open_risk_pct"] < v["risk.risk_per_trade_pct"]:
        errs.append("سقف ریسک همه خریدها نباید از ریسک هر معامله کمتر باشد")
    if v["stops.min_stop_pct"] >= v["stops.max_stop_pct"]:
        errs.append("کمترین فاصله حد ضرر باید از بیشترین کمتر باشد")
    if v["live.max_order_tmn"] > v["live.max_notional_tmn"]:
        errs.append("سقف هر خرید واقعی نباید از سقف مجموع بیشتر باشد")
    if v["live.max_order_tmn"] < v["risk.min_buy_tmn"]:
        errs.append("سقف هر خرید واقعی نباید از حداقل مبلغ خرید کمتر باشد")
    if not any(v[f"setups.{s}.enabled"] for s in ("T1_pullback", "T2_breakout", "T3_meanrev")):
        errs.append("دست‌کم یک الگوی ورود باید روشن بماند")
    return errs
