# PLAN_LIVE — ثبت سفارش واقعی (مرحله ۲-ب HANDOFF)

> وضعیت (2026-10-23): **کاربر Live را تأیید کرد (ADR-033).** `live.dry_run: false` در config. قفل دوم (`.env`) و راه‌اندازی
> با خود کاربر است (§۴). پایش: `scripts/live_status.py` ← `reports/shadow/live-1/status.html`.
> کلید API حساب مسابقه کار می‌کند: زیرحساب «هکاتون استارکوچ | والکس» (prop)، ۵٬۰۰۰٬۰۰۰ تومان آزاد (Q-38).
> مسیرها از مستندات رسمی `developers.wallex.ir/docs` (`docs/api-verification.md` V7/V8).

## ۱. معماری

```text
DecisionCycle (بدون تغییر، همان بک‌تست و سایه)
   └─ Order (از Portfolio با رزرو اتمیک)
        └─ LiveBroker  (execution/live.py)                 ← زیرکلاس ShadowSimBroker: همان حد ضرر/هدف/متحرک
             └─ WallexOrders (data/wallex_orders.py)       ← تنها جایی که POST/DELETE به والکس مجاز است
```

- `data/wallex_orders.py`: allowlist دقیق: `POST /v1/account/orders`، `DELETE /v1/account/orders/{client_id}`،
  `GET /v1/account/orders/{client_id}`، `GET /v1/account/openOrders`، `GET /v1/account/balances`. هر مسیر برداشت، OTC،
  انتقال، کارت، شبا، تعهدی و زیرحساب پیش از ساختن درخواست رد می‌شود. هدرها `x-api-key` و `sub-account-client-id` فقط از
  `.env` و در لاگ‌ها پوشانده می‌شوند. فاصله حداقل ۰٫۶ ثانیه بین درخواست‌ها (سقف والکس ۲۰ سفارش در ۱۰ ثانیه).
- حساب زنده = واریانت **LC** در `shadow/runner.py` (Laya + خبر، همان الگوریتم واریانت C):
  `scripts\run_shadow_forever.ps1 -Session live-1 -Variants LC`. موجودی شروع = موجودی آزاد واقعی تومان.
  `data/wallex_rest.py` فقط-GET می‌ماند. تست سایه A/B/C جدا ادامه دارد (`challenge-v15`).

## ۲. چرخه سفارش (`05` §13)

| مرحله | رفتار |
|---|---|
| ارسال | LIMIT قابل‌اجرا: خرید = بهترین Ask + ۰٫۳٪، فروش = بهترین Bid − ۰٫۳٪ (`live.limit_offset_pct`)؛ هرگز MARKET |
| شناسه | قطعی: `B_<candidate>` و `S_<candidate>_<t>`؛ ارسال دوباره پس از لغو: `..._r<n>` |
| پاسخ نامعلوم (timeout، 5xx) | `UNKNOWN`؛ **ارسال دوباره نمی‌شود**؛ با شناسه پیگیری می‌شود؛ رزرو نگه داشته و خرید جدید متوقف می‌شود؛ اگر ۳ بار پیدا نشد، رسیده‌نشده حساب می‌شود |
| پر شدن | فقط fill واقعی پوزیشن و سود/زیان می‌سازد؛ کارمزد خرید که به ارز پایه کسر شود مقدار نگه‌داشته را کم می‌کند |
| خرید پرنشده | بعد از `live.order_ttl_s` (۶۰ ثانیه) لغو؛ پر شدن جزئی = پوزیشن کوچک‌تر |
| فروش | فقط تا موجودی آزاد کیف پول؛ یک سفارش خروج برای هر پوزیشن؛ پرنشده یا جزئی ← لغو و ارسال دوباره با قیمت تازه |
| حد ضرر | سمت ربات، هر ۱۰ ثانیه با Bid. STOP سمت صرافی استفاده نمی‌شود |

## ۳. چک‌لیست ایمنی

- [x] کلاینت سفارش فقط مسیرهای allowlist (`tests/test_live.py`)؛ `wallex_rest` بدون POST/DELETE (`tests/test_safety.py`).
- [x] بک‌تست هرگز LiveBroker نمی‌سازد (`tests/test_no_live_and_trailing.py`).
- [x] mock سرور: `UNKNOWN` بدون ارسال دوباره، کارمزد به ارز پایه، لغو پس از مهلت، فروش تا موجودی آزاد، یک خروج.
- [x] تطبیق موجودی کیف پول با پوزیشن‌ها هر ۵ دقیقه؛ اختلاف = توقف خرید جدید. سفارش باز از اجرای قبلی در شروع = توقف خرید.
- [x] Kill switch عملیاتی: سفارش نامعلوم، اختلاف موجودی، ۳ خطای پشت‌سرهم ← خرید جدید متوقف (فروش‌ها ادامه دارند).
- [x] `live.dry_run: true` پیش‌فرض؛ ارسال واقعی **دو قفل** دارد: `live.dry_run: false` در config **و**
      `WALLEX_LIVE_APPROVAL=I-APPROVE-LIVE-ORDERS` در `.env` که فقط کاربر می‌گذارد.
- [x] سقف‌ها: هر خرید ≤ ۱۲۰٬۰۰۰ تومان (`live.max_order_tmn`)، مجموع پوزیشن‌های باز ≤ ۲۵۰٬۰۰۰ تومان (`live.max_notional_tmn`).
- [x] کلیدها فقط از `.env` و `secrets.RedactingFilter`.
- [x] تسویه (موتور تصمیم): از ۱۹:۰۰ UTC دوشنبه خرید جدید نه، از ۱۹:۳۰ فروش همه، در ۲۰:۳۰ فروش باقی‌مانده.
- [x] اجرای آزمایشی با بازار زنده: یک چرخه (`live-dry-1`) و قالب سفارش با مشخصات واقعی BTC/ETH (قیمت و مقدار درست، سقف رعایت شد).
- [x] اجرای آزمایشی پیوسته `live-dry-1` (۱۰:۳۸ تا ۱۲:۴۰ UTC): بدون خطای سفارش؛ تطبیق موجودی هر ۵ دقیقه سالم.
- [x] **تأیید نهایی کاربر** (2026-10-23، ADR-033).
- [x] پایش زنده: `live_status_LC.json` هر ۳۰ ثانیه + `scripts/live_status.py` (صفحه فارسی، موجودی واقعی کیف پول).

## ۴. روشن کردن Live

1. در `.env`: `WALLEX_LIVE_APPROVAL=I-APPROVE-LIVE-ORDERS` (کاربر خودش می‌گذارد).
2. در `config/default.yaml`: `live.dry_run: false` (انجام شد).
3. توقف `live-dry-1` (انجام شد) و اجرای `scripts\run_shadow_forever.ps1 -Session live-1 -Variants LC` (کاربر).
4. پایش: `python -I scripts/live_status.py --session live-1 --watch` و باز کردن `reports/shadow/live-1/status.html`.
5. توقف اضطراری: بستن پروسه‌های `run_shadow_forever` و `python -m wallex_agent` با `live-1`؛ سفارش باز را در پنل والکس لغو کنید.
6. گزارش بعد از اولین معامله؛ افزایش سقف‌ها فقط با تأیید کاربر.
