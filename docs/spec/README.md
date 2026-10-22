---
title: "Wallex Spot Trading Agent — Specifications v1.1"
version: "1.1"
status: "Specification only; implementation not started"
language: "fa-IR"
---

# Wallex Spot Trading Agent — Spec-Driven Documentation v1.1

این بسته شامل **۹ سند طراحی و قانون‌گذاری معماری** است؛ نه کد اجرایی و نه تأیید معاملات زنده. تغییرات جدید بر اساس درخواست کاربر در تاریخ ۲۰۲۶-۱۰-۲۲ اعمال شده‌اند.

## Architecture Freeze

- **Trading venue:** فقط **Spot**؛ فیوچرز، شورت، تعهدی و فروش بدون موجودی ممنوع.
- **Trading direction:** BUY برای تملک، **SELL فقط با ثبت و پیگیری سفارش اسپات** برای کاهش/خروج/تسویه؛ مسیر OTC مجاز نیست.
- **Strategy:** Fee-Aware Short-Horizon Momentum + Order Flow Confirmation.
- **Jev:** فقط Entry Confirmation با `APPROVE / REJECT / DEFER`؛ هیچ اختیار مالی، سفارش یا خروج ندارد.
- **Buy capacity:** حداکثر **۱۰ جایگاه خرید**، با احتساب سفارش BUY_PENDING و موقعیت فعال دارایی پایه.
- **Minimum buy:** **۱۰۰٬۰۰۰ تومان** یا حداقل رسمی بیشتر بازار؛ سقف ۱۰ الزام انجام ۱۰ معامله نیست.
- **Position adding:** ممنوع در MVP.
- **Conservative risk:** ۰٫۲۵٪ هر معامله، ۱٪ ریسک باز کل، ۱۵٪ ارزش هر موقعیت، ۱٫۵٪ حد زیان ۲۴ساعته، ۳٪ افت محافظتی.
- **Time windows:** افق نگهداری و پنجره‌های تحلیل از هزینه واقعی Maker/Taker حساب و بازار + اسپرد + لغزش + حرکت محافظه‌کارانه قابل‌برآورد تعیین شوند؛ بدون فرض نرخ کارمزد ثابت.
- **Caveman:** Engine/Proxy اختیاری برای فشرده‌سازی متنی؛ داده عددی و اطلاعات سفارش دست‌نخورده بمانند.
- **Exits:** فقط با سفارش SELL اسپات و بر اساس موجودی واقعی؛ مستقل از Jev.

## Documents

1. `00-mission.md` — مأموریت و مرزهای اسپات.
2. `01-constitution.md` — قوانین الزام‌آور و قواعد ده جایگاه/حداقل خرید/فروش.
3. `02-signal-catalog.md` — مومنتوم و Order Flow با پنجره متناسب با هزینه.
4. `03-jev-decision-engine.md` — فقط Entry Gate.
5. `04-caveman-and-data.md` — داده، Fee Snapshot و Context.
6. `05-risk-and-lifecycle.md` — حدود ریسک، ۱۰ جایگاه، سفارش BUY/SELL و تسویه.
7. `06-validation-and-roadmap.md` — ترتیب قابلیت‌ها و معیارهای پذیرش **معماری**.
8. `07-decisions-and-sources.md` — ADRها، مسائل باز و مراجع.
9. `08-fee-aware-time-windows.md` — فرمول‌ها و سیاست افق اقتصادی پس از هزینه.
10. `MANIFEST.md` — فهرست فایل‌ها و هش آن‌ها.

## Read order for Coding Agent

`01-constitution.md` → `07-decisions-and-sources.md` → `08-fee-aware-time-windows.md` → سند قابلیت موردنظر → برنامه طراحی/اجرا.

## Important distinctions

- مبلغ ۱۰۰٬۰۰۰ تومان **کف تعیین‌شده توسط تیم** است؛ در هر بازار کف رسمی بیشتر باید اعمال شود.
- «معامله اسپات با حساب اختصاصی اعتبار مسابقه» به معنی مجازبودن مارجین/فیوچرز نیست.
- «فروش با سفارش» به معنی فروش انجام‌شده نیست؛ باید Fill واقعی پیگیری شود.
- نرخ کارمزد، محدودیت سفارش و دسترسی حساب مسابقه تا زمانی که با API تأیید نشده‌اند، اطلاعات حل‌نشده‌اند.
- این سند مدعی تعیین زمان سودآور صرفاً از کارمزد نیست.

**Implementation status:** Not started.  
**Live status:** Not authorized pending account rules, fees, allowed symbols and actual order capabilities.
