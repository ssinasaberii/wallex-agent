# Architecture — Wallex Spot Trading Agent (v1.2)

This document answers the challenge brief's review questions: architecture and core logic, how AI and the
Wallex tools are used, buy/sell conditions, capital and risk management, how to re-run and evaluate, and
the results of the backtest and shadow test. How to run everything: [README.md](README.md).

## 1. One-paragraph summary

A deterministic engine scans liquid Wallex spot TMN pairs every hour. It classifies the market regime on
4h bars and looks for one of three setups on 1h bars. A **fee-aware gate** rejects any trade whose expected
move cannot pay Wallex's real round-trip cost. Surviving candidates are ranked, checked against live order
flow (shadow) and sent to an **AI entry judge (Laya, a local 421M-parameter decision model)**, which can
only APPROVE, REJECT or DEFER. A deterministic risk engine sizes the position (0.25 % risk), enforces caps
(10 slots, 1 % total risk, 15 % per position, 100,000 TMN minimum) and a kill switch. Exits (stops,
trailing, targets, time, regime, settlement) are deterministic and never involve the AI. Every candidate,
including rejected ones, is logged with its inputs and reasons.

## 2. Pipeline

```text
[Universe & liquidity]   allowed markets (challenge denylist), spot TMN, no stablecoins / tokenized RWAs,
                         top 25 by 24h TMN volume, spread <= 0.30 %, <= 2 % missing hours in 30 days
[BTC market filter]      BTCUSDT 4h regime: DOWNTREND or UNKNOWN -> no entries; HIGH_VOL -> half size
[Symbol regime, 4h]      UNKNOWN > HIGH_VOL > DOWNTREND > UPTREND > RANGE > GRAY, 2-bar hysteresis
[Entry trigger, 1h]      T1 pullback / T2 breakout (UPTREND), T3 mean reversion (RANGE), gray zone (T1/T3)
[Fee-aware gate]         stop/target, full round-trip cost, net reward:risk, horizon H, cost-to-move
[Ranking]                RS vs BTC, ADX, relative volume -> top 3 per cycle
[Order flow]             shadow/live only: OBI over the order book near mid (TFI logged only)
[AI entry judge]         Laya: APPROVE / REJECT / DEFER, fail-closed
[Risk engine]            sizing, slots, caps, cash, final recheck -> BUY
[Broker]                 BacktestSim | ShadowSim (walks the live order book) | Live (disabled)
[Position manager]       exits -> SELL (no AI)
[Decision log, reports]
```

**One decision function for every mode** (`engine/decision_cycle.py`). Backtest and shadow differ only in
the market view (historical vs live), the broker and the judge, as the brief requires ("the delivered code
must match the code that produced the returns"). Order-flow and news gates are enabled by the view's
capabilities, not by `if mode == ...`.

## 3. Buy rules (all numbers in `config/default.yaml`)

| step | rule |
|---|---|
| Regime (4h, closed bars) | UPTREND: ADX >= 25, EMA20 > EMA50, EMA50 slope > 0, +DI > -DI. RANGE: ADX < 20 and abs(slope) < ATR. HIGH_VOL: ATR % in the top 10 % of the last 60 days |
| T1 pullback | UPTREND; in the last 3 bars low touched EMA20 + 0.5 ATR, min RSI in [40, 50]; now close > EMA20 and a green bar |
| T2 breakout | UPTREND; close above the previous 20-bar high and relative volume >= 1.5 |
| T3 mean reversion | RANGE; previous close below the lower Bollinger band, now back above it; RSI < 35 and rising |
| Stop | 2 x ATR % (1h), at least 1.5 %; above 4 % -> no trade |
| Target | T1 2R, T2 2.5R (for the gate; exits by trailing), T3 the Bollinger middle band |
| Cost | `cost_rt` = buy fee + sell fee (Wallex level 1 taker 0.30 % each) + slippage both sides + spread |
| Gate | target >= cost_rt + 0.15 % buffer + 0.25 % minimum net edge; net R:R = (target - cost)/(stop + cost) >= 1.6 / 2.0 / 1.0; a horizon H <= 24h must exist where c_move x ATR % x sqrt(H) reaches the target before the settlement cutoff; cost/expected move <= 0.5 |
| AI judge | Laya must APPROVE (gray-zone candidates must also pass "strong enough for half size") |
| Risk | see §5 |

## 4. Sell rules (deterministic, never via the AI)

| exit | rule |
|---|---|
| Protective stop | price <= stop (shadow: checked every 15 s against the best bid) |
| Breakeven | T1/T2 at +1R: stop moved to entry x (1 + fee break-even) |
| Trailing | T2 always, T1 after +1R: stop = max(stop, highest high - 2 ATR); **the stop never moves down** |
| Target | T1 at 2R, T3 at the Bollinger middle band |
| Time | T1/T2 after min(2H, 24) hours, T3 after 12 hours |
| Regime | symbol becomes DOWNTREND / HIGH_VOL / UNKNOWN, or BTC turns DOWNTREND |
| Kill switch / settlement | -3 % from equity peak: sell all and stop buying; 90 min before the end no new entries, 60 min before the end sell everything, so the account ends in TMN as the brief requires |

Every exit is a SELL order on the spot market for at most the free balance; one exit order per position,
also while its state is unknown.

## 5. Capital and risk management

| limit | value |
|---|---|
| risk per trade | 0.25 % of min(equity, 5 M TMN), **including the full round-trip cost** (team decision Q-08) |
| total open risk | 1 % |
| position size | <= 15 % of capital, >= 100,000 TMN and >= the exchange minimum |
| slots | <= 10 distinct coins (open or pending), reserved atomically before an order is sent |
| one position per coin | no adding to positions, no averaging down |
| daily loss | 1.5 % in a rolling 24 h window -> no new entries |
| kill switch | 3 % drawdown from the peak |
| fail closed | missing data, stale bars, judge timeout or error, unknown order state -> no new entry |

## 6. How AI is used

**Laya** (ConvAI Innovations, Apache 2.0, 421M parameters, `laya==0.4.13`, revision pinned) runs locally on
CPU behind a Jev-compatible API (`POST /v1/systemone`). It does not generate text; it returns calibrated
probabilities for typed questions. For each candidate it receives a compact English summary (<= 300 tokens:
setup, regimes, key indicators, fee-gate summary, order flow, news headlines, portfolio state) and answers:

| question | type | used for |
|---|---|---|
| decision | choice APPROVE / REJECT / DEFER | main decision |
| signals_conflict | yes/no | REJECT, `SIGNAL_CONFLICT` |
| news_risk | yes/no | REJECT, `EVENT_RISK` |
| insufficient_data | yes/no | DEFER, `INSUFFICIENT_DATA` |
| gray_zone_strong_enough | yes/no (gray zone only) | REJECT, `WEAK_CONTEXT` |

Code combines the answers with thresholds from config. Laya cannot create opportunities, choose markets,
size positions, move stops or delay exits (Constitution Art. 3). Measured on CPU: p50 1.57 s, p95 1.75 s
per 4-question call; deterministic (same input, same output). Every request and response is stored in
`judge_calls/<candidate_id>.json`.

Why not use an LLM to decide? A historical backtest of an LLM is not valid (the model may know the past),
LLM outputs are not reproducible, and the brief rewards explainable, re-runnable decisions. The backtest uses
the rules-only judge; the AI judge is evaluated in the shadow test against the same candidates
(variant A vs B; experiment XA vs XB, §8). An optional BoxAPI `gpt-6-luna` judge exists behind the same
contract (ADR-027, cheapest model only, 2 USD cap) but is not enabled.

**Wallex tools used:** public REST market data (`/v1/markets`, `/hector/web/v1/markets`,
`/v1/udf/history`, `/v1/depth`, `/v2/depth/all`, `/v1/trades`). The websocket endpoint was unavailable
(503), so the shadow test polls REST within the 600-request rate limit. No account or order endpoint is used.

## 7. Data integrity

- Only closed bars are used; the API returns the forming bar and it is dropped. 4h bars are built from 1h
  bars on UTC boundaries (the API's 240 resolution returns 1h bars) and are invalid if any hour is missing.
- No look-ahead: features and full decisions at 50 random moments are identical on data cut at t and on
  the full data (tests). Backtest fills happen at the next bar's open; stop-before-target inside a bar.
- Backtests read a hashed snapshot; the same snapshot and config give byte-identical trades.

## 8. Results

### 8.1 Backtest (`reports/backtest/20261022T172742Z/`)

Data: snapshot `0a4e977b89b6b576` (47 symbols, 1h, 2026-01-25 .. 2026-10-22). After the 60-day warm-up the
period is split by time: Train 2026-03-27 .. 07-30, Validation 07-30 .. 09-10, Test 09-10 .. 10-22.
Primary metric: 72-hour episodes starting with 5,000,000 TMN (like the challenge), at 4 different start
hours, 52 episodes per period. Fees: Wallex level-1 taker 0.30 % per side.

**Calibration (Train only).** The spec asks to calibrate `c_move`, the factor that turns ATR into an expected
favourable move. Over 406 triggered setups the median ratio of the realized best move within H hours to
ATR % x sqrt(H) is **0.355** (p25 0.09, p75 0.78), lower than the 0.6 placeholder.

**Main result: the strategy with the spec parameters does not trade.** With real fees, every candidate
fails the fee-aware gate, in Validation and in the single Test run (0 trades in 52 + 52 episodes; the
continuous Validation+Test run also has 0 trades, return 0 %). The decision funnel shows why: 651 triggers
in the continuous run, 643 rejected because net reward:risk after the full round-trip cost is below the
minimum, and the other 8 because the target was below the required move (3), the stop would be wider
than 4 % (3) or no horizon reached the target (2). In 304 of the hours BTC was in a downtrend and no entry
was considered at all.

**What the gate protected against** (Validation, the only ablation that trades):

| variant | episodes | median 72h return | p10 | p90 | positive episodes | trades | median max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline (spec parameters) | 52 | 0.00 % | 0.00 % | 0.00 % | 0 % | 0 | 0.00 % |
| **fee gate disabled** | 52 | **-0.17 %** | -0.82 % | +0.74 % | 27 % | 305 | 0.35 % |

A 60-day continuous check without the gate made 98 trades, lost 2.8 % with a profit factor of 0.67, and paid
251,000 TMN in fees (5 % of capital) before the kill switch stopped it. The same signals with the gate
make no trades: **the triggers alone have no edge large enough to pay 0.6 % round-trip fees**, and the
system recognises that instead of losing money. Relaxing reward:risk (1.0/1.2) and using the optimistic
p75 `c_move` still gives 0 trades, even at 0.10 % fees, because the 2R targets of T1/T2 are larger than the
move the market typically delivers within 24 hours.

Benchmarks (median over Test episodes): cash 0 %, buy-and-hold BTCTMN +0.37 %, equal-weight top-5
-0.35 %. In USDT terms every TMN-cash strategy lost ~29 % over Validation+Test because USDT/TMN rose from
194,199 to 271,340.

Acceptance tests all pass (66 tests), including byte-identical reproducibility and zero coins at
episode end.

### 8.2 Shadow test (`reports/shadow/challenge/`, running until 2026-10-23 17:30 UTC)

Live Wallex data, simulated orders filled against the real order book, four virtual accounts on the same
candidates:

| variant | judge | fee gate | purpose |
|---|---|---|---|
| A | rules only | on | the trading configuration |
| B | Laya | on | AI judge on the trading configuration |
| XA | rules only | off | experiment: candidates that the gate would block |
| XB | Laya | off | experiment: does the AI judge filter the losing entries? |

`summary.md` is rewritten every hour with equity, return, trades, the decision funnel, judge decisions and
latency, API errors and uptime. Expected from the backtest: A and B make few or no trades; the AI judge's
value is measured by XB vs XA. Order books and trades are recorded for later replay and for verifying the
meaning of `isBuyOrder`.

### 8.3 Team experiments (information only; `scripts/exp_*.py`, `reports/experiments/`)

**Higher reward targets** (T1/T2 = 3/3.5R, 4/5R, 5/6R), 52 Validation + 52 Test episodes each:

| targets | horizon check | Validation trades / total PnL | Test trades / total PnL | Test win rate |
|---|---|---:|---:|---:|
| any (2R-6R) | on (spec) | 0 / 0 | 0 / 0 | - |
| 3 / 3.5 R | off | 57 / -172,346 TMN | 129 / -273,593 TMN | 33 % |
| 4 / 5 R | off | 162 / +51,295 TMN | 271 / -1,078,355 TMN | 21 % |
| 5 / 6 R | off | 162 / +80,926 TMN | 271 / -1,037,853 TMN | 21 % |

Higher targets pass the reward:risk check but are rarely reached (4 of 271 Test trades hit the target); the
small Validation gain reverses on Test, so it is not a robust edge.

**The dollar effect.** On Wallex, TMN price = USD price x USDT/TMN. Over the snapshot USDT/TMN moved 0.39 %
per hour (std) and drifted +0.24 % per day; it explains 49 % of BTCTMN's hourly variance and 16-31 % for the
other top pairs. Running the strategy on dollar-adjusted prices (each TMN candle divided by that hour's
USDT/TMN) gives the same answer with the gate (0 trades); without the gate it makes ~35 % fewer trades and
loses less (Validation -122,757 vs -147,257 TMN; Test -1,022,360 vs -1,536,116 TMN) but still loses.
Wallex USDT pairs are liquid only for a few coins (BTC, ETH, XRP comparable to TMN; PEPE, DOGE, BNB, ADA much
thinner, BNB/ADA spreads 1.3 % / 6 %), and holding the capital in USDT adds two conversions (~0.6 %) and a
currency position the challenge does not reward (returns are measured and must be realized in TMN).

### 8.4 What this means for the challenge

With Wallex's level-1 fees the honest answer of this strategy is *no trade*, which protects capital
(Constitution Art. 19: "no trade is a valid outcome"). Trading more would need lower fees (maker orders or a
higher fee tier), larger expected moves (longer horizons than the 72-hour challenge allows) or a signal with
a demonstrated edge. Changing the strategy parameters is a team decision (Art. 18); the experiment variants
above provide the evidence for it.

## 9. Limitations (honest list)

- Order flow and news cannot be evaluated historically; their value is measured only in the shadow test.
- No historical spread: backtests use a static spread estimate from the snapshot's market metadata.
- Survivorship bias: the universe is today's pair list and today's allowed-markets list.
- TMN returns include part of the USDT/TMN move; a USDT-denominated return is reported too.
- The fee endpoint `/v1/account/fee` rejected the provided key; fees are the Wallex level-1 schedule
  (maker 0.25 %, taker 0.30 %) confirmed by the team.
- `isBuyOrder` semantics are unverified (a small sample suggests it marks the maker side); TFI is log-only.
- Reduced scope for the deadline: no timeframe comparison, no parameter grid, no random-entry benchmark,
  no news feed in the shadow test.
- Fees are modelled in TMN on both sides; Wallex may charge the buy fee in the base asset.
