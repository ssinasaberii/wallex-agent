"""Command line entry point: python -m wallex_agent <command>."""

from __future__ import annotations

import argparse
import json
import logging


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="wallex_agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify-api", help="Phase 0 read-only API verification")
    v.add_argument("--v5-rounds", type=int, default=12)
    v.add_argument("--ws-seconds", type=int, default=30)
    sub.add_parser("verify-llm", help="Phase 0 V11: one short BoxAPI call")
    vl = sub.add_parser("verify-laya", help="Phase 0 V10: local laya-serve contract and latency")
    vl.add_argument("--n", type=int, default=50)
    fd = sub.add_parser("fetch-data", help="update candle cache and freeze a hashed snapshot")
    fd.add_argument("--config", default=None)
    fd.add_argument("--top-n", type=int, default=None)
    sh = sub.add_parser("shadow", help="run the shadow test until the challenge end")
    sh.add_argument("--config", default=None)
    sh.add_argument("--session", default=None, help="resume an existing session id")
    # A = rules, B = Laya, C = Laya + news (the trading algorithm); XA/XB (gate off) equal A/B in v1.3
    # LC = live account (LiveBroker; dry run unless config live.dry_run is false AND .env approval is set)
    sh.add_argument("--variants", default="A,B,C")
    sh.add_argument("--once", action="store_true", help="run one decision cycle now and exit (smoke test)")
    db = sub.add_parser("dashboard", help="local control panel on 127.0.0.1 (settings, decisions, trades)")
    db.add_argument("--port", type=int, default=8765)
    db.add_argument("--no-browser", action="store_true")
    bt = sub.add_parser("backtest", help="run the backtest suite")
    bt.add_argument("--config", default=None)
    bt.add_argument("--snapshot", default=None)
    bt.add_argument("--quick", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request would flood the shadow log
    from wallex_agent import secrets

    secrets.install_log_redaction()
    if args.cmd == "verify-api":
        from wallex_agent import verify_api

        summary = verify_api.run(v5_rounds=args.v5_rounds, ws_seconds=args.ws_seconds)
        print(json.dumps(summary, indent=1, default=str, ensure_ascii=False))
    elif args.cmd == "verify-llm":
        from wallex_agent import verify_llm

        print(json.dumps(verify_llm.run(), indent=1, default=str, ensure_ascii=False))
    elif args.cmd == "verify-laya":
        from wallex_agent import verify_laya

        print(json.dumps(verify_laya.run(n=args.n), indent=1, default=str, ensure_ascii=False))
    elif args.cmd == "fetch-data":
        from wallex_agent import fetch_data
        from wallex_agent.config import DEFAULT_CONFIG, load_config

        cfg = load_config(args.config or DEFAULT_CONFIG)
        print(json.dumps(fetch_data.run(cfg, args.top_n), indent=1, default=str))
    elif args.cmd == "dashboard":
        from wallex_agent.dashboard.server import serve

        serve(args.port, not args.no_browser)
    elif args.cmd == "shadow":
        import time

        from wallex_agent.config import DEFAULT_CONFIG, load_config
        from wallex_agent.shadow.runner import ShadowRunner

        cfg = load_config(args.config or DEFAULT_CONFIG)
        r = ShadowRunner(cfg, args.session, tuple(args.variants.split(",")))
        if args.once:
            r.poll_depth()
            r.poll_trades()
            r.poll_news()
            r.cycle(int(time.time()) // 3600 * 3600)
            r.flush_recordings()
            print(json.dumps({"session": r.session, "dir": str(r.dir)}, indent=1))
        else:
            r.run()
    elif args.cmd == "backtest":
        from wallex_agent.backtest import suite
        from wallex_agent.config import DEFAULT_CONFIG, load_config

        cfg = load_config(args.config or DEFAULT_CONFIG, apply_env=False)
        print(json.dumps(suite.run(cfg, args.config or str(DEFAULT_CONFIG), args.snapshot, quick=args.quick),
                         indent=1, default=str))
    return 0
