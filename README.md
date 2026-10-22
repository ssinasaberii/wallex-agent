# Wallex Spot Trading Agent â€” Backtest + Shadow Test

An algorithmic spot trading agent for the Wallex 72-hour hackathon challenge. A deterministic,
regime-based strategy proposes entries; a local AI model (**Laya**) approves, rejects or defers each entry;
deterministic risk rules size, protect and exit every position. Every decision is logged with its reasons.

> **Scope of this version:** backtest and shadow test only. **No real order is ever sent.** The REST
> client can only issue GET requests to an allowlist of public/market endpoints; `LiveBroker` raises
> `NotImplementedError` (tests prove no path reaches it).

Architecture, AI role, buy/sell rules, risk management and results: **[ARCHITECTURE.md](ARCHITECTURE.md)**.
Specification: `docs/spec/` (v1.1 + v1.2 in `09-...md`). API facts: `docs/api-verification.md`.
Open questions and team decisions: `docs/OPEN_QUESTIONS.md`.

---

## 1. Requirements

- Windows 10/11 (PowerShell) or Linux/macOS, **Python 3.12**
- Internet access to `api.wallex.ir` (data) and, once, to Hugging Face (Laya weights download)
- ~3 GB disk for the Laya environment (CPU PyTorch + model)

## 2. Setup

Windows:

```powershell
.\tasks.ps1 setup          # .venv with locked dependencies (requirements.lock)
.\tasks.ps1 setup-laya     # .venv-laya with CPU torch + laya[serve]==0.4.13 (requirements-laya.lock)
copy .env.example .env     # then fill in the values (see below)
```

Linux/macOS: the same targets exist in the `Makefile` (`make setup`, `make setup-laya`, ...).

`.env` (never committed, never logged):

| key | needed for |
|---|---|
| `WALLEX_API_KEY` | only `/v1/account/fee` in `verify-api` (optional; fees come from config) |
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | optional BoxAPI comparison judge (not used by default) |
| `LAYA_URL` | local Laya server, default `http://127.0.0.1:8000` |
| `CHALLENGE_END_UTC` | challenge end; overrides `config/default.yaml` when set |

## 3. Reproduce the backtest (offline)

The backtest reads a **content-addressed data snapshot** (`data/snapshots/<hash>/`, verified against its
manifest before use), so the same snapshot + config give byte-identical `trades.csv` (test 2).

```powershell
.\tasks.ps1 fetch-data     # optional: download fresh candles and freeze a NEW snapshot
.\tasks.ps1 backtest       # uses data/snapshots/LATEST; or: python -m wallex_agent backtest --snapshot <hash>
```

Output: `reports/backtest/<run_id>/`

| file | content |
|---|---|
| `summary.md` | results, validation table (ablations, fee/slippage sensitivity), limitations |
| `results.json` | everything in machine-readable form |
| `config.yaml`, `data_snapshot_hash`, `git_commit` | exact inputs (source hash if git is unavailable) |
| `trades.csv`, `episodes_test.csv` | Test-period trades and per-episode metrics |
| `equity.csv`, `trades_continuous.csv`, `decisions.jsonl`, `funnel.csv` | continuous run |
| `test_episodes/ep*/decisions.jsonl` | decision log of every Test episode |
| `equity_drawdown.png`, `episode_returns.png` | charts |

Snapshot used for the reported v1.4 results: `ecbb7bb14b3ec827` (42 symbols, 3 years of 1h candles):
`python -m wallex_agent backtest --snapshot ecbb7bb14b3ec827` (run `20261022T231324Z`). The earlier hourly v1.2
results used `0a4e977b89b6b576` with `--config config/v1.2_hourly.yaml`. The optimisation studies are scripts in
`scripts/` (`optimize_tf.py`, `exp_exits.py`, `exp_risk.py`, ...) and are summarised in `docs/experiments.md`.

## 4. Run the shadow test (live data, simulated orders)

```powershell
.\tasks.ps1 laya-serve                    # terminal 1: local Laya judge on 127.0.0.1:8000
.\tasks.ps1 shadow                        # terminal 2: runs until the challenge end
# or unattended, with automatic restart and resume:
powershell -ExecutionPolicy Bypass -File scripts\run_shadow_forever.ps1 -Session challenge
```

Smoke test (one decision cycle now, then exit): `python -m wallex_agent shadow --once --session smoke`.

Variants on the same candidates: **A** rules judge, **B** Laya judge, **C** Laya judge + news gate (the trading
algorithm). Output: `reports/shadow/<session>/` â€” `summary.md` (rewritten every hour: equity per variant, decision
funnel, judge statistics, news that would block, uptime, API errors), `A|B|C/decisions.jsonl`,
`B|C/judge_calls/*.json` (full Laya request/response), `news.jsonl` (interpreted headlines), `trades_*.csv`,
`equity_*.csv`, `state.sqlite` (resume state). The v1.4 session is `challenge-v14`.
Raw order books (20 levels, every 10 s) and trades are recorded in `data/recordings/<date>/`.

## 5. Tests and checks

```powershell
.\tasks.ps1 test           # 96 tests, including all 10 acceptance tests of spec 09 Â§13, news and scale-out
.\tasks.ps1 lint
.\tasks.ps1 verify-api     # Phase 0 read-only API checks (writes data/snapshots/verify_*)
.\tasks.ps1 verify-laya    # Laya contract + CPU latency
```

| spec 09 Â§13 acceptance test | file |
|---|---|
| 1 no look-ahead (50 random moments, features and full decisions) | `tests/test_lookahead.py`, `tests/test_lookahead_decisions.py` |
| 2 reproducibility (byte-identical trades) | `tests/test_reproducibility_settlement.py` |
| 3 indicators vs reference implementations | `tests/test_indicators.py` |
| 4 risk invariants (10 slots under concurrency, floors, caps, UNKNOWN orders) | `tests/test_risk.py` |
| 5 kill switch | `tests/test_risk.py` |
| 6 judge fail-closed (timeout, invalid, id mismatch, expired; no SELL via judge) | `tests/test_judge_failclosed.py` |
| 7 fee gate | `tests/test_fee_gate.py` |
| 8 zero coins at episode end | `tests/test_reproducibility_settlement.py` |
| 9 LiveBroker unreachable | `tests/test_no_live_and_trailing.py`, `tests/test_safety.py` |
| 10 stop never moves down (property-based) | `tests/test_no_live_and_trailing.py` |

## 6. Repository map

```text
config/            default.yaml (v1.4, all parameters), v1.2_hourly.yaml, allowed_markets.yaml (challenge denylist),
                   macro_events.yaml, rss_feeds.yaml (news sources, tiers, asset names, systemic keywords)
docs/              spec/, api-verification.md, OPEN_QUESTIONS.md, experiments.md
src/wallex_agent/
  data/            wallex_rest (GET-only), candles, market_meta, market_view, snapshot, quality, fees, allowed
  features/        indicators (EMA/ATR/ADX/RSI/BB/Donchian/RelVol/A-D), orderflow (OBI/TFI)
  strategy/        regime, triggers, fee_horizon, universe_rank, candidate
  context/         news (RSS, dedupe, asset matching, gate), news_interpreter (Laya), fng (Fear & Greed)
  judge/           base (contract + fail-closed), rules_baseline, laya
  risk/            portfolio (slots, reservations, kill switch), sizing
  execution/       backtest_sim, live (disabled), broker
  positions/       manager (exits)
  engine/          decision_cycle (the single cycle for every mode), features
  backtest/        runner, suite, metrics
  shadow/          runner (live data loop, order-book simulator, variants, recorder, report)
  decision_log.py  JSONL decision log
scripts/           run_shadow_forever.ps1
tests/
```

## 7. Safety

- No order, OTC or withdrawal endpoint exists in runnable code (static test + allowlisted client).
- API keys are read only from `.env`, masked in every log line, never written to reports.
- Laya runs on `127.0.0.1` only; the BoxAPI judge receives candidate summaries only (no keys, no balances).
