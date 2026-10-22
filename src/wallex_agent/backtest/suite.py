"""Backtest suite (spec 09 §9): calibration on Train, validation episodes, ablations and sensitivities on
Validation, one Test run, benchmarks, report.  `python -m wallex_agent backtest`.

Reduced scope for the challenge deadline (PLAN.md §1.1): no timeframe comparison, no parameter grid,
random-entry benchmark omitted. Every omission is listed in the report.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from wallex_agent.backtest.metrics import episode_stats, run_metrics  # noqa: E402
from wallex_agent.backtest.runner import build_universe, run_period  # noqa: E402
from wallex_agent.data.fees import FeeSnapshot  # noqa: E402
from wallex_agent.data.market_view import HistoricalMarketView  # noqa: E402
from wallex_agent.data.snapshot import SNAP_DIR, load_snapshot  # noqa: E402
from wallex_agent.engine.features import FeatureProvider  # noqa: E402
from wallex_agent.strategy.regime import regime_at  # noqa: E402
from wallex_agent.strategy.triggers import pick_setup  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
H1 = 3600
DAY = 86400


def source_hash() -> str:
    """Git commit of the code (with -dirty if src/ or config/ has uncommitted changes); source hash otherwise."""
    import shutil as _sh
    import subprocess

    git = _sh.which("git") or next((p for p in (r"C:\Program Files\Git\cmd\git.exe",) if Path(p).exists()), None)
    try:
        if git:
            sha = subprocess.run([git, "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                 check=True).stdout.strip()
            dirty = subprocess.run([git, "status", "--porcelain", "--", "src", "config"], cwd=ROOT,
                                   capture_output=True, text=True, check=True).stdout.strip()
            if sha:
                return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        pass
    h = hashlib.sha256()
    for p in sorted((ROOT / "src").rglob("*.py")):
        h.update(p.read_bytes())
    return "nogit-" + h.hexdigest()[:12]


def with_overrides(cfg, **paths):
    """Return a new Config with dotted-path overrides, e.g. **{"fee_gate.c_move": 0.9}."""
    d = cfg.model_dump()
    for path, val in paths.items():
        node = d
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = val
    return type(cfg).model_validate(d)


# ---------------------------------------------------------------- calibration (09 §7.5.5)
def calibrate_c_move(cfg, view, uni, feats, start: int, end: int) -> dict:
    """Median of realized max favourable move / (ATR% x sqrt(H)) over triggered setups in Train."""
    ratios, n = [], 0
    hs = sorted(cfg.fee_gate.horizons_h)
    period4 = cfg.data.regime_hours * H1
    tf = cfg.data.trigger_hours
    step = max(6, tf) * H1
    first = (start // step + 1) * step
    for sym in uni.static_symbols:
        full1 = feats.signal_1h(view, sym)  # 1h signal prices: same units as the entry price below
        if len(full1) < 500:
            continue
        hi = full1["high"].to_numpy()
        ct = full1["close_time"].to_numpy()
        for t in range(first, end, step):  # every max(6h, trigger bar): fast and decorrelated
            v = view.as_of(t)
            bars, f = feats.trigger(v, sym)
            if len(bars) < 60 or int(bars["close_time"].iloc[-1]) != t:
                continue
            _, _, reg = feats.regime(v, sym)
            rg, _ = regime_at(reg, t, period4)
            setup, _ = pick_setup(rg, bars, f, len(bars) - 1, cfg.setups)
            if setup is None:
                continue
            i = len(bars) - 1
            atr = float(f["atr_pct"].iloc[i])
            px = float(bars["close"].iloc[i])
            if not atr > 0:
                continue
            n += 1
            j0 = int(np.searchsorted(ct, t, side="right"))
            for h in hs:
                if j0 + h > len(hi):
                    break
                mfe = float(hi[j0:j0 + h].max()) / px - 1
                ratios.append(max(mfe, 0.0) / (atr * math.sqrt(h / tf)))
    if not ratios:
        return {"c_move": cfg.fee_gate.c_move, "n_setups": 0, "method": "fallback to config (no setups)"}
    return {"c_move": round(float(np.median(ratios)), 3), "n_setups": n, "n_ratios": len(ratios),
            "p25": round(float(np.percentile(ratios, 25)), 3), "p75": round(float(np.percentile(ratios, 75)), 3),
            "method": "median over triggered setups in Train (every 6h) and H in horizons of "
                      "max(high[t+1..t+H])/close_t-1 divided by ATR%_1h*sqrt(H)"}


# ---------------------------------------------------------------- episodes
def episodes(start: int, end: int, hours: int, offsets: list[int]) -> list[tuple[int, int, int]]:
    out = []
    for off in offsets:
        t = (start // DAY) * DAY + off * H1
        while t < start:
            t += hours * H1
        while t + hours * H1 <= end:
            out.append((off, t, t + hours * H1))
            t += hours * H1
    return out


def run_episodes(cfg, view, uni, feats, eps, fee=None, slip_mult=1.0, log_dir=None) -> tuple[list[dict], list]:
    rows, all_trades = [], []
    for k, (off, s, e) in enumerate(eps):
        r = run_period(cfg, view, uni, s, e, fee=fee, features=feats, slip_mult=slip_mult,
                       log_dir=(log_dir / f"ep{k:03d}") if log_dir else None,
                       header={"mode": "backtest", "variant": "A", "strategy_version": cfg.strategy_version,
                               "config_hash": cfg.config_hash, "episode": k})
        m = run_metrics(r.trades, r.equity, cfg.challenge.initial_equity_tmn, r.fees_paid)
        m.update(episode=k, offset_h=off, start=s, end=e, killed=r.killed,
                 leftover_coins=sum(1 for q in r.leftover_qty.values() if q > 0))
        rows.append(m)
        for t in r.trades:
            all_trades.append(t | {"episode": k})
    return rows, all_trades


def buy_hold(view, sym: str, s: int, e: int, fee: float) -> float | None:
    b = view.full(sym, 1)
    a = b[(b["close_time"] > s) & (b["close_time"] <= e)]
    if len(a) < 2:
        return None
    return 100 * (float(a["close"].iloc[-1]) / float(a["open"].iloc[0]) * (1 - fee) ** 2 - 1)


# ---------------------------------------------------------------- main
def run(cfg, config_path: str, snapshot: str | None, quick: bool = False) -> dict[str, Any]:
    snapshot = snapshot or (SNAP_DIR / "LATEST").read_text().strip()
    candles, meta_path, manifest = load_snapshot(snapshot)
    view = HistoricalMarketView(candles)
    uni = build_universe(cfg, meta_path, candles)
    feats = FeatureProvider(cfg)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "reports" / "backtest" / run_id
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config_path, out / "config.yaml")
    (out / "data_snapshot_hash").write_text(snapshot)
    (out / "git_commit").write_text(source_hash())

    refs = (cfg.data.btc_tmn_symbol, cfg.data.btc_regime_symbol, cfg.data.usdt_tmn_symbol)
    t_first = min(int(d["open_time"].min()) for s, d in candles.items() if s in refs)
    t_last = max(int(d["close_time"].max()) for d in candles.values())
    # Warm-up: min_regime_bars closed regime bars (+6 days margin). 4h regime: 66 days; daily regime: 366 days.
    warm_days = math.ceil(cfg.data.min_regime_bars * cfg.data.regime_hours / 24) + 6
    usable = (t_first // DAY + warm_days) * DAY
    span = t_last - usable
    tr_end = usable + int(span * cfg.backtest.split.train) // DAY * DAY
    va_end = tr_end + int(span * cfg.backtest.split.validation) // DAY * DAY
    split = {"train": [usable, tr_end], "validation": [tr_end, va_end], "test": [va_end, t_last]}
    iso = {k: [datetime.fromtimestamp(x, UTC).strftime("%Y-%m-%d %H:%M") for x in v] for k, v in split.items()}
    res: dict[str, Any] = {"run_id": run_id, "snapshot": snapshot, "split_utc": iso,
                           "universe_static": uni.static_symbols}

    # 1. calibrate c_move on Train only
    cal = calibrate_c_move(cfg, view, uni, feats, usable, tr_end)
    res["c_move_calibration"] = cal
    cfg_cal = with_overrides(cfg, **{"fee_gate.c_move": cal["c_move"]})
    feats_cal = FeatureProvider(cfg_cal)

    hours, offs = cfg.backtest.episode_hours, cfg.backtest.episode_start_hours_utc
    val_eps = episodes(tr_end, va_end, hours, offs)
    test_eps = episodes(va_end, t_last, hours, offs)
    if quick:
        val_eps, test_eps = val_eps[:4], test_eps[:4]

    # 2. Validation: baseline and ablations (information for the team)
    variants = {
        "baseline": cfg_cal,
        "fee_gate_on": with_overrides(cfg_cal, **{"fee_gate.enabled": True}),
        "trail_2xATR": with_overrides(cfg_cal, **{"exits.trail_atr_mult": 2.0}),
        "no_T1": with_overrides(cfg_cal, **{"setups.T1_pullback.enabled": False}),
        "no_T2": with_overrides(cfg_cal, **{"setups.T2_breakout.enabled": False}),
        "no_T3": with_overrides(cfg_cal, **{"setups.T3_meanrev.enabled": False}),
        "no_btc_filter": with_overrides(cfg_cal, **{"btc_filter.enabled": False}),
        "no_hysteresis": with_overrides(cfg_cal, **{"regime.hysteresis_enabled": False}),
        "gray_approve": with_overrides(cfg_cal, **{"judge.baseline_gray_policy": "approve"}),
        "tmn_signal_prices": with_overrides(cfg_cal, **{"data.signal_price": "tmn"}),
    }
    if quick:
        variants = {k: variants[k] for k in ("baseline", "fee_gate_on")}
    val_table = {}
    for name, c in variants.items():
        same_features = c.indicators == cfg_cal.indicators and c.regime == cfg_cal.regime and c.data == cfg_cal.data
        fp = feats_cal if same_features else FeatureProvider(c)
        rows, _ = run_episodes(c, view, uni, fp, val_eps)
        val_table[name] = episode_stats(rows)
    # fee and slippage sensitivity on the baseline (09 §9.3)
    for fpct in cfg.fees.sensitivity_per_side_pct:
        rows, _ = run_episodes(cfg_cal, view, uni, feats_cal, val_eps,
                               fee=FeeSnapshot.from_config(cfg.fees, taker_override_pct=fpct))
        val_table[f"fee_{fpct}%_per_side"] = episode_stats(rows)
    rows, _ = run_episodes(cfg_cal, view, uni, feats_cal, val_eps, slip_mult=2.0)
    val_table["slippage_x2"] = episode_stats(rows)
    for risk, cap in ((0.5, 2.0),):
        c = with_overrides(cfg_cal, **{"risk.risk_per_trade_pct": risk, "risk.max_open_risk_pct": cap})
        rows, _ = run_episodes(c, view, uni, feats_cal, val_eps)
        val_table[f"risk_{risk}%_cap_{cap}% (info only)"] = episode_stats(rows)
    res["validation"] = val_table

    # 3. Test: run ONCE with the baseline config (spec parameters + c_move calibrated on Train)
    test_rows, test_trades = run_episodes(cfg_cal, view, uni, feats_cal, test_eps, log_dir=out / "test_episodes")
    res["test"] = episode_stats(test_rows)
    res["test_leftover_coins"] = int(sum(r["leftover_coins"] for r in test_rows))

    # 4. Continuous run over Validation+Test with 5M TMN, no reset
    cont = run_period(cfg_cal, view, uni, tr_end, t_last, features=feats_cal, log_dir=out / "continuous")
    cm = run_metrics(cont.trades, cont.equity, cfg.challenge.initial_equity_tmn, cont.fees_paid)
    usdt = view.full(cfg.data.usdt_tmn_symbol, 1)
    u = usdt[(usdt["close_time"] >= tr_end) & (usdt["close_time"] <= t_last)]["close"]
    if len(u) > 1:
        cm["return_usdt_pct"] = 100 * ((1 + cm["return_pct"] / 100) / (float(u.iloc[-1]) / float(u.iloc[0])) - 1)
    cm["time_in_market_pct"] = _time_in_market(cont.trades, tr_end, t_last)
    res["continuous"] = cm

    # 5. Benchmarks on the Test episodes
    fee = cfg.fees.taker_pct / 100
    bh_btc = [x for x in (buy_hold(view, cfg.data.btc_tmn_symbol, s, e, fee) for _, s, e in test_eps) if x is not None]
    top5 = uni.static_symbols[:5]
    bh5 = []
    for _, s, e in test_eps:
        xs = [buy_hold(view, sym, s, e, fee) for sym in top5]
        xs = [x for x in xs if x is not None]
        if xs:
            bh5.append(float(np.mean(xs)))
    bh_usdt = [x for x in (buy_hold(view, cfg.data.usdt_tmn_symbol, s, e, fee) for _, s, e in test_eps)
               if x is not None]
    res["benchmarks_test_median_return_pct"] = {
        "cash": 0.0, "buy_hold_BTCTMN": float(np.median(bh_btc)) if bh_btc else None,
        "hold_USDT (toman devaluation)": float(np.median(bh_usdt)) if bh_usdt else None,
        f"buy_hold_equal_weight_{'+'.join(top5)}": float(np.median(bh5)) if bh5 else None,
        "random_entry": "omitted (deadline)"}
    res["strategy_test_mean_return_pct"] = float(np.mean([r["return_pct"] for r in test_rows])) if test_rows else None

    # 6. Files
    pd.DataFrame(test_trades).to_csv(out / "trades.csv", index=False, float_format="%.6f")
    pd.DataFrame(test_rows).to_csv(out / "episodes_test.csv", index=False, float_format="%.6f")
    pd.DataFrame(cont.equity, columns=["t", "equity_tmn"]).to_csv(out / "equity.csv", index=False, float_format="%.2f")
    pd.DataFrame(cont.trades).to_csv(out / "trades_continuous.csv", index=False, float_format="%.6f")
    pd.DataFrame([{"stage": k, "count": v} for k, v in sorted(cont.funnel.items(), key=lambda kv: -kv[1])]).to_csv(
        out / "funnel.csv", index=False)
    shutil.copyfile(out / "continuous" / "decisions.jsonl", out / "decisions.jsonl")
    _charts(out, cont.equity, test_rows)
    (out / "results.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    _summary_md(out, res, cfg, manifest)
    res["out_dir"] = str(out.relative_to(ROOT))
    return res


def _time_in_market(trades, s, e) -> float:
    hours = set()
    for t in trades:
        for h in range(int(t["entry_t"]) // H1, int(t["exit_t"]) // H1 + 1):
            hours.add(h)
    return 100 * len(hours) / max(1, (e - s) // H1)


def _charts(out: Path, equity, rows) -> None:
    if equity:
        ts = [datetime.fromtimestamp(t, UTC) for t, _ in equity]
        eq = np.array([e for _, e in equity])
        fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        ax[0].plot(ts, eq)
        ax[0].set_title("Continuous run equity (TMN)")
        peak = np.maximum.accumulate(eq)
        ax[1].fill_between(ts, -100 * (peak - eq) / peak, 0, color="tab:red")
        ax[1].set_title("Drawdown %")
        fig.tight_layout()
        fig.savefig(out / "equity_drawdown.png", dpi=110)
        plt.close(fig)
    if rows:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist([r["return_pct"] for r in rows], bins=20)
        ax.set_title("Test episodes: return % distribution")
        fig.tight_layout()
        fig.savefig(out / "episode_returns.png", dpi=110)
        plt.close(fig)


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _summary_md(out: Path, res: dict, cfg, manifest: dict) -> None:
    L = [f"# Backtest {res['run_id']}", "",
         f"- strategy: `{cfg.strategy_version}` (regime {cfg.data.regime_hours}h, trigger {cfg.data.trigger_hours}h, "
         f"signal prices {cfg.data.signal_price}, fee gate {'on' if cfg.fee_gate.enabled else 'off'})",
         f"- episodes: {cfg.backtest.episode_hours}h, start offsets {cfg.backtest.episode_start_hours_utc} h",
         f"- data snapshot: `{res['snapshot']}` ({len(manifest['ranges'])} symbols, 1h candles; longer bars "
         "aggregated from 1h)",
         f"- source hash: `{(out / 'git_commit').read_text()}`, config hash `{cfg.config_hash}`",
         f"- split (UTC): {res['split_utc']}",
         f"- fees: taker {cfg.fees.taker_pct}% per side ({cfg.fees.source}); slippage tiers "
         f"{cfg.slippage.tier1_per_side_pct}% / {cfg.slippage.tier2_per_side_pct}% per side (assumed)",
         "- judge: RulesBaselineJudge only (09 §7.9)", "",
         "## c_move calibration (Train only)", "", "```", json.dumps(res["c_move_calibration"], indent=1), "```", "",
         "## Test (run once, baseline config)", ""]
    L += [f"- {k}: {_fmt(v)}" for k, v in res["test"].items()]
    L += ["", "## Continuous run (Validation+Test, 5M TMN, no reset)", ""]
    L += [f"- {k}: {_fmt(v)}" for k, v in res["continuous"].items()]
    L += ["", "## Benchmarks (median over Test episodes, %)", ""]
    L += [f"- {k}: {_fmt(v)}" for k, v in res["benchmarks_test_median_return_pct"].items()]
    L += ["", f"## Validation: variants, ablations and sensitivities ({cfg.backtest.episode_hours}h episodes)", "",
          "| variant | episodes | mean % | median % | p10 % | p90 % | positive % | zero-trade % | median maxDD % "
          "| trades | kills |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for k, s in res["validation"].items():
        if s:
            L.append(f"| {k} | {s['episodes']} | {s['mean_return_pct']:.3f} | {s['median_return_pct']:.3f} | "
                     f"{s['p10_return_pct']:.3f} | "
                     f"{s['p90_return_pct']:.3f} | {s['positive_pct']:.0f} | {s['zero_trade_pct']:.0f} | "
                     f"{s['median_maxdd_pct']:.3f} | {s['total_trades']} | {s['kill_switch_episodes']} |")
    L += ["", "## Limitations (09 §15)", "",
          "- Order flow (OBI/TFI) and news cannot be evaluated historically; only in Shadow.",
          "- No historical spread: a static spread estimate from the snapshot's market metadata is used.",
          "- Survivorship bias: the universe is today's pair list and today's allowed-markets denylist.",
          "- TMN returns include part of the USDT/TMN rate move; a USDT-denominated return is reported.",
          "- Fees are charged in TMN on both sides; Wallex may take the buy fee in the base asset.",
          "- Reduced scope for the deadline: no timeframe comparison, no parameter grid, no random-entry benchmark.",
          "- With 0.25% risk per trade and 1% total open risk, absolute 3-day returns are small by design."]
    (out / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
