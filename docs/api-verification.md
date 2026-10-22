# api-verification.md — Phase 0

> اجرا: 2026-10-22 حدود 15:04 تا 15:45 UTC، فقط فراخوانی‌های خواندنی (GET). هیچ سفارشی ثبت نشد.
> شواهد خام: `data/snapshots/verify_20261022T150427Z/` (`summary.json`، پاسخ‌های کامل بازارها، depth، نمونه‌های V5).
> دستور بازتولید: `python -m wallex_agent verify-api` و `python -m wallex_agent verify-llm`
> هیچ کلیدی در این سند یا شواهد ثبت نشده است.

## خلاصه

| # | موضوع | نتیجه | اثر روی طراحی |
|---|---|---|---|
| V1 | بازارها و محدودیت‌ها | ✅ ۱۸۷ جفت TMN اسپات؛ `minNotional = 50,000` تومان؛ `stepSize`/`tickSize` = **تعداد رقم اعشار**، نه گام | کف خرید = `max(100,000, 50,000)` = ۱۰۰٬۰۰۰ |
| V2 | resolutionهای کندل | ⚠️ فقط `60` و `1D` واقعی‌اند؛ `240` کندل ۱h و `15` کندل ۱m برمی‌گرداند؛ آخرین کندل **باز** است | ۴h از تجمیع ۱h؛ حذف کندل باز اجباری |
| V3 | عمق تاریخچه | ✅ BTCTMN/ETHTMN حدود ۲۸۹۰ روز؛ همه ۱۰ جفت برتر ≥ ۲۳۰ روز؛ در ۴۰۰ روز اخیر فقط دو gap کوچک (۳h و ۲h) | ۲۴۰ روز (۶۰ warm-up + ۱۸۰ backtest) کافی است، به‌جز USOONTMN (۲۳۰ روز) |
| V4 | دفتر سفارش | ✅ ۵۰ سطح در هر سمت؛ `/v2/depth/all` همه ۳۷۳ نماد را در یک درخواست می‌دهد | OBI و ضبط depth با یک درخواست |
| V5 | معنای `isBuyOrder` | ⚠️ **نامطمئن**؛ نمونه کوچک (۲۳) با تمایل به معنای معکوس | TFI در `log_only` می‌ماند |
| V6 | وب‌سوکت | ❌ در دسترس نیست (503 / گواهی منقضی) | Shadow با REST polling |
| V7 | کارمزد حساب | ❌ `400 Bad Request` با کلید فعلی | کارمزد از config با `assumed: true` |
| V8 | انواع سفارش | ⚠️ سایت مستندات در دسترس نیست؛ فقط شواهد غیرمستقیم | شبیه‌ساز: LIMIT و MARKET |
| V9 | rate limit | ✅ `x-ratelimit-limit: 600`؛ در ۴۹۷ درخواست هیچ 429 دیده نشد | throttle ۰٫۱۵ ثانیه کافی است |
| V10 | Laya محلی | ✅ اجرا روی CPU؛ p50 ۱٫۵۷ ثانیه، p95 ۱٫۷۵ ثانیه برای ۴ سؤال؛ قطعی؛ کوتاه‌سازی دقیق گزارش می‌شود | `LayaJudge` و `NewsInterpreter` شدنی‌اند؛ state ≤ ~۳۰۰ توکن |
| V11 | BoxAPI | ✅ کار می‌کند؛ ⚠️ `temperature=0` پشتیبانی نمی‌شود | Q-23 |

---

## V1 — بازارها

- `GET /v1/markets` ← `result.symbols{SYMBOL: {...}}`، ۳۷۳ نماد. فیلدها: `symbol, baseAsset, quoteAsset, baseAssetPrecision, quotePrecision, minQty, minNotional, stepSize, tickSize, isMarketTypeEnable, isZeroFee, stats{bidPrice, askPrice, lastPrice, 24h_tmnVolume, 24h_quoteVolume, ...}, createdAt`.
- `GET /hector/web/v1/markets` ← `result.markets[]`، ۴۶۹ نماد. فیلدها: `is_spot, is_otc, is_margin, amount_precision, price_precision, is_market_type_enable, quote_volume_24h, fair_price{...}, ...`.
- همه نمادهای اسپات hector در `/v1/markets` هم هستند.
- **`stepSize == amount_precision` و `tickSize == price_precision` در ۳۷۳ از ۳۷۳ نماد**؛ یعنی این دو فیلد تعداد رقم اعشار مجازند (مثلاً BTCTMN: `stepSize=8` یعنی گام ۱e-8). در کد: `qty_step = 10**-stepSize`، `price_tick = 10**-tickSize`.
- `minNotional` برای همه ۳۰ جفت پرحجم ۵۰٬۰۰۰ تومان است ← کف سیاست تیم (۱۰۰٬۰۰۰) غالب است.
- فهرست کامل TMN اسپات با حجم ۲۴ساعته و اسپرد: `data/snapshots/verify_20261022T150427Z/tmn_spot_markets.json`.

۱۵ جفت اول بر اساس حجم ۲۴ساعته تومانی (لحظه اجرا):

| نماد | حجم ۲۴h (میلیارد تومان) | اسپرد % | minNotional | stepSize | tickSize |
|---|---:|---:|---:|---:|---:|
| USDTTMN | 259.6 | 0.002 | 50,000 | 2 | 0 |
| BTCTMN | 119.5 | 0.051 | 50,000 | 8 | 0 |
| PEPETMN | 98.2 | 0.125 | 50,000 | 0 | 4 |
| PUMPTMN | 95.8 | **0.704** | 50,000 | 1 | 0 |
| ETHTMN | 84.8 | 0.094 | 50,000 | 5 | 0 |
| BNBTMN | 77.7 | 0.176 | 50,000 | 5 | 0 |
| USOONTMN | 77.3 | 0.270 | 50,000 | 4 | 0 |
| DOGETMN | 43.0 | 0.199 | 50,000 | 1 | 0 |
| XAUTTMN | 38.6 | 0.161 | 50,000 | 5 | 0 |
| ZECTMN | 36.3 | **0.365** | 50,000 | 4 | 0 |
| XRPTMN | 34.6 | 0.154 | 50,000 | 1 | 0 |
| ARBTMN | 34.1 | **0.429** | 50,000 | 4 | 0 |
| SOLTMN | 24.5 | **0.367** | 50,000 | 5 | 0 |
| DASHTMN | 20.5 | 0.244 | 50,000 | 5 | 0 |
| XMRTMN | 12.8 | **1.274** | 50,000 | 4 | 0 |

نکته‌ها:
- بسیاری از جفت‌ها اسپرد بالاتر از آستانه ۰٫۳۰٪ دارند و طبق `09` §۷.۱ حذف می‌شوند. این اسپرد لحظه‌ای است؛ برآورد ایستای Backtest باید از چند snapshot (مثلاً میانه طی ضبط Shadow) ساخته شود.
- در فهرست، دارایی‌های غیرکریپتو هم هستند: `XAUTTMN` (طلای توکنی)، `USOONTMN` و `COPXONTMN` (به‌احتمال زیاد سهام/ETF توکنی). **Q-24**.

## V2 — کندل‌ها (`GET /v1/udf/history?symbol&resolution&from&to`)

- قالب TradingView UDF: `{s, t[], o[], h[], l[], c[], v[]}`؛ `t` زمان **باز شدن** کندل به ثانیه epoch (UTC). قیمت‌ها و حجم به‌صورت رشته.
- | resolution درخواستی | چیزی که برمی‌گردد |
  |---|---|
  | `60` | ✅ کندل ۱ ساعته، هم‌تراز ساعت UTC |
  | `1D` / `D` | ✅ کندل روزانه، مرز 00:00 UTC |
  | `240` | ❌ کندل **۱ ساعته** (گام ۳۶۰۰ ثانیه) |
  | `15` | ❌ کندل **۱ دقیقه‌ای** (گام ۶۰ ثانیه) |
  | `1`، `5`، `30`، `120`، `4H`، `240m`، `1W` | خالی |
- **تصمیم (طبق `09` §۵.۲):** ۴h از تجمیع ۱h با مرزهای 00/04/08/12/16/20 UTC؛ کندل ۴h فقط وقتی معتبر است که هر ۴ کندل ۱h آن موجود و بسته باشند.
- **آخرین کندل برگشتی در حال شکل‌گیری است:** کندل 15:00 در ساعت 15:04 برگشت و طی ۲۵ ثانیه `close` و `volume` آن تغییر کرد. حذف کندل‌هایی با `t + period > now` اجباری است.
- **سقف تعداد کندل در هر درخواست:** حدود **۶۹۵** کندل (درخواست ۱۰۰۰ تا ۳۰۰۰ ساعت، همه ۶۹۵ برگرداندند؛ ۵۰۰ ساعت ← ۵۰۰). دریافت در قطعه‌های ۵۰۰ ساعته.
- برای مقایسه تایم‌فریم `(1h, 15m)`: ۱۵m باید از داده ۱m (resolution=`15`) ساخته شود. عمق تاریخچه ۱m بررسی نشده است.

## V3 — عمق تاریخچه ۱h (جست‌وجوی دودویی روی `from`)

| نماد | اولین داده (روز قبل) |
|---|---:|
| BTCTMN، ETHTMN | ~2892 |
| BTCUSDT | ~2570 |
| XRPTMN | ~1992 |
| DOGETMN | ~1908 |
| BNBTMN | ~1873 |
| PEPETMN | ~1252 |
| XAUTTMN | ~814 |
| PUMPTMN | ~434 |
| ZECTMN | ~393 |
| USOONTMN | ~231 |

- gapها در ۴۰۰ روز اخیر (BTCTMN): `2026-03-02 02:00→05:00` (۳ ساعت) و `2026-09-22 12:00→14:00` (۲ ساعت). gapهای بزرگ‌تری که در اجرای اول دیده شد، اثر همان سقف ۶۹۵ کندلی بود، نه نبود داده.
- سیاست gap: ATR و اندیکاتورها از روی gap پل نمی‌زنند؛ کندل ۴h ناقص `UNKNOWN` می‌دهد؛ اپیزود Backtest که gap بیش از ۱ ساعت داشته باشد علامت می‌خورد و در گزارش جدا شمرده می‌شود.

## V4 — دفتر سفارش

- `GET /v1/depth?symbol=BTCTMN` ← `result{ask[], bid[]}`؛ هر سطح `{price (number), quantity (number), sum (string)}`؛ ۵۰ سطح در هر سمت؛ ask صعودی، bid نزولی.
- `GET /v2/depth/all` ← `result{SYMBOL: {ask[], bid[]}}` برای **۳۷۳ نماد در یک درخواست** (تا ۵۰ سطح؛ بازارهای کم‌عمق کمتر).
- **تصمیم:** ضبط depth در Shadow با یک فراخوانی `/v2/depth/all` هر ۱۰ ثانیه (۶ درخواست در دقیقه)، به‌جای وب‌سوکت.

## V5 — معنای `isBuyOrder` (BLOCK-003)

`GET /v1/trades?symbol` ← `result.latestTrades[]` با `{symbol, quantity, price, sum, isBuyOrder, timestamp (ISO UTC، دقت ثانیه)}`.

روش: ۱۲ دور روی ۵ جفت پرحجم؛ در هر دور اول depth و بلافاصله trades؛ فقط معاملات کمتر از ۲۰ ثانیه قبل از snapshot مقایسه شدند.

| `isBuyOrder` | زیر Mid | بالای Mid | روی/زیر Bid | روی/بالای Ask |
|---|---:|---:|---:|---:|
| `true` | 14 | 4 | 7 | 4 |
| `false` | 2 | 3 | 0 | 2 |

- اگر `true` یعنی «آغازکننده خریدار بوده»، این معاملات باید نزدیک Ask باشند؛ ۱۴ از ۱۸ زیر Mid بوده‌اند. **تمایل به معنای معکوس** دیده می‌شود (`isBuyOrder` = سفارش منتظر/Maker سمت خرید ← آغازکننده فروشنده)، اما نمونه کوچک است (۲۳) و snapshot با معامله هم‌زمان نیست.
- **وضعیت: تأییدنشده.** TFI طبق `09` §۷.۷ در `log_only` می‌ماند. ضبط Shadow (معاملات + depth هر ۱۰ ثانیه) نمونه بزرگ‌تری می‌دهد و تحلیل در P3/P5 تکرار می‌شود.

## V6 — وب‌سوکت

- `python-socketio 4.6.1` (EIO=3) به `https://api.wallex.ir` ← `ConnectionError`.
- `https://api.wallex.ir/socket.io/?EIO=3&transport=polling` و `EIO=4` ← **503** از nginx.
- `ws.wallex.ir` ← گواهی TLS منقضی؛ `socket.wallex.ir` ← DNS ندارد.
- **تصمیم (کم‌ریسک‌ترین):** Shadow با REST polling: `/v2/depth/all` هر ۱۰ ثانیه، `/v1/trades` برای Universe فعال و پوزیشن‌های باز هر ~۱۵ ثانیه، پایش stop با Bid از depth. حدود ۱۰۰ تا ۱۵۰ درخواست در دقیقه، زیر سقف ۶۰۰. **Q-25**.

## V7 — کارمزد حساب (`GET /v1/account/fee`)

| هدر | پاسخ |
|---|---|
| بدون کلید | `401 {"code":1201,"message":"authorization header is missing"}` |
| `x-api-key: <key>` | `400 Bad Request` |
| `X-API-Key` + `Accept: application/json` | `400 Bad Request` |
| `x-api-key` + `?symbol=BTCTMN` | `400 Bad Request` |
| `Authorization: Bearer <key>` | `400 Bad Request` |

- endpoint وجود دارد و احراز هویت می‌خواهد، ولی کلید فعلی پذیرفته نمی‌شود (قالب کلید `id|token`، ۶۷ کاراکتر).
- **وضعیت: `pending`.** طبق `09` §۴، کارمزد از config با `assumed: true` خوانده می‌شود. **Q-06** به‌روز شد: تیم نوع کلید (کلید API حساب مسابقه؟) و نرخ کارمزد را تأیید کند. برای جلوگیری از قفل شدن حساب، تلاش بیشتری انجام نشد.

## V8 — انواع سفارش (فقط مستندات، سفارشی ارسال نشد)

- `api-docs.wallex.ir` (گواهی TLS نامعتبر، به GitHub Pages اشاره می‌کند) و `developers.wallex.ir/spot/*` (404) در دسترس نبودند.
- شواهد غیرمستقیم از متادیتا: فلگ `isMarketTypeEnable` / `is_market_type_enable` برای هر بازار (BTCTMN: `true`؛ PEPETMN و XLMTMN: `false`) ← MARKET فقط در برخی بازارها فعال است؛ LIMIT پایه است. سفارش STOP شاهدی ندارد.
- **تصمیم:** شبیه‌ساز Shadow برای بازارهایی که MARKET ندارند، خروج اضطراری را «LIMIT قابل‌اجرا روی Bid» مدل می‌کند (walk-the-book). STOP سمت صرافی فرض نمی‌شود؛ stop در موتور پایش می‌شود.

## V9 — rate limit

- هدرها: `x-ratelimit-limit: 600`، `x-ratelimit-remaining` (هدر reset یا `retry-after` دیده نشد).
- ۴۹۷ درخواست با throttle ۰٫۱۵ ثانیه: کمترین remaining = ۳۳۳، هیچ 429. پنجره احتمالاً یک دقیقه است (تأییدنشده).
- **برای جلوگیری از مسدود شدن، 429 عمداً تحریک نشد.** کلاینت: throttle ۰٫۱۵ ثانیه، backoff روی 429 و 5xx (یک 504 گذرا روی `udf/history` دیده شد و با retry برطرف شد)، و ثبت هدرها.

## V10 — Laya محلی ✅

**نصب** (در venv جداگانه `.venv-laya` تا torch محیط اصلی را سنگین نکند؛ Judge از طریق HTTP صحبت می‌کند):

```powershell
py -3.12 -m venv .venv-laya
.\.venv-laya\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\.venv-laya\Scripts\python.exe -m pip install "laya[serve]==0.4.13"   # extra «serve» = fastapi + uvicorn
```

- `laya` بدون extra `serve` سرور را اجرا نمی‌کند (`No module named 'uvicorn'`).
- قفل نسخه‌ها: `requirements-laya.lock` (torch `2.14.1+cpu`).

**اجرا** (فقط checkpoint انگلیسی، فقط روی loopback):

```powershell
$env:LAYA_HOST='127.0.0.1'; $env:LAYA_PORT='8000'; $env:LAYA_MODELS='english'; $env:LAYA_DEFAULT_MODEL='english'
$env:LAYA_DEVICE='cpu'; $env:LAYA_THREADS='8'
.\.venv-laya\Scripts\laya-serve.exe
```

- اجرای اول وزن‌ها را از Hugging Face دانلود می‌کند (کش محلی؛ دفعات بعد بدون اینترنت).
- `GET /health` ← `{"status":"ok","loaded":["english"],"revisions":{"english":"7b928d828b7b0e022f929d9bd2e44165aa270148"},"device":"cpu"}`.
- **نسخه مدل** برای لاگ: `model = "laya-rl-agent"` در پاسخ + `revision = 7b928d82…` از `/health` + `laya==0.4.13`. revision وزن‌ها در خود بسته pin شده است ← تکرارپذیر برای داور.
- هشدار هنگام بارگذاری: کالیبراسیون (temperature) برای سؤال‌های choice با **۱۱ گزینه یا بیشتر** نامعتبر است و confidence آن‌ها کالیبره‌نشده است. بزرگ‌ترین choice ما ۸ گزینه دارد (`event_type`).
- روی ویندوز هشدار symlink در کش Hugging Face می‌آید (بی‌اثر؛ فقط فضای دیسک بیشتر).

**قرارداد درخواست** (`POST /v1/systemone`، نمونه کامل در `data/snapshots/verify_laya/judge_request.json`):

```json
{
  "state": "<متن انگلیسی کاندیدا>",
  "model": "english",
  "questions": {
    "decision": {"type": "choice", "instructions": "...",
                 "criteria": {"APPROVE": "...", "REJECT": "...", "DEFER": "..."}},
    "signals_conflict": {"type": "noul", "instructions": "Do the main signals contradict each other?"}
  }
}
```

- نوع boolean در API نامش `noul` است؛ `criteria` برای noul اختیاری است (`{"true": "...", "false": "..."}`).
- `score` با `criteria` به‌صورت فهرست سطوح.

**قرارداد پاسخ** (نمونه کامل: `judge_response.json`):

```json
{
  "model": "laya-rl-agent",
  "answers": {
    "decision": {"type": "choice", "choice": "APPROVE",
                 "probabilities": {"APPROVE": 0.5211, "REJECT": 0.2196, "DEFER": 0.2593},
                 "confidence": 0.0692, "answer_confidence": 0.5211, "x_jev_confidence": 0.2817,
                 "action": {"act_probability": 1.0}},
    "signals_conflict": {"type": "noul", "noul": 0.1188, "confidence": 0.8812, "answer_confidence": 0.8812}
  },
  "usage": {"input_tokens": 1535, "output_tokens": 0, "state_tokens": 266,
            "state_tokens_dropped": 0, "truncated": false, "truncated_questions": []},
  "routing": {"...": "..."}
}
```

- برای `noul`، مقدار `noul` = **احتمال true**.
- `confidence` در Laya «آنتروپی نرمال‌شده» است، نه احتمال گزینه. طبق `09` §۷.۹.۱ (confidence = احتمال گزینه انتخاب‌شده) از **`answer_confidence`** / `probabilities[choice]` استفاده می‌شود.
- پاسخ شناسه کاندیدا را برنمی‌گرداند؛ تطبیق شناسه در wrapper ما انجام می‌شود (درخواست و پاسخ با `request_hash` جفت می‌شوند و candidate_id در خود state هست).

**بودجه توکن:**
- سقف ۵۱۲ **برای هر سؤال جداگانه** اعمال می‌شود (state + سؤال + گزینه‌ها؛ سهم سر سؤال تا ۱۹۲). عملاً state باید ≤ حدود **۳۰۰ توکن** باشد.
- خلاصه نمونه کاندیدا (۹ خط، همه بخش‌های §۷.۹.۱) = **۲۶۶ توکن** ← جا می‌شود.
- با state هشت برابر: `truncated: true`، `state_tokens_dropped: 1672`، `truncated_questions: ["decision"]`. Laya **بی‌صدا کوتاه می‌کند**؛ پس wrapper ما: (۱) قبل از ارسال با tokenizer همان checkpoint می‌شمارد و طبق ترتیب §۷.۹.۱ کوتاه می‌کند؛ (۲) اگر با این حال پاسخ `truncated: true` داشت ← `NO_TRADE` با `CONTEXT_OVERFLOW`.

**تأخیر روی CPU** (i9-13980HX، ۸ thread، ۵۰ فراخوانی، state ۲۶۶ توکن، ۴ سؤال):

| معیار | مقدار |
|---|---:|
| p50 | **1567 ms** |
| p95 | **1745 ms** |
| max | 1767 ms |
| فراخوانی اول (گرم‌نشده، ۵ سؤال) | 2023 ms |
| تیتر خبر (۳ سؤال choice، state کوتاه) | 226 ms |

- بسیار زیر timeout ۲۰ ثانیه‌ای `09` §۷.۹. زمان تقریباً خطی با تعداد سؤال‌هاست (هر سؤال یک forward جدا).
- **قطعی است:** یک ورودی دو بار ← احتمال‌های یکسان (برخلاف BoxAPI).

**نمونه تفسیر خبر** (تیتر ساختگی «Major exchange halts withdrawals after suspected hot wallet exploit»):
`event_type = security_incident (0.68؛ رتبه دوم exchange_wallex 0.29)`، `direction = negative (0.90)`، `severity = 3 (0.75)` ← طبق §۷.۸ مسدودکننده می‌شد. این فقط یک نمونه است، نه ارزیابی دقت.

**نکته برای طراحی:** در نمونه کاندیدا، `P(APPROVE)` فقط ۰٫۵۲ بود. آستانه‌های `τ_*` در config Experimental هستند و پیش از تکیه بر آن‌ها باید با داده Shadow (دفتر خلاف واقع) سنجیده شوند.

## V11 — BoxAPI (`https://ai.boxapi.ir/ai/v1/chat/completions`)

| مورد | نتیجه |
|---|---|
| مدل | `gpt-6-luna` (همان در پاسخ) |
| `temperature=0` | ❌ `400: Only the default (1) value is supported` |
| `max_tokens` | ❌ باید `max_completion_tokens` باشد |
| `response_format={"type":"json_object"}` | ✅ JSON معتبر |
| تأخیر | ۱٫۵ تا ۲٫۵ ثانیه (پرامپت کوتاه) |
| مصرف | ~۲۸ توکن ورودی، ~۳۰ خروجی (شامل ۷ توکن reasoning) |
| هزینه | **`usage.cost` در پاسخ** برمی‌گردد: حدود ۰٫۰۰۰۰۱۵ تا ۰٫۰۰۰۰۱۷ دلار برای هر فراخوانی آزمایشی |

- بودجه را می‌توان با جمع `usage.cost` واقعی کنترل کرد (دقیق‌تر از برآورد قیمت).
- نبود `temperature=0` با `09` §۷.۹ («temperature=0») تعارض دارد ← **Q-23**. پیشنهاد: پاسخ‌ها با schema اعتبارسنجی شوند، درخواست و پاسخ کامل در `judge_calls/` ذخیره شود، و تکرارپذیری واریانت D فقط از روی همین ثبت‌ها ادعا شود.
