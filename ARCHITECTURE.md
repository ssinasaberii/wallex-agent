# Architecture — Wallex Spot Trading Agent (v1.4)

This document answers the challenge brief's review questions: architecture and core logic, how AI and the
Wallex tools are used, buy/sell conditions, capital and risk management, how to re-run and evaluate, and the
results of the backtest and shadow test. How to run everything: [README.md](README.md). Every optimisation
experiment, with its numbers: [docs/experiments.md](docs/experiments.md) (Persian).

## 1. One-paragraph summary

A deterministic engine scans liquid Wallex spot TMN pairs every four hours. It classifies each coin's trend on
**daily** bars and looks for one of three entry setups on **4-hour** bars, computed on **dollar-adjusted prices**
(TMN price divided by that hour's USDT/TMN rate), so the toman's own moves do not create signals. Candidates are
ranked, checked against live order flow and **news** (shadow/live), and sent to an **AI entry judge (Laya, a
local 421M-parameter decision model)** that can only approve, reject or defer. A deterministic risk engine sizes
each position at 0.25 % risk including the full round-trip fee, and enforces caps (10 slots, 1 % total risk,
15 % per position, 100,000 TMN minimum) and a 3 % kill switch. Exits (stop, trailing stop, target, time, regime,
settlement) are deterministic and never involve the AI. Every candidate, rejected ones included, is logged with
its inputs and reasons.

## 2. How we got here (versions)

| version | what | why it changed |
|---|---|---|
| v1.2 | spec strategy: 4h regime, 1h triggers, fee-aware gate | with Wallex's real 0.30 % taker fee it never traded; without the gate it lost money |
| v1.3 | daily regime, 4h triggers, dollar-adjusted signals, trailing stop, gate off (ADR-028); news gate (ADR-029) | the only configuration positive in Train, Validation and Test of a 3-year snapshot |
| **v1.4** | exits: T1 target 3R, stop 3 x ATR, trailing 4 x ATR (ADR-030) | same return as v1.3 in 30-day windows, smaller worst window, profit factor 3.1 |

Selection was always: search on Train, choose on Validation, run Test once. The previous configuration is kept in
`config/v1.2_hourly.yaml`.

## 3. Pipeline

```text
[Universe]            allowed markets (challenge denylist), spot TMN, no stablecoins / tokenized RWAs,
                      top 25 by 24h TMN volume, spread <= 0.30 %, <= 2 % missing hours in 30 days
[BTC filter]          BTCUSDT daily regime: DOWNTREND or UNKNOWN -> no entries; HIGH_VOL -> half size
[News, market-wide]   systemic event -> no entries for 6 h; no feed read for 45 min -> no entries
[Coin regime, 1D]     UNKNOWN > HIGH_VOL > DOWNTREND > UPTREND > RANGE > GRAY, 2-bar hysteresis
[Entry trigger, 4h]   T1 pullback / T2 breakout (UPTREND), T3 mean reversion (RANGE), gray zone (T1/T3)
[Stop and target]     stop 3 x ATR (1.5 %..8 %), targets in R; full round-trip cost computed and logged
[Ranking]             RS vs BTC, ADX, relative volume -> top 3 per cycle
[Order flow]          shadow/live: order-book imbalance near mid (TFI logged only)
[News, per coin]      strong negative headline on this coin -> no entry for 12 h
[AI entry judge]      Laya: APPROVE / REJECT / DEFER, fail-closed
[Risk engine]         sizing, slots, caps, cash, final recheck -> BUY
[Broker]              BacktestSim | ShadowSim (walks the live order book) | Live (disabled)
[Position manager]    exits -> SELL (no AI)
[Decision log, reports]
```

**One decision function for every mode** (`engine/decision_cycle.py`): backtest and shadow differ only in the
market view, the broker and the judge, as the brief requires. Order-flow and news gates switch on with the
view's capabilities, not with `if mode == ...`. Stops, the kill switch and settlement are checked every hour;
entries and position updates happen when a 4-hour bar closes.

## 4. Buy rules (all numbers in `config/default.yaml`)

| step | rule |
|---|---|
| Coin regime (daily, closed bars, dollar-adjusted) | UPTREND: ADX >= 25, EMA20 > EMA50, EMA50 slope > 0, +DI > -DI. RANGE: ADX < 20 and abs(slope) < ATR. HIGH_VOL: ATR % in the top 10 % of the last 360 days |
| T1 pullback (4h) | UPTREND; in the last 3 bars the low touched EMA20 + 0.5 ATR and the lowest RSI was 40..50; now close > EMA20 on a green bar |
| T2 breakout (4h) | UPTREND; close above the previous 20-bar high and relative volume >= 1.5 |
| T3 mean reversion (4h) | RANGE; previous close below the lower Bollinger band, now back above it; RSI < 35 and rising |
| Gray zone | regime GRAY with a T1/T3 trigger; half size; the rules judge rejects it, Laya decides |
| Stop | 3 x ATR % of the 4h bar, at least 1.5 %; wider than 8 % -> no trade |
| Target | T1 3R; T2 none (trailing only); T3 the Bollinger middle band |
| Cost | buy fee + sell fee (0.30 % each) + slippage both sides + spread, included in the risk of every trade |
| News | no strong negative tier-1/2 headline about the coin in the last 6 h; no systemic event |
| AI judge | Laya must APPROVE |

## 5. Sell rules (deterministic, never via the AI)

| exit | rule |
|---|---|
| Protective stop | price <= stop (shadow: every 10-15 s against the best bid) |
| Break-even | T1/T2 at +1R: stop to entry x (1 + fee break-even) |
| Trailing stop | T2 always, T1 after +1R: stop = max(stop, highest high - 4 ATR); **the stop never moves down** |
| Target | T1 at 3R, T3 at the Bollinger middle band |
| Time | at most 7 days (T1/T2: min(2H, 168 h); T3: 168 h) |
| Regime | coin turns DOWNTREND / HIGH_VOL / UNKNOWN, or BTC turns DOWNTREND |
| Kill switch / settlement | -3 % from the equity peak: sell all and stop buying; 90 min before the challenge end no entries, 60 min before it sell everything (the account ends in TMN, as the brief requires) |

Staged selling (`exits.scale_out`) is implemented and allowed but off: it raised the win rate without raising the
return. Adding to positions is forbidden (Constitution Article 4); it exists only as a backtest experiment flag
that the shadow runner refuses.

## 6. Capital and risk management

| limit | value |
|---|---|
| risk per trade | 0.25 % of min(equity, 5 M TMN), including the full round-trip cost |
| total open risk | 1 % |
| position size | <= 15 % of capital, >= 100,000 TMN and >= the exchange minimum |
| slots | <= 10 coins (open or pending), reserved atomically before an order is sent |
| one position per coin | no adding, no averaging down |
| daily loss | 1.5 % in a rolling 24 h window -> no new entries |
| kill switch | 3 % drawdown from the peak |
| fail closed | missing or stale data, judge timeout or error, news feeds silent, unknown order state -> no new entry |

Higher risk was tested (0.5 %, 1 %, 2 % per trade): it reaches the 3 % kill switch sooner and did worse on Test
(`docs/experiments.md` §3).

## 7. How AI is used

**Laya** (ConvAI Innovations, Apache 2.0, 421M parameters, `laya==0.4.13`, weights pinned to a revision) runs
locally on CPU behind a Jev-compatible API. It returns calibrated probabilities for typed questions, it does not
generate text, and the same input always gives the same answer. It has two jobs:

1. **Entry judge.** For each candidate it gets a short English summary (<= 300 tokens: setup, regimes, key
   indicators, stop/target/cost, order flow, related headlines, Fear & Greed, portfolio) and answers: decision
   (APPROVE/REJECT/DEFER), do the signals conflict, is there news risk, is data missing, and for gray-zone
   candidates whether the evidence is strong enough for half size. Code combines the answers with thresholds from
   config. CPU latency p50 1.6 s, p95 1.7 s.
2. **News reader.** For every English headline: event type, direction, severity, and whether it threatens the
   whole market.

Laya cannot create opportunities, choose markets, size positions, move stops or delay exits (Constitution
Art. 3). Every request and response is stored in `judge_calls/<candidate_id>.json`.

The backtest uses the rules-only judge (an AI judge cannot be backtested honestly: a model may know the past).
The AI's value is measured in the shadow test on the same candidates: **A** rules, **B** Laya, **C** Laya + news.

## 8. News (shadow/live)

- 12 reachable English feeds, ranked: tier 1 CoinDesk, The Block, Blockworks, Decrypt; tier 2 Cointelegraph,
  The Defiant, CryptoSlate, crypto.news; tier 3 BeInCrypto, NewsBTC, U.Today, The Daily Hodl (context only).
- Polled every 5 minutes, de-duplicated by URL and normalised title, coins matched by name and ticker.
- **Coin block:** negative, severity 3, tier 1-2, younger than 6 h -> no entry on that coin for 12 h.
- **Systemic block (EVENT_SHOCK):** negative, tier 1-2, and Laya >= 0.8 with severity 3, or Laya >= 0.4 plus a
  systemic keyword (de-peg, withdrawals halted, exchange hack, ban, chain halt) -> no entries for 6 h. The rule was
  set by probing Laya on real and made-up headlines: alone, it rated a Tether de-peg 0.44 and a Lightning bug 0.68.
- "Unclear" or positive news never blocks; positive news never creates a trade.
- Headline text is untrusted data: it is only classified and shown as text.

News history is not available for free, so news cannot be backtested; its effect is measured as variant C vs B.

## 9. Data integrity

- Only closed bars are used; the API returns the forming bar and it is dropped. 4h and daily bars are built from
  1h bars on UTC boundaries and are invalid if any hour is missing.
- No look-ahead: indicators and full decisions at 50 random moments are identical on data cut at t and on the
  full data, for 1h and 4h triggers (tests). Fills happen at the next bar's open; stop before target inside a bar.
- Backtests read a hashed snapshot; the same snapshot and config give byte-identical trades.

## 10. Results

### 10.1 Backtest (`reports/backtest/20261022T224409Z/`)

Snapshot `ecbb7bb14b3ec827`: 42 symbols, 3 years of 1h candles. After one year of warm-up (the daily regime needs
360 bars): Train 2024-10-23 .. 2026-01-03, Validation 2026-01-03 .. 2026-05-28, Test 2026-05-28 .. 2026-10-22.
Main measure: 30-day windows, each starting with 5,000,000 TMN, two start offsets.

| period (30-day windows) | mean return | median | worst window | profitable windows | trades |
|---|---:|---:|---:|---:|---:|
| Validation | +0.35 % | +0.06 % | -0.17 % | 50 % | 32 |
| **Test (run once)** | **+1.10 %** | +0.08 % | -0.16 % | 50 % | 74 |

Ablations on Validation: switching the fee gate back on gives no trades; computing signals on raw TMN prices
instead of dollar-adjusted ones turns the result negative (-0.27 % vs +0.35 %); removing T1 turns it negative.

**One continuous account (Validation + Test, no monthly reset): -0.73 %.** A run of six losing trades in May took
the account 3 % below its peak on 3 August 2026; the kill switch then stopped it for the rest of the period, so it
missed the August–October gains. (v1.3 in the same run stayed 2.81 % below its peak and ended +4.2 %.) The 3 %
kill switch is a team rule designed for the 72-hour challenge; how it should reset in longer operation is an open
team decision.

**Benchmarks over the same Test windows (median):** holding BTCTMN +20.2 %, holding USDT +8.3 %, cash 0 %. The
toman fell sharply (USDT/TMN rose ~40 % between July and October 2026), so anything held in TMN terms gained;
the strategy invests a small part of the capital at a time (0.25 % risk per trade) and captures little of that.

### 10.2 Shadow test (`reports/shadow/challenge-v14/`)

Live Wallex data, simulated orders filled against the real order book, three virtual accounts on the same
candidates (A rules, B Laya, C Laya + news), decisions every 4 hours until the challenge end
(2026-10-23 17:30 UTC). `summary.md` is rewritten every hour with equity, trades, the decision funnel, judge
statistics, news items that would block, uptime and API errors. Earlier sessions (`challenge`, `challenge-v13`)
are kept as records.

### 10.3 Decision dashboard

Every opportunity of the backtest, with the reason it was taken or rejected (in Persian), every trade and the
decision funnel: the team's dashboard "Wallex Agent Decisions" (built by `scripts/export_dashboard.py`).

## 11. Limitations

- With real fees the edge is small: a few tenths of a percent to about 1 % per month in 30-day windows, far below
  simply holding BTC or USDT in this period of toman devaluation.
- The 3 % kill switch makes long continuous runs path-dependent (§10.1).
- Order flow and news cannot be evaluated historically; their value is measured only in the shadow test.
- No historical spread: backtests use the spread in the snapshot's market metadata.
- Survivorship bias: the universe is today's pair list and today's allowed-markets list.
- Fees are modelled in TMN on both sides; Wallex may charge the buy fee in the base asset.
- `/v1/account/fee` rejected the provided key; fees are the Wallex level-1 schedule confirmed by the team.
- `isBuyOrder` semantics are unverified; TFI is logged only.
- Not done: random-entry benchmark, historical news backtest, a parameter search on the gray-zone thresholds.
