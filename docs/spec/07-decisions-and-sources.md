---
title: "Architecture Decision Register"
version: "1.1"
status: "Four core decisions approved"
language: "fa-IR"
---

# Architecture Decision Register

**Version:** 1.1  
**Status:** Four core decisions approved  
**Purpose:** Single source of architectural decisions

## 1. تصمیم‌های مصوب

### ADR-001 — Spec-Driven Development

**Status:** Accepted

پروژه با Constitution، Feature Specifications، Implementation Plan، Implementation و Validation توسعه می‌یابد.

### ADR-002 — Jev as Entry Confirmation Gate

**Status:** Accepted

Jev فقط فرصت‌های تولیدشده توسط الگوریتم را تأیید، رد یا تعویق می‌کند. Jev بازار انتخاب نمی‌کند، فرصت مستقل نمی‌سازد، اندازه موقعیت تعیین نمی‌کند و خروج اضطراری را مدیریت نمی‌کند.

**Reason:** محدودکردن دامنه قضاوت مدل، کاهش ابهام و امکان اندازه‌گیری ارزش افزوده.

### ADR-003 — Momentum with Order Flow

**Status:** Accepted

استراتژی اصلی: Short-Horizon Momentum with Order Flow Confirmation.

سیگنال‌های اصلی:

- OBI
- TFI
- Price Momentum

سیگنال‌های مکمل:

- Trend
- Relative Volume

**Reason:** تمرکز بر اطلاعات کوتاه‌مدت قابل‌اندازه‌گیری و جلوگیری از پراکندگی استراتژی.

### ADR-004 — No Position Adding

**Status:** Accepted

افزایش موقعیت در MVP ممنوع است.

**Reason:** محدودکردن ریسک و پیچیدگی مدیریت سفارش و موقعیت؛ اجازه کاهش حجم یا فروش اسپات باقی است.

### ADR-005 — Conservative Risk

**Status:** Accepted

حدود طراحی:

- ۰٫۲۵٪ ریسک هر معامله
- ۱٪ ریسک باز
- ۱۵٪ سقف ارزش هر موقعیت
- **۱۰ جایگاه خرید هم‌زمان** با احتساب رزرو سفارش‌های BUY_PENDING
- ۱٫۵٪ حد زیان ۲۴ساعته
- ۳٪ توقف محافظتی کل دوره

**Reason:** اولویت حفظ سرمایه در یک مسابقه کوتاه‌مدت. تعداد جایگاه‌ها مستقل از سقف ریسک تجمیعی است. حداقل ارزش هر خرید ۱۰۰٬۰۰۰ تومان یا کف رسمی بیشتر است.

### ADR-006 — Deterministic Exits

**Status:** Accepted

حد ضرر، کاهش حجم، حفاظت از سود، خروج و تسویه مستقل از Jev انجام می‌شوند.

### ADR-007 — Caveman as Optional Optimization

**Status:** Accepted

Caveman Engine / Proxy برای فشرده‌سازی انتخابی Context متنی استفاده می‌شود، مشروط به اثبات سازگاری و منفعت.

### ADR-008 — News and Macro as Gates

**Status:** Accepted

اخبار و رویدادهای Macro در MVP عمدتاً برای کاهش ریسک ورود استفاده می‌شوند.

### ADR-009 — Derivatives Features Removed from Current Scope

**Status:** Accepted

سیستم تنها از بازار و سفارش‌های اسپات استفاده می‌کند. استراتژی معاملات فیوچرز، شورت و تعهدی و سیگنال وابسته به اجرای آن‌ها در اسناد جاری جایی ندارد.

### ADR-010 — Fail Closed

**Status:** Accepted

نبود داده یا پاسخ معتبر، مجوز ورود نیست.

## 2. پارامترهای اولیه که هنوز باید اعتبارسنجی شوند

| مورد | مقدار اولیه | وضعیت |
|---|---|---|
| وزن OBI | ۳۰ | Experimental |
| وزن TFI | ۳۰ | Experimental |
| وزن Momentum | ۲۰ | Experimental |
| وزن Trend | ۱۰ | Experimental |
| وزن Volume | ۱۰ | Experimental |
| آستانه بررسی ورود | ۷۰ از ۱۰۰ | Experimental |
| EMA | ۹ و ۲۱ | Experimental |
| پنجره TFI | تطبیقی با افق اقتصادی، نرخ معاملات و کارمزد | Architecture rule |
| ATR Stop | ATR(14) و ضریب اولیه ۲ | Experimental |
| تازگی داده کوتاه‌مدت | تابع افق، تأخیر و فرکانس داده | Architecture rule |
| حداکثر بازار بررسی عمیق در هر چرخه | ۳؛ مستقل از سقف ۱۰ موقعیت خرید | Experimental |
| حداقل خرید | ۱۰۰٬۰۰۰ تومان یا حداقل رسمی بیشتر | Approved |
| سقف جایگاه خرید | ۱۰ | Approved |
| سیاست فروش | سفارش `SELL` اسپات، LIMIT پیش‌فرض | Approved |
| افق نگهداری | براساس هزینه رفت‌وبرگشت واقعی و حرکت قابل‌برآورد | Approved method |

این مقادیر بخشی از تنظیمات اولیه v1.0 هستند، اما به‌عنوان پارامترهای سودآور تأیید نشده‌اند.

## 3. موارد مسدودکننده اجرای واقعی

### BLOCK-001 — Competition Credit Account

باید تأیید شود:

- نوع حساب
- روش ثبت سفارش
- هزینه اعتبار
- آستانه هشدار
- آستانه لیکوییدیشن
- قواعد تسویه

### BLOCK-002 — Allowed Markets

باید فهرست کامل بازارهای مجاز از حساب واقعی تأیید شود.

### BLOCK-003 — Trade Direction

معنای سمت آغازکننده معاملات در API والکس باید مشخص شود.

### BLOCK-004 — Trading Costs

کارمزد زنده Maker/Taker برای **هر بازار و همین حساب**، اسپرد، لغزش و هزینه احتمالی حساب اختصاصی باید قابل محاسبه باشند. مقدار کارمزد عمومی یا نمونه API به‌عنوان نرخ ثابت حساب مسابقه پذیرفته نیست.

### BLOCK-005 — Jev Access

باید تأیید شود:

- API قابل دسترسی است.
- نسخه مدل مشخص است.
- قرارداد پاسخ معتبر است.
- تأخیر قابل‌قبول است.
- محدودیت درخواست مشخص است.

### BLOCK-006 — Caveman Compatibility

باید مشخص شود آیا مسیر Caveman Engine / Proxy با روش فراخوانی Jev سازگار است.

### BLOCK-007 — Competition Timing

زمان دقیق شروع، پایان و منطقه زمانی مسابقه باید مشخص شود.

### BLOCK-008 — Live Risk Basis

مبنای واقعی محاسبه سرمایه و تعهدات حساب اعتباری باید تأیید شود.

## 4. مواردی که تغییرشان نسخه جدید می‌خواهد

- تغییر نقش Jev
- تغییر استراتژی اصلی
- فعال‌سازی افزایش موقعیت
- افزایش سقف ریسک
- تغییر سقف ده جایگاه یا حداقل خرید ۱۰۰٬۰۰۰ تومان
- تغییر سیاست خروج فقط با سفارش SELL اسپات
- تغییر سیاست انتخاب پنجره کارمزد-محور
- تغییر سیاست خروج
- تغییر سیاست تسویه
- تغییر نقش Caveman از اختیاری به اجباری

## 5. منابع طراحی

### Spec-Driven Development

DeepLearning.AI:

https://www.deeplearning.ai/courses/spec-driven-development-with-coding-agents

### Jev / TypeSafe AI

https://docs.typesafe.ai/introduction

https://docs.typesafe.ai/concepts/state

https://docs.typesafe.ai/primitives/choice

https://docs.typesafe.ai/confidence

### Caveman

https://github.com/JuliusBrussee/caveman/blob/main/engine/README.md

### Wallex

https://api-docs.wallex.ir/

### منابع خارجی تکمیلی

- Wallex Spot Markets: https://developers.wallex.ir/spot/activemarket
- Wallex Spot Order Creation: https://developers.wallex.ir/spot/createorder
- Wallex Order Detail: https://developers.wallex.ir/spot/singleorderdetail
- Wallex Active Orders: https://developers.wallex.ir/spot/activeorders
- Wallex API Reference including account fee: https://api-docs.wallex.ir/

**توجه:** نرخ واقعی کارمزد و حداقل سفارش حساب مسابقه از داده معتبر همان حساب خوانده می‌شوند، نه از یک نرخ ثابت در اسناد.

## 6. Change Log

### v0.1

- معماری اولیه چندلایه
- پیشنهاد نقش‌های گسترده‌تر برای Jev
- معرفی چند گزینه استراتژی
- حدود ریسک پیشنهادی
- Caveman اختیاری

### v1.0

- تثبیت Jev فقط به‌عنوان Entry Confirmation Gate
- تثبیت Momentum + Order Flow
- ممنوعیت افزایش موقعیت در MVP
- تصویب سیاست محافظه‌کارانه ریسک
- حذف Jev از مسیر خروج
- تفکیک Candidate Generation از AI Judgment
- تفکیک تأیید AI از مجوز قطعی سفارش
- تعریف Baseline و Ablation متناسب با نقش جدید Jev

## 7. قانون اجرای Spec

Coding Agent موظف است:

1. Constitution v1.0 را بخواند.
2. ADRهای مرتبط را بررسی کند.
3. Feature Spec را بخواند.
4. موارد BLOCK را بررسی کند.
5. پیش از کدنویسی برنامه ارائه کند.
6. تغییر معماری را بدون اصلاح Spec انجام ندهد.
7. برای هر قابلیت شواهد تست ارائه دهد.

### v1.1

- فقط اسپات؛ عدم امکان فیوچرز، شورت، معامله تعهدی و فروش دارایی ناموجود.
- افزایش سقف موقعیت‌های خرید از ۲ به ۱۰ با رزرو BUY_PENDING.
- حداقل ارزش خرید ۱۰۰٬۰۰۰ تومان همراه حداقل بالاتر هر بازار.
- خروج صرفاً با ارسال و پیگیری سفارش فروش اسپات.
- انتخاب افق نگهداری و پنجره‌های بررسی از گیت کارمزد واقعی و هزینه اجرای رفت‌وبرگشت.
- حذف مراحل ارزیابی معاملاتی دوره‌های بعد از این مجموعه اسناد.
- اضافه شدن `08-fee-aware-time-windows.md`.

## 8. وضعیت نهایی

**Architecture decisions: Approved**

**Implementation: Not started**

**Live trading: Not authorized**

**External API and account validation: Pending**

## 9. ADRهای الحاقی نسخه 1.1

### ADR-011 — Spot Only

**Accepted:** فقط سفارش‌های اسپات برای خرید و فروش. اعتبار حساب هکاتون به‌تنهایی مجوز معاملات اهرمی/تعهدی نیست.

### ADR-012 — Ten Buy Slots

**Accepted:** حداکثر ۱۰ دارایی پایه متمایز با موقعیت باز یا سفارش خرید رزروشده؛ بدون الزام به پرکردن تمام جایگاه‌ها.

### ADR-013 — Minimum Buy 100,000 TMN

**Accepted:** کف طراحی تیم برای هر خرید اولیه؛ حداقل رسمی بیشتر والکس مقدم است.

### ADR-014 — Explicit Spot Sell Orders

**Accepted:** فروش/تسویه تنها از مسیر سفارش `SELL` اسپات روی موجودی آزاد. LIMIT پیش‌فرض و MARKET تنها با تأیید مجوز و کنترل لغزش.

### ADR-015 — Fee-Aware Time Windows

**Accepted:** پنجره‌ها پس از انتخاب افق اقتصادی متناسب با کارمزد واقعی رفت‌وبرگشت، اسپرد، لغزش، دامنه حرکت قابل برآورد و زمان باقی‌مانده تنظیم می‌شوند؛ کارمزد به‌تنهایی زمان سودده را تعیین نمی‌کند.

### ADR-016 — Current Document Scope

**Accepted:** مستندات فعلی فقط معماری، منطق، سیاست‌های تصمیم و معیارهای پذیرش طراحی را پوشش می‌دهند.

## 10. ADRهای v1.2

مرجع کامل: `09-v1.2-merged-technical-and-testing.md`. هر جا این بخش با ADRهای قبلی تعارض دارد، این بخش مقدم است.

| ADR | وضعیت | تصمیم | چه چیزی را تغییر می‌دهد |
|---|---|---|---|
| ADR-017 | Accepted (v1.2) | موتور تکنیکال **رژیم‌محور با سه Setup** (پولبک روند، شکست، بازگشت به میانگین) جایگزین «مومنتوم کوتاه‌مدت به‌عنوان تنها استراتژی» | Article 2، DEC-02، ADR-003 |
| ADR-018 | Accepted (v1.2) | OBI و TFI از «سیگنال اصلی امتیازدار» به **گیت تأیید ورود در Shadow/Live** تبدیل می‌شوند، چون داده تاریخی دفتر سفارش وجود ندارد | S01/S02 و وزن‌های ۳۰/۳۰ در `02` |
| ADR-019 | Accepted (v1.2) | تایم‌فریم پیش‌فرض: رژیم روی ۴h، ماشه روی ۱h؛ همچنان مشروط به گیت `08` و مقایسه در Backtest | تکمیل `08` |
| ADR-020 | Accepted (v1.2) | روند با EMA20/EMA50 + ADX(14) + ‎+DI/‎−DI روی تایم رژیم؛ EMA9/21 حذف | S04 |
| ADR-021 | Accepted (v1.2) | امتیاز رتبه‌بندی فقط از داده کندلی (قابل بک‌تست) | `02` §4 |
| ADR-022 | Accepted (v1.2) | فیلتر کل بازار با رژیم بیت‌کوین روی `BTCUSDT` | S08 |
| ADR-023 | Accepted (v1.2) | فقط منابع داده رایگان؛ CryptoPanic حذف | `04` §3 |
| ADR-024 | Accepted (v1.2) | کاندیداهای «ناحیه خاکستری» هم به Judge فرستاده می‌شوند (سقف اندازه ۵۰٪)؛ نقش Judge فقط `APPROVE/REJECT/DEFER` | تکمیل `03` |
| ADR-025 | Accepted (v1.2) | Backtest و Shadow Test دوباره در محدوده و الزامی‌اند | `00` §10، Article 17، `06` §14، ADR-016 |
| ADR-026 | Accepted (2026-10-22، تیم) | **Laya جایگزین Jev به‌عنوان Entry Judge** | ADR-002، `03`، BLOCK-005، `09` §۷.۸ و §۷.۹ |
| ADR-027 | Accepted (2026-10-22، تیم) | **LLMJudge از طریق BoxAPI** (سازگار با OpenAI، مدل `gpt-6-luna`) فقط به‌عنوان واریانت مقایسه‌ای در Shadow | `09` §۷.۹ و §۱۰.۲ |

### ADR-026 — Laya as Entry Judge

**Status:** Accepted (تصمیم تیم، 2026-10-22)

Jev (TypeSafe AI) پولی است و استفاده نمی‌شود. به‌جای آن **Laya** استفاده می‌شود: مدل تصمیم متن‌باز ConvAI Innovations (Apache 2.0، حدود ۴۲۱M پارامتر، API سازگار با Jev). بسته `laya` در PyPI موجود است (آخرین نسخه دیده‌شده: 0.4.13).

- **اجرا:** محلی با `pip install laya` و `laya-serve` (endpoint سازگار با Jev: `POST /v1/systemone`). اولویت با اجرای محلی است: بدون کلید، تکرارپذیر برای داور و مستقل از تحریم. مسیر جایگزین (Vercel AI Gateway، مدل `convaiinnovations/laya-free`) فقط با تأیید صریح تیم.
- **محدودیت‌ها:** Context حداکثر ۵۱۲ توکن، فقط انگلیسی؛ متن تولید نمی‌کند و فقط به سؤال‌های تایپ‌دار (boolean / choice / score) احتمال می‌دهد.
- **نقش بدون تغییر:** فقط گیت ورود (`APPROVE/REJECT/DEFER`)؛ هیچ اختیاری در خروج، اندازه یا ریسک. قرارداد `JudgeDecision` و قواعد Fail Closed بدون تغییرند. Confidence هیچ اثری بر اندازه پوزیشن ندارد.
- **تفسیر خبر:** `event_type`، `direction` و `severity` هر کدام یک سؤال choice روی تیتر انگلیسی.
- BLOCK-005 برای Laya با اعتبارسنجی Phase 0 (نسخه مدل، تأخیر p50/p95 روی CPU، شکل ورودی/خروجی) جایگزین می‌شود.

**Reason:** حذف هزینه و وابستگی به کلید، تکرارپذیری محلی برای داوران، و حفظ نقش محدود Judge طبق `03` §6 (Choice + Yes/No).

### ADR-027 — BoxAPI LLMJudge as Shadow Comparison Variant

**Status:** Accepted (تصمیم تیم، 2026-10-22)

`LLMJudge` با provider = BoxAPI (`https://ai.boxapi.ir/ai/v1`، سازگار با OpenAI Chat Completions، مدل پیش‌فرض `gpt-6-luna`) پیاده می‌شود، **فقط** به‌عنوان یک واریانت جداگانه در Shadow برای مقایسه با Laya. در Backtest استفاده نمی‌شود (LLM ممکن است گذشته را بداند).

- قرارداد خروجی، temperature=0، اعتبارسنجی JSON schema و Fail Closed همان `09` §۷.۹.
- بودجه اعتبار محدود است (حدود ۳ دلار)؛ سقف هزینه در config. با عبور از سقف، این واریانت فقط `NO_TRADE` تولید می‌کند و بقیه سیستم بی‌تأثیر می‌ماند.
- فقط خلاصه کاندیدا ارسال می‌شود؛ هیچ کلید، موجودی یا شناسه حساب به provider نمی‌رود.
- Provider‌های Anthropic / OpenAI / Gemini به‌صورت interface باقی می‌مانند و پیاده‌سازی نمی‌شوند.
