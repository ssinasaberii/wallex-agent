"""Phase 0: read-only verification of the Wallex API (spec 09 §4, V1-V9).

Run: python -m wallex_agent verify-api
Writes raw evidence to data/snapshots/verify_<utc>/ and prints a JSON summary.
No order, OTC or withdrawal endpoint is reachable from here (see wallex_rest.ALLOWED_PATHS).
"""

from __future__ import annotations

import itertools
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wallex_agent import secrets
from wallex_agent.data.wallex_rest import WallexRest

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
RES_SECONDS = {"15": 900, "60": 3600, "240": 14400, "1D": 86400}


def _now() -> int:
    return int(time.time())


def _f(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- V1 markets
def v1_markets(api: WallexRest, out: Path) -> dict[str, Any]:
    v1 = api.get_json("/v1/markets")
    hector = api.get_json("/hector/web/v1/markets")
    (out / "v1_markets.json").write_text(json.dumps(v1, ensure_ascii=False), encoding="utf-8")
    (out / "hector_markets.json").write_text(json.dumps(hector, ensure_ascii=False), encoding="utf-8")
    syms = v1["result"]["symbols"]
    hmap = {m["symbol"]: m for m in hector["result"]["markets"]}
    rows = []
    for sym, m in syms.items():
        h = hmap.get(sym, {})
        st = m.get("stats", {})
        bid, ask = _f(st.get("bidPrice")), _f(st.get("askPrice"))
        mid = (bid + ask) / 2 if bid and ask else None
        rows.append({
            "symbol": sym, "base": m.get("baseAsset"), "quote": m.get("quoteAsset"),
            "is_spot": h.get("is_spot"), "in_hector": bool(h),
            "minNotional": m.get("minNotional"), "minQty": m.get("minQty"),
            "stepSize": m.get("stepSize"), "tickSize": m.get("tickSize"),
            "baseAssetPrecision": m.get("baseAssetPrecision"), "quotePrecision": m.get("quotePrecision"),
            "amount_precision": h.get("amount_precision"), "price_precision": h.get("price_precision"),
            "is_market_type_enable": h.get("is_market_type_enable", m.get("isMarketTypeEnable")),
            "is_zero_fee": h.get("is_zero_fee", m.get("isZeroFee")),
            "bid": bid, "ask": ask,
            "spread_pct": (ask - bid) / mid * 100 if mid else None,
            "tmn_volume_24h": _f(st.get("24h_tmnVolume")) or _f(h.get("quote_volume_24h")),
            "last": _f(st.get("lastPrice")),
        })
    tmn_spot = [r for r in rows if r["quote"] == "TMN" and r["is_spot"]]
    tmn_spot.sort(key=lambda r: -(r["tmn_volume_24h"] or 0))
    (out / "tmn_spot_markets.json").write_text(json.dumps(tmn_spot, indent=1), encoding="utf-8")
    # stepSize / tickSize: are they decimal counts or increments?
    agree_step = sum(1 for r in rows if r["stepSize"] == r["amount_precision"])
    agree_tick = sum(1 for r in rows if r["tickSize"] == r["price_precision"])
    return {
        "n_v1": len(syms), "n_hector": len(hmap), "n_tmn_spot": len(tmn_spot),
        "n_spot_not_in_v1": len([s for s, h in hmap.items() if h.get("is_spot") and s not in syms]),
        "step_equals_amount_precision": f"{agree_step}/{len(rows)}",
        "tick_equals_price_precision": f"{agree_tick}/{len(rows)}",
        "top30_tmn_spot": [{k: r[k] for k in ("symbol", "tmn_volume_24h", "spread_pct", "minNotional", "minQty",
                                              "stepSize", "tickSize", "amount_precision", "price_precision")}
                           for r in tmn_spot[:30]],
        "v1_sample_keys": sorted(next(iter(syms.values())).keys()),
        "hector_sample_keys": sorted(next(iter(hmap.values())).keys()),
    }


# ---------------------------------------------------------------- V2 candles
def _history(api: WallexRest, symbol: str, res: str, frm: int, to: int) -> dict[str, Any]:
    r = api.get("/v1/udf/history", {"symbol": symbol, "resolution": res, "from": frm, "to": to})
    try:
        return r.json() | {"_status": r.status_code}
    except ValueError:
        return {"_status": r.status_code, "_text": r.text[:300]}


def v2_candles(api: WallexRest, out: Path) -> dict[str, Any]:
    now = _now()
    res_out: dict[str, Any] = {}
    for res, sec in RES_SECONDS.items():
        d = _history(api, "BTCTMN", res, now - sec * 50, now)
        (out / f"udf_BTCTMN_{res}.json").write_text(json.dumps(d), encoding="utf-8")
        t = d.get("t") or []
        info: dict[str, Any] = {"status": d.get("_status"), "s": d.get("s"), "keys": sorted(d.keys()), "n": len(t)}
        if t:
            info["first_t"] = t[0]
            info["last_t"] = t[-1]
            info["last_t_iso"] = datetime.fromtimestamp(t[-1], UTC).isoformat()
            info["now"] = now
            info["last_bar_open"] = t[-1] + sec > now  # forming bar returned?
            info["mod_period_offsets"] = sorted({x % sec for x in t})[:5]
            info["mod_3600_offsets"] = sorted({x % 3600 for x in t})[:5]
            diffs = sorted({b - a for a, b in itertools.pairwise(t)})
            info["step_diffs"] = diffs[:6]
        res_out[res] = info
    # Max bars per request: ask for 6000 hours.
    big = _history(api, "BTCTMN", "60", now - 6000 * 3600, now)
    tb = big.get("t") or []
    res_out["max_bars_probe_60"] = {"requested": 6000, "returned": len(tb),
                                    "first_iso": datetime.fromtimestamp(tb[0], UTC).isoformat() if tb else None}
    big15 = _history(api, "BTCTMN", "15", now - 6000 * 900, now)
    res_out["max_bars_probe_15"] = {"requested": 6000, "returned": len(big15.get("t") or [])}
    # Is the last 60m bar still changing? Two calls 25s apart.
    a = _history(api, "BTCTMN", "60", now - 3 * 3600, _now())
    time.sleep(25)
    b = _history(api, "BTCTMN", "60", now - 3 * 3600, _now())
    if a.get("t") and b.get("t"):
        res_out["last_bar_changes_within_25s"] = {
            "same_last_t": a["t"][-1] == b["t"][-1],
            "close_a": a["c"][-1], "close_b": b["c"][-1], "vol_a": a["v"][-1], "vol_b": b["v"][-1],
        }
    return res_out


# ---------------------------------------------------------------- V3 history depth
def _has_data(api: WallexRest, symbol: str, res: str, frm: int, span: int) -> bool:
    d = _history(api, symbol, res, frm, frm + span)
    return bool(d.get("t"))


def v3_depth_of_history(api: WallexRest, symbols: list[str]) -> dict[str, Any]:
    now = _now()
    out: dict[str, Any] = {}
    for sym in symbols:
        out[sym] = {}
        for res in ("60", "240"):
            span = 7 * 86400
            lo, hi = now - 3000 * 86400, now - span  # search window ~8 years
            if _has_data(api, sym, res, lo, span):
                out[sym][res] = {"earliest_days_ago": ">=3000"}
                continue
            if not _has_data(api, sym, res, hi, span):
                out[sym][res] = {"earliest_days_ago": None}
                continue
            while hi - lo > 2 * 86400:
                mid = (lo + hi) // 2
                if _has_data(api, sym, res, mid, span):
                    hi = mid
                else:
                    lo = mid
            out[sym][res] = {"earliest_days_ago": round((now - hi) / 86400, 1)}
        # Gap check over the last 260 days of 1h (in chunks the API accepts).
        out[sym]["gaps_1h_last_260d"] = _gap_check(api, sym, now)
    return out


def _gap_check(api: WallexRest, sym: str, now: int) -> dict[str, Any]:
    ts: list[int] = []
    end = now
    start = now - 260 * 86400
    chunk = 500 * 3600  # udf/history returns at most ~695 bars per request (V2)
    t0 = start
    while t0 < end:
        d = _history(api, sym, "60", t0, min(t0 + chunk, end))
        ts.extend(d.get("t") or [])
        t0 += chunk
    ts = sorted(set(ts))
    if not ts:
        return {"n": 0}
    missing = [(a, b) for a, b in itertools.pairwise(ts) if b - a > 3600]
    return {"n": len(ts), "expected": int((ts[-1] - ts[0]) / 3600) + 1, "gaps": len(missing),
            "largest_gap_h": max(((b - a) // 3600 for a, b in missing), default=0)}


# ---------------------------------------------------------------- V4 depth
def v4_orderbook(api: WallexRest, out: Path) -> dict[str, Any]:
    d = api.get_json("/v1/depth", {"symbol": "BTCTMN"})
    (out / "depth_BTCTMN.json").write_text(json.dumps(d), encoding="utf-8")
    r = d["result"]
    res: dict[str, Any] = {"v1_keys": sorted(r.keys()), "ask_levels": len(r.get("ask", [])),
                           "bid_levels": len(r.get("bid", [])),
                           "level_keys": sorted(r["ask"][0].keys()) if r.get("ask") else None,
                           "ask_sorted_asc": _sorted(r.get("ask", []), asc=True),
                           "bid_sorted_desc": _sorted(r.get("bid", []), asc=False)}
    resp = api.get("/v2/depth/all")
    res["v2_all_status"] = resp.status_code
    try:
        d2 = resp.json()
        (out / "depth_all_v2.json").write_text(json.dumps(d2)[:2_000_000], encoding="utf-8")
        rr = d2.get("result", d2)
        res["v2_all_top_keys"] = sorted(rr.keys())[:10] if isinstance(rr, dict) else type(rr).__name__
        res["v2_all_n_symbols"] = len(rr) if isinstance(rr, dict) else None
        if isinstance(rr, dict) and rr:
            k0 = next(iter(rr))
            v0 = rr[k0]
            res["v2_all_sample_symbol"] = k0
            res["v2_all_sample_shape"] = {k: (len(v) if isinstance(v, list) else type(v).__name__)
                                          for k, v in v0.items()} if isinstance(v0, dict) else type(v0).__name__
    except ValueError:
        res["v2_all_text"] = resp.text[:200]
    return res


def _sorted(levels: list[dict[str, Any]], asc: bool) -> bool:
    p = [float(x["price"]) for x in levels]
    return p == sorted(p, reverse=not asc)


# ---------------------------------------------------------------- V5 isBuyOrder
def v5_trade_side(api: WallexRest, symbols: list[str], rounds: int, out: Path) -> dict[str, Any]:
    """Poll depth then trades; for trades newer than the previous poll, compare price with the
    best bid/ask snapshot taken just before. Aggressor-buy trades should print at/near the ask."""
    seen: set[tuple] = set()
    rows = []
    for _ in range(rounds):
        for sym in symbols:
            dep = api.get_json("/v1/depth", {"symbol": sym})["result"]
            if not dep.get("ask") or not dep.get("bid"):
                continue
            best_ask = min(float(x["price"]) for x in dep["ask"])
            best_bid = max(float(x["price"]) for x in dep["bid"])
            snap_t = time.time()
            tr = api.get_json("/v1/trades", {"symbol": sym})["result"]["latestTrades"]
            for t in tr:
                key = (sym, t["timestamp"], t["price"], t["quantity"], t["isBuyOrder"])
                if key in seen:
                    continue
                seen.add(key)
                ts = datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00")).timestamp()
                if snap_t - ts > 20:  # only trades close to the snapshot
                    continue
                px = float(t["price"])
                mid = (best_ask + best_bid) / 2
                rows.append({"symbol": sym, "isBuyOrder": t["isBuyOrder"], "price": px,
                             "bid": best_bid, "ask": best_ask, "age_s": round(snap_t - ts, 1),
                             "side_vs_mid": "above_mid" if px > mid else ("below_mid" if px < mid else "at_mid"),
                             "at_ask": px >= best_ask, "at_bid": px <= best_bid})
        time.sleep(4)
    (out / "v5_trade_side_samples.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    tab: dict[str, dict[str, int]] = {"true": {}, "false": {}}
    for r in rows:
        k = "true" if r["isBuyOrder"] else "false"
        tab[k][r["side_vs_mid"]] = tab[k].get(r["side_vs_mid"], 0) + 1
    return {"n_trades_compared": len(rows), "isBuyOrder_vs_mid": tab,
            "true_at_ask": sum(1 for r in rows if r["isBuyOrder"] and r["at_ask"]),
            "true_at_bid": sum(1 for r in rows if r["isBuyOrder"] and r["at_bid"]),
            "false_at_ask": sum(1 for r in rows if not r["isBuyOrder"] and r["at_ask"]),
            "false_at_bid": sum(1 for r in rows if not r["isBuyOrder"] and r["at_bid"])}


# ---------------------------------------------------------------- V6 websocket
def v6_websocket(seconds: int, out: Path) -> dict[str, Any]:
    try:
        import socketio  # python-socketio 4.x (EIO=3)
    except ImportError as e:
        return {"status": "client_missing", "error": str(e)}
    events: dict[str, int] = {}
    samples: dict[str, Any] = {}
    sio = socketio.Client(reconnection=False, logger=False, engineio_logger=False)

    @sio.on("Broadcaster")
    def _bc(*args):
        ch = args[0] if args else "?"
        ch = ch if isinstance(ch, str) else json.dumps(ch)[:40]
        events[ch] = events.get(ch, 0) + 1
        if ch not in samples and len(args) > 1:
            samples[ch] = json.dumps(args[1])[:800]

    @sio.on("*")
    def _any(event, *args):
        events[f"event:{event}"] = events.get(f"event:{event}", 0) + 1

    res: dict[str, Any] = {"client": f"python-socketio {socketio.__version__}"}
    try:
        t0 = time.time()
        sio.connect("https://api.wallex.ir", transports=["websocket"])
        res["connect_ms"] = round((time.time() - t0) * 1000)
        res["eio_version"] = getattr(sio.eio, "protocol", None) or "3 (engineio<4)"
        for ch in ("BTCTMN@buyDepth", "BTCTMN@sellDepth", "BTCTMN@trade", "BTCTMN@marketCap"):
            sio.emit("subscribe", {"channel": ch})
        time.sleep(seconds)
        res["status"] = "connected"
    except Exception as e:  # noqa: BLE001
        res["status"] = "error"
        res["error"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            sio.disconnect()
        except Exception:  # noqa: BLE001
            pass
    res["events"] = events
    (out / "v6_ws_samples.json").write_text(json.dumps(samples, indent=1), encoding="utf-8")
    res["sample_channels"] = list(samples)
    return res


# ---------------------------------------------------------------- V7 fee
def v7_fee(api: WallexRest, out: Path) -> dict[str, Any]:
    if not secrets.has("WALLEX_API_KEY"):
        return {"status": "pending", "reason": "WALLEX_API_KEY not set"}
    r = api.get("/v1/account/fee")
    res: dict[str, Any] = {"status_code": r.status_code}
    try:
        d = r.json()
    except ValueError:
        return res | {"status": "error", "text": r.text[:200]}
    (out / "v7_fee.json").write_text(json.dumps(d, indent=1), encoding="utf-8")
    res["status"] = "ok" if r.status_code == 200 else "error"
    rr = d.get("result", d)
    res["top_keys"] = sorted(rr.keys())[:20] if isinstance(rr, dict) else type(rr).__name__
    if isinstance(rr, dict):
        k0 = next(iter(rr), None)
        if k0 is not None:
            res["sample_key"] = k0
            res["sample_value"] = rr[k0]
        for s in ("BTCTMN", "USDTTMN", "ETHTMN"):
            if s in rr:
                res[f"fee_{s}"] = rr[s]
    else:
        res["raw"] = d
    return res


# ---------------------------------------------------------------- main
def run(v5_rounds: int = 12, ws_seconds: int = 30) -> dict[str, Any]:
    secrets.install_log_redaction()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "data" / "snapshots" / f"verify_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    api = WallexRest()
    summary: dict[str, Any] = {"run_utc": stamp}
    steps = [
        ("V1", lambda: v1_markets(api, out)),
        ("V2", lambda: v2_candles(api, out)),
        ("V4", lambda: v4_orderbook(api, out)),
        ("V7", lambda: v7_fee(api, out)),
    ]
    for name, fn in steps:
        try:
            summary[name] = fn()
        except Exception as e:  # noqa: BLE001
            summary[name] = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        log.info("%s done", name)
    top = [r["symbol"] for r in summary.get("V1", {}).get("top30_tmn_spot", [])
           if r["symbol"] not in ("USDTTMN", "USDCTMN", "DAITMN")][:10]
    for name, fn in [("V3", lambda: v3_depth_of_history(api, top + ["BTCUSDT"])),
                     ("V5", lambda: v5_trade_side(api, top[:5], v5_rounds, out)),
                     ("V6", lambda: v6_websocket(ws_seconds, out))]:
        try:
            summary[name] = fn()
        except Exception as e:  # noqa: BLE001
            summary[name] = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        log.info("%s done", name)
    summary["V9"] = {"x_ratelimit_limit": api.rate.limit, "last_remaining": api.rate.remaining,
                     "requests": api.rate.requests, "count_429": api.rate.count_429,
                     "errors": api.rate.errors,
                     "min_remaining_seen": min((int(s["remaining"]) for s in api.rate.samples
                                                if s["remaining"] and s["remaining"].lstrip("-").isdigit()),
                                               default=None)}
    api.close()
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    summary["_out_dir"] = str(out.relative_to(ROOT))
    return summary
