"""Information-only experiment (team question 2026-10-22): does the USDT/TMN rate distort the analysis, and
what if the capital were held in USDT and only USDT pairs were traded?

1. Variance decomposition: TMN price = USD price x USDT/TMN. How much of the hourly move is the dollar?
2. Liquidity and spread of Wallex USDT pairs vs TMN pairs (Phase 0 market snapshot).
3. Strategy on dollar-adjusted prices: every TMN candle divided by the USDT/TMN rate of the same hour
   (scaled to the latest rate so TMN floors stay meaningful). This is "the same market without the dollar".
Usage: python -I scripts/exp_usdt.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from wallex_agent.backtest.metrics import episode_stats  # noqa: E402
from wallex_agent.backtest.runner import build_universe  # noqa: E402
from wallex_agent.backtest.suite import episodes, run_episodes, with_overrides  # noqa: E402
from wallex_agent.config import load_config  # noqa: E402
from wallex_agent.data.market_view import HistoricalMarketView  # noqa: E402
from wallex_agent.data.snapshot import load_snapshot  # noqa: E402
from wallex_agent.engine.features import FeatureProvider  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SNAP = "0a4e977b89b6b576"
cfg0 = load_config(apply_env=False)
candles, meta_path, _ = load_snapshot(SNAP)
out: dict = {}

# ---------------------------------------------------------------- 1. variance decomposition
u = candles["USDTTMN"].set_index("open_time")["close"]
ur = np.log(u).diff()
rows = []
for sym in ["BTCTMN", "ETHTMN", "SOLTMN", "PEPETMN", "DOGETMN", "XRPTMN", "BNBTMN"]:
    p = candles[sym].set_index("open_time")["close"]
    pr = np.log(p).diff()
    j = pd.concat([pr, ur], axis=1, keys=["tmn", "usdt"]).dropna()
    usd = j["tmn"] - j["usdt"]
    rows.append({"symbol": sym, "hourly_vol_tmn_%": round(100 * j["tmn"].std(), 3),
                 "hourly_vol_usdttmn_%": round(100 * j["usdt"].std(), 3),
                 "dollar_share_of_variance_%": round(100 * j["usdt"].var() / j["tmn"].var(), 2),
                 "corr_usd_price_vs_dollar": round(float(np.corrcoef(usd, j["usdt"])[0, 1]), 3),
                 "drift_from_dollar_%_per_day": round(100 * 24 * j["usdt"].mean(), 3)})
out["variance"] = rows

# ---------------------------------------------------------------- 2. USDT vs TMN pair liquidity
meta = json.loads(meta_path.read_text(encoding="utf-8"))["markets"]
usdt_rate = float(u.iloc[-1])
liq = []
for base in ["BTC", "ETH", "SOL", "PEPE", "DOGE", "XRP", "BNB", "TRX", "ADA", "SUI"]:
    t, s = meta.get(f"{base}TMN"), meta.get(f"{base}USDT")
    if not t or not s:
        continue
    liq.append({"base": base, "tmn_vol_24h_bn_tmn": round(t["tmn_volume_24h"] / 1e9, 2),
                "usdt_vol_24h_bn_tmn": round(s["tmn_volume_24h"] / 1e9, 2), "tmn_spread_%": t["spread_pct"],
                "usdt_spread_%": s["spread_pct"], "usdt_min_notional": s["min_notional"]})
out["liquidity"] = liq

# ---------------------------------------------------------------- 3. strategy on dollar-adjusted prices
scale = (usdt_rate / u)  # multiply TMN prices by U_last / U_t -> constant-dollar toman
adj = {}
for sym, df in candles.items():
    if sym == "USDTTMN" or not sym.endswith("TMN"):
        adj[sym] = df
        continue
    f = df["open_time"].map(scale).ffill().bfill()
    d = df.copy()
    for c in ("open", "high", "low", "close"):
        d[c] = d[c] * f.to_numpy()
    adj[sym] = d
uni = build_universe(cfg0, meta_path, candles)
res = json.loads((ROOT / "reports/backtest/20261022T172742Z/results.json").read_text())
split = {k: [int(pd.Timestamp(x, tz="UTC").timestamp()) for x in v] for k, v in res["split_utc"].items()}
val = episodes(split["validation"][0], split["validation"][1], 72, [0, 6, 12, 18])
test = episodes(split["test"][0], split["test"][1], 72, [0, 6, 12, 18])
variants = {"baseline (spec)": with_overrides(cfg0, **{"fee_gate.c_move": 0.355}),
            "no fee gate": with_overrides(cfg0, **{"fee_gate.enabled": False})}
strat = []
for prices, data in (("TMN prices", candles), ("dollar-adjusted prices", adj)):
    view = HistoricalMarketView(data)
    for name, c in variants.items():
        fp = FeatureProvider(c)
        for period, eps in (("validation", val), ("test", test)):
            rws, trades = run_episodes(c, view, uni, fp, eps)
            s = episode_stats(rws)
            r = {"prices": prices, "variant": name, "period": period, "trades": s["total_trades"],
                 "median_72h_%": round(s["median_return_pct"], 3), "positive_eps_%": round(s["positive_pct"]),
                 "win_rate_%": round(100 * sum(t["pnl_tmn"] > 0 for t in trades) / len(trades)) if trades else 0,
                 "sum_pnl_tmn": round(sum(t["pnl_tmn"] for t in trades))}
            strat.append(r)
            print(json.dumps(r, ensure_ascii=False), flush=True)
out["strategy"] = strat
dst = ROOT / "reports" / "experiments"
dst.mkdir(parents=True, exist_ok=True)
(dst / "usdt_effect.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: out[k] for k in ("variance", "liquidity")}, indent=1, ensure_ascii=False))
