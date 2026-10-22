# PLAN.md — Wallex Spot Trading Agent (v1.2)

> وضعیت: **پیش‌نویس برای تأیید تیم.** هیچ کدی نوشته نشده است. طبق بخش ۰ بند ۲ سند `09`، پس از این فایل کار متوقف می‌شود.
> تاریخ نگارش: 2026-10-22 14:20 UTC

---

## وضعیت فازها (به‌روزرسانی 2026-10-22 ۲۱:۱۵ تهران)

| فاز | وضعیت | خروجی |
|---|---|---|
| P0 | ✅ | `docs/api-verification.md` |
| P1 | ✅ | لایه داده، اندیکاتورها، snapshot `0a4e977b89b6b576` |
| P2 | ✅ | چرخه تصمیم، ریسک، خروج، لاگ؛ تست‌های ۱، ۳ تا ۷، ۹، ۱۰ |
| P3 | ✅ در حال اجرا | Shadow با واریانت‌های A، B (Laya)، XA و XB تا پایان مسابقه؛ `reports/shadow/challenge/` |
| P4 | ✅ | `reports/backtest/20261022T172742Z/`؛ تست‌های ۲ و ۸ |
| P5 | بخشی | Laya و Order Flow (OBI) در Shadow فعال؛ خبر/RSS و واریانت BoxAPI به دلیل زمان حذف شد |
| P6 | ✅ | `README.md`، `ARCHITECTURE.md` |

نتیجه کلیدی: با کارمزد واقعی، استراتژی با عددهای Spec معامله نمی‌کند (Q-32 در `docs/OPEN_QUESTIONS.md`).

## ۰. خلاصه یک‌پاراگرافی

یک موتور تصمیم واحد (`engine/decision_cycle.py`) ساخته می‌شود که Backtest، Shadow و (در آینده) Live همه آن را صدا می‌زنند و فقط در منبع داده، Broker و Judge فرق دارند. ترتیب کار طبق `09` §۱۴ است: اعتبارسنجی API ← داده و اندیکاتور ← چرخه تصمیم با `RulesBaselineJudge` ← **Shadow حداقلی روشن و در حال اجرا** ← Backtest کامل ← Order Flow/خبر/Judge هوشمند ← مستندات داور. هیچ مسیر کدی برای ثبت سفارش واقعی وجود نخواهد داشت.

---

## ۱. بودجه زمانی (مهم‌ترین قید)

| مورد | مقدار |
|---|---|
| اکنون | 2026-10-22 14:20 UTC |
| پایان چالش (از KICKOFF) | 2026-10-24 17:30 UTC |
| زمان باقی‌مانده | **حدود ۵۱ ساعت** |
| شروع چالش (استنباطی، ۷۲ ساعت قبل از پایان) | حدود 2026-10-21 17:30 UTC — یعنی چالش **احتمالاً از دیروز شروع شده** (تأیید در OPEN_QUESTIONS، Q-01) |

پیامد: Shadow فقط حدود دو روز داده جمع می‌کند، پس باید زودتر از Backtest کامل روشن شود (همان منطق `09` §۱۴). برنامه زمانی پیشنهادی (ساعت کار Agent، بدون احتساب زمان بررسی تیم):

| فاز | هدف زمانی پیشنهادی (UTC) | خروجی |
|---|---|---|
| P0 | 22 Oct، تا ~17:00 | `docs/api-verification.md`، snapshot متادیتا و فهرست جفت‌های TMN |
| P1 | 22 Oct، تا ~22:00 | لایه داده + کش Parquet + اندیکاتورها + تست ۱ و ۳ |
| P2 | 23 Oct، تا ~06:00 | چرخه تصمیم کامل + ریسک + خروج + لاگ + تست ۴–۷ و ۱۰ |
| P3 | 23 Oct، تا ~09:00 | Shadow واریانت A + ضبط داده، **روشن** |
| P4 | 23 Oct، تا ~20:00 | Backtest کامل + گزارش |
| P5 | 24 Oct، تا ~08:00 | Order Flow، خبر، Macro، LLM/Jev Judge، واریانت B/C (مشروط به کلید) |
| P6 | 24 Oct، تا ~14:00 | README و ARCHITECTURE برای داور |

**اولویت‌بندی در صورت کمبود وقت** (به ترتیب، آنچه اول حذف/کوچک می‌شود):
1. مقایسه تایم‌فریم `(1D, 4h)` و `(1h, 15m)` ← فقط پیش‌فرض `(4h, 1h)`
2. گرید پارامتر ← فقط پیش‌فرض‌ها + حساسیت کارمزد
3. بنچمارک ورود تصادفی ۲۰۰ بار ← ۵۰ بار
4. RSS/خبر ← فقط Macro
5. `ReplayMarketView` ← فقط ضبط داده (بدون replay)

موارد **غیرقابل حذف**: تست‌های پذیرش §۱۳، لاگ تصمیم، Shadow واریانت A، Backtest اپیزودیک پیش‌فرض، README قابل اجرا.

---

### ۱.۱ برنامه فشرده امشب (به‌روزرسانی 2026-10-22 19:30 تهران)

تیم: کار باید تا **۱ تا ۲ بامداد امشب (تهران)** نهایی شود. پایان مسابقه: **جمعه ۲۱:۰۰ تهران = 2026-10-23 17:30 UTC**. Shadow پس از امشب روی همین سیستم تا پایان مسابقه روشن می‌ماند و گزارش ساعتی خودش را به‌روز می‌کند.

| ساعت (تهران) | فاز | خروجی |
|---|---|---|
| ۱۹:۴۵ تا ۲۲:۰۰ | P2 | چرخه تصمیم، ریسک، خروج، شبیه‌ساز Backtest، لاگ تصمیم، تست‌های ۴ تا ۷، ۹ و ۱۰ |
| ۲۲:۰۰ تا ۲۲:۴۵ | P3 (+ بخشی از P5) | Shadow با واریانت A (قواعد) و B (Laya، از قبل تأییدشده) + ضبط داده، **روشن** |
| ۲۲:۴۵ تا ۰۰:۱۵ | P4 | Backtest اپیزودیک پیش‌فرض + حساسیت کارمزد + بنچمارک‌ها (نقد، BTC، ورود تصادفی ۵۰ بار) + گزارش |
| ۰۰:۱۵ تا ۰۱:۳۰ | P6 | README و ARCHITECTURE برای داور |

**حذف‌شده برای امشب** (در گزارش به‌عنوان محدودیت ذکر می‌شود): خبر/RSS و واریانت C، واریانت D (BoxAPI) مگر وقت بماند، `ReplayMarketView`، مقایسه تایم‌فریم‌ها، گرید پارامتر، ۲۰۰ اجرای تصادفی (← ۵۰).

## ۲. ترتیب اعتبار و حل تعارض‌ها

ترتیب از `09` §۱: بریف مسابقه ← API واقعی ← `09` (v1.2) ← Spec v1.1 ← config ← Judge ← محتوای خارجی.

تعارض‌های شناسایی‌شده و نحوه حل:

| تعارض | منبع | حل |
|---|---|---|
| Article 2 و DEC-02 و ADR-003: «تنها استراتژی = مومنتوم + Order Flow؛ Mean Reversion ممنوع» | `01`، `00`، `07` | ADR-017 در `09` جایگزین می‌کند (سه Setup رژیم‌محور، شامل T3). طبق Article 18 باید ADR در `07` ثبت شود ← کار P0 |
| وزن‌های ۳۰/۳۰/۲۰/۱۰/۱۰ و آستانه ۷۰ | `02` §4–5 | ADR-018 و ADR-021: OBI/TFI فقط گیت تأیید در Shadow؛ امتیاز رتبه‌بندی فقط کندلی (`09` §۷.۶) |
| EMA 9/21 | `02` §9 | ADR-020: EMA20/50 + ADX + DI |
| «Backtest/Shadow خارج از محدوده» | `00` §10، Article 17، `06` §14، ADR-016 | ADR-025: الزامی هستند |
| «بدون نرخ کارمزد معتبر ← NO_TRADE» | `08` §3 | در Backtest/Shadow مجاز است از config با `assumed: true` بخوانیم (`09` §۰ بند ۴ و §۴)؛ در Live همچنان NO_TRADE. چون کلید والکس موجود است، V7 اجرا می‌شود |
| «فروش LIMIT پیش‌فرض» در برابر شبیه‌ساز بدبینانه Taker | Article 22، `05` §10 در برابر `09` §۸.۱ | Backtest: مدل بدبینانه `09` §۸.۱. Shadow: سؤال باز Q-12 |

`09` می‌گوید متن آن باید در `docs/spec/09-...md` ذخیره شود؛ **از قبل موجود است** و دست نمی‌خورد.

---

## ۳. محیط توسعه (بررسی‌شده روی ماشین تیم)

| ابزار | وضعیت | تصمیم پیشنهادی |
|---|---|---|
| Python | `python` روی PATH نیست (alias فروشگاه)؛ `py` launcher با 3.13، 3.12، 3.9 موجود | `py -3.12 -m venv .venv` (3.12 برای سازگاری بهتر pyarrow/socketio) |
| git | **نصب نیست** | لازم است: فیلد `git_commit` در هر گزارش و تحویل گیت‌هاب. تا نصب، fallback: `nogit-<sha256 درخت src>` (Q-05) |
| make | نصب نیست | `Makefile` برای داور + `tasks.ps1` برای ویندوز؛ هر دو فقط `python -m wallex_agent <cmd>` را صدا می‌زنند تا منطق یک‌جا باشد |
| uv | نصب نیست | `requirements.in` + `requirements.lock` (با `pip freeze` پس از نصب، نسخه‌های دقیق) |

---

## ۴. ساختار ماژول‌ها

همان ساختار `09` §۱۲، به‌علاوه چند فایل زیرساختی (با علامت ★):

```text
config/
  default.yaml            همه پارامترهای Experimental؛ هر عدد غیرتأییدشده با assumed: true
  allowed_markets.yaml    خالی تا اعلام فهرست رسمی (BLOCK-002)
  macro_events.yaml       تیم پر می‌کند
  rss_feeds.yaml          P5
  rulebook.md             پیش‌نویس من، ویرایش تیم
src/wallex_agent/
  ★ config.py             مدل pydantic کل config + config_hash (sha256 نسخه canonical)
  ★ cli.py / __main__.py  verify-api | fetch-data | backtest | shadow | report | test
  ★ secrets.py            خواندن .env + فیلتر redaction برای همه loggerها
  data/
    wallex_rest.py        کلاینت httpx فقط-GET با whitelist مسیرها، throttle، retry، ثبت 429
    wallex_ws.py          socket.io v2 (EIO=3)، reconnect، heartbeat — P3/P5
    candles.py            دریافت udf/history، حذف کندل باز، ذخیره افزایشی Parquet، تجمیع 1h→4h/1D با مرز UTC
    market_meta.py        is_spot، minNotional/minQty/stepSize/tickSize، snapshot با زمان
    fees.py               FeeSnapshot(id, maker, taker, source, retrieved_at, validity, assumed)
    market_view.py        MarketView (interface) ← Historical | Live | Replay
  features/
    indicators.py         EMA، ATR(Wilder)، ADX/DI، RSI، BB، Donchian، RelVol، Chaikin A/D، ATR% pct
    orderflow.py          OBI، TFI، پنجره‌های تطبیقی — P5 (OBI در P3 فقط log)
  strategy/
    universe.py           §۷.۱
    btc_filter.py         §۷.۲ روی BTCUSDT
    regime.py             §۷.۳ + هیسترزیس
    triggers.py           T1/T2/T3/Gray §۷.۴
    fee_horizon.py        §۷.۵ (g_be، required_move، rr_net، انتخاب H، cost_to_move)
    ranking.py            §۷.۶
    candidate.py          dataclass کاندیدا + candidate_id قطعی (هش symbol+ts+setup+config_hash)
  context/
    news.py, news_interpreter.py, macro.py, fng.py, context_builder.py — macro در P2، بقیه P5
    caveman.py            stub، خاموش
  judge/
    base.py               EntryJudge + JudgeDecision + اعتبارسنجی Fail-Closed (timeout/schema/id/expiry)
    rules_baseline.py     P2
    laya.py               P5 — Judge اصلی Shadow (ADR-026): خلاصه انگلیسی ≤۵۱۲ توکن، سؤال‌های اتمی، ترکیب قطعی `09` §۷.۹.۱
    llm_judge.py          P5 — BoxAPI / gpt-6-luna فقط واریانت D (ADR-027)، با سقف هزینه؛ بقیه providerها فقط interface
  risk/
    sizing.py, gates.py   §۷.۱۰
    portfolio.py          slotها با رزرو اتمیک (Lock)، ریسک باز، زیان ۲۴ساعته، Kill Switch، peak equity
  execution/
    broker.py             interface + OrderIntent + ماشین وضعیت `05` §13
    backtest_sim.py       §۸.۱
    shadow_sim.py         §۸.۲
    live.py               فقط NotImplementedError
  positions/manager.py    §۷.۱۱ — فقط SELL_INTENT؛ stop فقط بالا می‌رود
  engine/
    decision_cycle.py     تنها تابع چرخه تصمیم
    clock.py              SimClock | WallClock
    state_store.py        SQLite (فقط Shadow)
  logging/decision_log.py decisions.jsonl + judge_calls/
  backtest/  runner.py, episodes.py, walkforward.py, benchmarks.py, metrics.py, report.py
  shadow/    runner.py, variants.py, recorder.py, report.py
tests/
Makefile, tasks.ps1, requirements.in, requirements.lock, README.md
```

### ۴.۱ قرارداد چرخه تصمیم

```text
run_cycle(t, view: MarketView, portfolio, broker, judge, cfg, log) -> CycleResult
  1. exits      : positions.manager روی همه پوزیشن‌ها ← SELL_INTENTها ← broker   (قبل از هر ورود، مستقل از Judge)
  2. halt check : Kill Switch / زیان ۲۴h / settlement / macro / BTC filter
  3. universe   → regime → triggers → fee_horizon → ranking (top 3)
  4. order_flow : فقط اگر view.has_orderbook (Shadow/Live)
  5. news gate  : فقط اگر view.has_news
  6. judge      : با timeout و Fail-Closed
  7. risk       : sizing + gates + final recheck ← BUY intent ← broker
  8. log        : یک رکورد برای هر کاندیدا، حتی ردشده
```

گیت‌های «فقط Shadow/Live» با قابلیت‌های `MarketView` فعال می‌شوند، نه با `if mode == ...`؛ این تنها استثنای مجاز `09` §۳ است.

### ۴.۲ ایمنی (غیرقابل‌مذاکره)

- `wallex_rest.py` فقط متد `GET` دارد و مسیرها را با whitelist چک می‌کند؛ هر مسیر `/v1/account/orders` یا `otc` یا برداشت را رد می‌کند (حتی GET سفارش‌ها لازم نیست).
- تست استاتیک: grep روی `src/` برای `post(`، `delete(`، `put(`، `/otc`، `withdraw` ← باید خالی باشد (به‌جز `live.py` که فقط raise می‌کند).
- تست §۸.۳: monkeypatch روی `LiveBroker.__init__` و اجرای کامل backtest و shadow-dry ← هرگز صدا زده نمی‌شود.
- کلید `.env` فقط از طریق `secrets.py` خوانده می‌شود؛ یک `logging.Filter` مقدار هر کلید را در همه لاگ‌ها با `***` جایگزین می‌کند؛ تست برای آن.
- `.env` هرگز خوانده/چاپ نمی‌شود توسط من؛ فقط کد runtime.

---

## ۵. جزئیات پیاده‌سازی به ترتیب فاز

### P0 — اعتبارسنجی API (پس از تأیید این PLAN)

1. ساخت venv، `requirements.in`، lock، اسکلت پکیج، `wallex_rest.py` فقط-GET.
2. ثبت ADR-017 تا ADR-025 در `docs/spec/07-decisions-and-sources.md` (بخش جدید «ADRهای v1.2»)، به‌روزرسانی `MANIFEST.md` (Q-14).
3. اسکریپت `python -m wallex_agent verify-api` برای V1 تا V9:
   - V2: درخواست resolutionهای `15, 60, 240, 1D`؛ مقایسه آخرین کندل با `now` برای تشخیص کندل باز؛ تست مرز ۴ ساعته.
   - V3: جست‌وجوی دودویی روی `from` برای یافتن عمیق‌ترین تاریخچه 1h و 4h در ۱۰ جفت پرحجم.
   - V5: نمونه‌گیری هم‌زمان `trades` و `depth` در چند دور؛ نسبت معاملات `isBuyOrder=true` با قیمت ≥ ask.
   - V6: یافتن کلاینت سازگار (`python-socketio<5` با EIO=3) و تست ۴ کانال.
   - V7: `GET /v1/account/fee` (تنها فراخوانی احراز هویت‌شده). پاسخ بدون کلید ثبت می‌شود.
   - V8: فقط از مستندات.
   - V10: `pip install laya` در venv، اجرای `laya-serve`، ثبت شکل دقیق `POST /v1/systemone` برای boolean/choice، نسخه مدل، شمارش توکن، تأخیر p50/p95 روی CPU (۵۰ فراخوانی). اگر محلی نشد: **توقف و گزارش به تیم**، نه Vercel.
   - V11: یک فراخوانی کوتاه BoxAPI با `gpt-6-luna` (پشتیبانی JSON، تأخیر، مصرف توکن).
4. خروجی: `docs/api-verification.md` + `data/snapshots/meta_<ts>.json` + فهرست TMN اسپات با حجم ۲۴h و اسپرد.

### P1 — داده و اندیکاتورها

- `candles.py`: کش افزایشی Parquet، رکورد با `source, event_time, received_time, processed_time, quality_status`؛ تشخیص gap.
- تجمیع 4h از 1h اگر V2 نشان دهد `240` هم‌تراز UTC نیست (روش در گزارش ثبت می‌شود).
- `HistoricalMarketView.as_of(t)`: فقط کندل‌هایی با `close_time ≤ t` — تنها دروازه دسترسی موتور به داده.
- اندیکاتورها با pandas/numpy؛ تست مرجع با `ta` یا مقادیر دستی (کتابخانه مرجع فقط در `tests/`).
- **تست ۱ (ضد نگاه به آینده)**: ۵۰ لحظه تصادفی با seed ثابت؛ موتور روی داده بریده‌شده تا `t` و کل داده ← خروجی برابر. در P1 روی featureها، در P2 روی تصمیم کامل تکرار می‌شود.
- `fetch-data`: دانلود ≥ ۲۴۰ روز 1h (۶۰ روز warm-up + ۱۸۰ روز Backtest) برای Universe + BTCUSDT + BTCTMN + USDTTMN، و snapshot با هش.

### P2 — چرخه تصمیم کامل

- `universe → btc_filter → regime → triggers → fee_horizon → ranking → judge(baseline) → risk → broker(backtest_sim) → positions`.
- `portfolio.py`: رزرو اتمیک با `threading.Lock`، وضعیت‌های `BUY_INTENT_RESERVED / BUY_PENDING / OPEN`، `UNKNOWN` جایگاه را نگه می‌دارد.
- `decision_log.py`: همه فیلدهای `09` §۱۱.
- Macro gate (زمانی) از `macro_events.yaml`.
- تست‌های ۴، ۵، ۶ (با judge mock)، ۷، ۱۰ + تکرار تست ۱ روی تصمیم‌ها.

### P3 — Shadow حداقلی (روشن ماندن مداوم)

- `shadow/runner.py`: حلقه async؛ polling کندل ۲۰ ثانیه بعد از هر ساعت؛ پایش stop هر ۱۵ ثانیه با Bid (REST `depth` در P3، وب‌سوکت در P5 اگر وقت نبود).
- `recorder.py`: depth ۲۰ سطح هر ۱۰ ثانیه + trades ← `data/recordings/{date}/*.parquet`.
- `state_store.py` (SQLite) برای ری‌استارت بدون گم شدن پوزیشن.
- `ShadowSimBroker`: walk-the-book برای Taker.
- اجرای پایدار روی ویندوز: `tasks.ps1 shadow` در یک ترمینال جدا + خودبازیابی؛ جلوگیری از sleep سیستم (Q-11).
- گزارش ساعتی `reports/shadow/{session_id}/summary.md`.
- `challenge_end_utc = 2026-10-24T17:30Z` در config؛ برای تست تسویه، پایان مجازی قابل تنظیم.

### P4 — Backtest کامل

- `episodes.py`: اپیزودهای ۷۲ساعته غیرهم‌پوشان در Validation و Test؛ ۴ ساعت شروع مختلف (00، 06، 12، 18 UTC).
- `walkforward.py`: Train 60٪ / Val 20٪ / Test 20٪؛ کالیبراسیون `c_move` روی Train؛ گرید کوچک روی Val؛ **Test فقط یک بار** (قفل با فایل نشانه `reports/backtest/TEST_RUN_LOCK`).
- `benchmarks.py`: نقد، B&H BTCTMN، B&H هم‌وزن ۵ نماد، ورود تصادفی ۲۰۰ seed.
- Ablation و حساسیت‌ها طبق `09` §۹.۳ و §۱۵ (ریسک ۰٫۵٪/۲٪ فقط اطلاعی).
- تست ۲ (تکرارپذیری بایت‌به‌بایت `trades.csv`) و تست ۸ (موجودی کوین صفر پایان اپیزود).
- خروجی‌های §۹.۵ و نمودارها با matplotlib (backend `Agg`).

### P5 — Order Flow، خبر، Judge هوشمند

- OBI `confirm`، TFI `log_only` تا تأیید V5.
- RSS (فقط منابع انگلیسی) + `NewsInterpreter` با Laya: سه سؤال choice روی تیتر؛ `assets[]` با تطبیق alias.
- `LayaJudge` (واریانت B و C) و `LLMJudge` BoxAPI (واریانت D، با سقف هزینه).
- دفتر خلاف واقع برای کاندیداهای ردشده.
- اگر Laya محلی اجرا نشد (V10): B/C متوقف تا تصمیم تیم؛ A و D ادامه می‌یابند.

### P6 — مستندات داور

- `README.md`: نصب و اجرای گام‌به‌گام روی ویندوز و لینوکس، اجرای آفلاین backtest با snapshot.
- `ARCHITECTURE.md`: معماری، نقش AI، قواعد خرید/فروش، ریسک، نتایج، محدودیت‌های §۱۵.

---

## ۶. فرضیات (همه در config با `assumed: true` یا در گزارش)

| # | فرض | دلیل / جایگزین |
|---|---|---|
| A1 | **(تأیید تیم، Q-08)** در فرمول qty، `est_exit_cost%` = **کل هزینه رفت‌وبرگشت**: `g_be + slip_rt + spread_cost` (کارمزد خرید + فروش + لغزش دو طرف + اسپرد). نتیجه: اگر stop بخورد، زیان با احتساب همه کارمزدها از بودجه ریسک (۰٫۲۵٪) بیشتر نمی‌شود | پاسخ تیم: «هزینه خروج باید در ریسک و ریوارد حساب شود تا فقط به‌خاطر فروش و کارمزد ضرر نکنیم» |
| A2 | **(تأیید تیم، Q-08)** در `rr_net = (target_dist% − cost) / (stop_dist% + cost)`، `cost` = همان کل هزینه رفت‌وبرگشت A1 (بدون buffer و min_net_edge که جداگانه در `required_move` هستند) | همان |
| A3 | stop اولیه T3 هم از قاعده `k_atr` می‌آید؛ هدف T3 = `BB_mid` در کندل ورود (ثابت) برای گیت، و لمس `BB_mid` جاری برای خروج | `09` §۷.۵ و §۷.۱۱ |
| A4 | breakeven = `entry × (1 + g_be)`؛ اسلیپیج در آن لحاظ نمی‌شود | عیناً طبق `09` |
| A5 | 4h و 1D از تجمیع 1h با مرز UTC (اگر V2 خلافش را نشان ندهد) | `09` §۵.۲ |
| A6 | `settlement_entry_cutoff = end − 90min` | `09` §۷.۱۰ |
| A7 | رژیم تأییدشده در شروع = `UNKNOWN`، تا دو کندل متوالی هم‌نظر | هیسترزیس شروع سرد را تعریف نکرده |
| A8 | `size_mult = 0.5` اگر gray یا BTC_HIGH_VOL (ضرب‌نشده) | عیناً طبق `09` |
| A9 | کارمزد پیش‌فرض Backtest = نرخ Taker از V7؛ اگر V7 شکست خورد، مقدار config با `assumed: true` که تیم تعیین می‌کند | Q-06 |
| A10 | اسلیپیج: ۰٫۰۵٪ ۵ نماد اول، ۰٫۱۵٪ بقیه (مثال `09` §۸.۱) با `assumed: true` | |
| A11 | Universe Backtest = جفت‌های TMN فعال امروز (survivorship bias در گزارش) | `09` §۷.۱ |
| A12 | RS vs BTC با `BTCTMN`، رژیم BTC با `BTCUSDT` | `09` §۶ و ADR-022 |
| A13 | Macro در Backtest فقط زمانی؛ شرط «اسپرد BTCTMN < ۱٫۵× میانه» فقط در Shadow | اسپرد تاریخی نداریم |
| A14 | candidate_id قطعی (هش) تا تکرارپذیری حفظ شود | تست ۲ |
| A15 | `allowed_markets.yaml` خالی ← همه TMN اسپات فعال مجاز، و در گزارش ذکر می‌شود | `09` §۷.۱ |

---

## ۷. نگاشت تست‌های پذیرش (`09` §۱۳)

| # | تست | فاز | فایل |
|---|---|---|---|
| 1 | ضد نگاه به آینده (۵۰ لحظه، seed ثابت) | P1 (feature)، P2 (تصمیم) | `tests/test_lookahead.py` |
| 2 | تکرارپذیری بایت‌به‌بایت | P4 | `tests/test_reproducibility.py` |
| 3 | اندیکاتورها با مرجع | P1 | `tests/test_indicators.py` |
| 4 | ریسک `05` §17 (۶ مورد، شامل هم‌زمانی با thread) | P2 | `tests/test_risk_*.py` |
| 5 | Kill Switch با افت ۳٪ | P2 | `tests/test_kill_switch.py` |
| 6 | Judge: timeout، JSON نامعتبر، id نامطابق، منقضی؛ SELL بدون Judge | P2 | `tests/test_judge_failclosed.py` |
| 7 | گیت کارمزد: ۰٫۵٪ هر طرف، هدف ۱٪ ← رد؛ تغییر snapshot ← ابطال | P2 | `tests/test_fee_gate.py` |
| 8 | موجودی کوین صفر پایان اپیزود | P4 | `tests/test_settlement.py` |
| 9 | عدم دسترسی به LiveBroker | P2 | `tests/test_no_live.py` |
| 10 | stop هرگز پایین نمی‌آید (property-based با hypothesis) | P2 | `tests/test_trailing.py` |
| + | redaction کلید، grep استاتیک endpointهای ممنوع | P0 | `tests/test_safety.py` |

---

## ۸. سؤال‌های باز

فهرست کامل در `docs/OPEN_QUESTIONS.md`. **مواردی که قبل از شروع P0 یا در طول آن نیاز به پاسخ دارند:**

- ~~Q-00~~ حل شد: فعلاً فقط Backtest و Shadow.
- ~~Q-03/Q-04~~ حل شد: Laya محلی (ADR-026) + BoxAPI برای واریانت D (ADR-027).
- **Q-05:** نصب git برای `git_commit` و تحویل گیت‌هاب.
- ~~Q-18، Q-19، Q-21~~ حل شد: `unclear` مسدود نمی‌کند؛ `gpt-6-luna` ارزان‌ترین مدل است و سقف ۲ دلار؛ Laya روی `127.0.0.1:8000`.
- **Q-22:** مقایسه checkpoint `laya-typed-decisions` (۱۰۲۴ توکن) با `laya` (۵۱۲) در V10؟

---

## ۹. آنچه بعد از تأیید انجام می‌دهم

1. پاسخ‌های تیم به Q-00، Q-05 و Q-14 را اعمال می‌کنم (بقیه سؤال‌ها مانع P0 نیستند).
2. P0 را اجرا می‌کنم، `docs/api-verification.md` را می‌نویسم، `OPEN_QUESTIONS.md` را به‌روز می‌کنم و دوباره متوقف می‌شوم.
