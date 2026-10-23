"""Information-only study (user questions 2026-10-23): how different are Wallex USDT pairs from TMN pairs?

1. Volume: last 30 days of 1h candles (read-only /v1/udf/history) for each coin's TMN and USDT pair; USDT-pair
   volume converted to TMN with the USDT/TMN close of the same hour.
2. Tracking: real USDT-pair close vs "TMN close / USDTTMN close" (the prices exp_usdt_account.py simulates).
3. Dollar share of the moves (3-year snapshot): log return in TMN = log return in USDT + log return of USDT/TMN.
   Share of the TMN-return variance that the USDT/TMN rate alone explains, per horizon, and the part of each
   coin's total 3-year TMN move that came from the rate.
4. Trend cleanliness: Kaufman efficiency ratio (|net move| / sum of |moves| over 20 bars of 4h) on USDT prices vs
   TMN prices; higher = moves in a straighter line.
Usage: python -I scripts/usdt_vs_tmn.py   (writes reports/experiments/usdt_vs_tmn.json)
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "TRX", "ADA", "PEPE", "SHIB", "LTC", "AVAX", "SUI", "UNI", "AAVE",
         "ARB", "FIL", "APE", "HYPE", "PUMP", "WLD", "SEI", "DYDX", "CAKE", "FLOKI", "SAND", "GALA", "S"]


def fetch_30d():
    from wallex_agent.data.candles import fetch_range
    from wallex_agent.data.wallex_rest import WallexRest

    api, end = WallexRest(), int(time.time()) // 3600 * 3600
    start = end - 30 * 86400
    out = {}
    for s in ["USDTTMN"] + [b + q for b in BASES for q in ("TMN", "USDT")]:
        try:
            df = fetch_range(api, s, start, end)
            out[s] = df.set_index("open_time")[["close", "volume"]] if len(df) else None
        except Exception as e:  # noqa: BLE001 (a missing pair is reported, not fatal)
            out[s] = None
            print(f"{s}: {e}", file=sys.stderr)
    api.close()
    return out


def volume_and_tracking(c):
    fx = c["USDTTMN"]["close"]
    rows = []
    for b in BASES:
        t, u = c.get(b + "TMN"), c.get(b + "USDT")
        vt = float((t["close"] * t["volume"]).sum()) if t is not None else 0.0
        vu = float((u["close"] * u["volume"] * fx.reindex(u.index).ffill()).sum()) if u is not None else 0.0
        track = None
        if t is not None and u is not None:
            j = pd.concat([t["close"], u["close"], fx], axis=1, keys=["t", "u", "fx"]).dropna()
            dev = 100 * (j["u"] / (j["t"] / j["fx"]) - 1)
            track = {"median_abs_%": round(float(dev.abs().median()), 3), "p95_abs_%": round(float(dev.abs().quantile(.95)), 3)}
        rows.append({"coin": b, "tmn_vol_30d_mtmn": round(vt / 1e6), "usdt_vol_30d_mtmn": round(vu / 1e6),
                     "usdt_share_%": round(100 * vu / (vt + vu), 1) if vt + vu else None, "tracking": track})
    return rows


def dollar_share():
    from wallex_agent.data.snapshot import load_snapshot

    candles, _, _ = load_snapshot("ecbb7bb14b3ec827")
    fx = np.log(candles["USDTTMN"].set_index("open_time")["close"])
    res, totals, er = {}, [], {"usdt": [], "tmn": []}
    for h, name in ((1, "1h"), (4, "4h"), (24, "1D"), (168, "1W"), (720, "30D")):
        shares = []
        for s, df in candles.items():
            if not s.endswith("TMN") or s == "USDTTMN" or len(df) < 5000:
                continue
            p = np.log(df.set_index("open_time")["close"])
            j = pd.concat([p, fx], axis=1, keys=["p", "fx"]).dropna()
            j = j[j.index % (h * 3600) == 0] if h > 1 else j
            r = j.diff().dropna()
            if len(r) > 30 and r["p"].var() > 0:
                shares.append(100 * r["fx"].var() / r["p"].var())
        res[name] = {"median_%": round(float(np.median(shares)), 1), "coins": len(shares)}
    for s, df in candles.items():
        if not s.endswith("TMN") or s == "USDTTMN" or len(df) < 5000:
            continue
        p = np.log(df.set_index("open_time")["close"])
        j = pd.concat([p, fx], axis=1, keys=["p", "fx"]).dropna()
        tot, fxm = float(j["p"].iloc[-1] - j["p"].iloc[0]), float(j["fx"].iloc[-1] - j["fx"].iloc[0])
        totals.append({"coin": s[:-3], "tmn_move_%": round(100 * (np.exp(tot) - 1), 1),
                       "usdt_move_%": round(100 * (np.exp(tot - fxm) - 1), 1)})
        b4 = j[j.index % (4 * 3600) == 0]
        for k, series in (("tmn", b4["p"]), ("usdt", b4["p"] - b4["fx"])):
            d = series.diff()
            e = (series.diff(20).abs() / d.abs().rolling(20).sum()).dropna()
            er[k].append(float(e.median()))
    fx_move = float(np.exp(fx.iloc[-1] - fx.iloc[0]) - 1)
    return {"variance_share_of_usdt_rate": res, "usdt_tmn_move_3y_%": round(100 * fx_move, 1),
            "coin_moves_3y": totals,
            "efficiency_ratio_4h_median": {k: round(float(np.median(v)), 4) for k, v in er.items()}}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    c = fetch_30d()
    vol = volume_and_tracking(c)
    ds = dollar_share()
    tv, uv = sum(r["tmn_vol_30d_mtmn"] for r in vol), sum(r["usdt_vol_30d_mtmn"] for r in vol)
    out = {"volume_30d": {"total_tmn_pairs_mtmn": tv, "total_usdt_pairs_mtmn": uv,
                          "usdt_share_%": round(100 * uv / (tv + uv), 1), "by_coin": vol}, **ds}
    (ROOT / "reports" / "experiments" / "usdt_vs_tmn.json").write_text(json.dumps(out, indent=1, ensure_ascii=False),
                                                                      encoding="utf-8")
    print(json.dumps(out, indent=1, ensure_ascii=False))
