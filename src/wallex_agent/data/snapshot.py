"""Frozen data snapshots for reproducible backtests (spec 09 §5.2).

A snapshot is data/snapshots/<hash>/ with the 1h candle Parquet files, the market metadata JSON and a
manifest. The hash is sha256 over the manifest's file list (name + content sha256), so the same data
always gives the same hash and a judge can verify a backtest input offline.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from wallex_agent.data.candles import CandleStore

ROOT = Path(__file__).resolve().parents[3]
SNAP_DIR = ROOT / "data" / "snapshots"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_snapshot(symbols: list[str], meta_path: Path, store: CandleStore | None = None,
                  note: str = "") -> tuple[str, Path]:
    store = store or CandleStore()
    staging = SNAP_DIR / "_staging"
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "candles").mkdir(parents=True)
    files = []
    ranges = {}
    for s in sorted(symbols):
        src = store.path(s)
        if not src.exists():
            continue
        dst = staging / "candles" / f"{s}.parquet"
        shutil.copyfile(src, dst)
        df = pd.read_parquet(dst, columns=["open_time"])
        ranges[s] = {"bars": len(df), "first_open": int(df["open_time"].min()), "last_open": int(df["open_time"].max())}
        files.append({"path": f"candles/{s}.parquet", "sha256": _sha256(dst)})
    shutil.copyfile(meta_path, staging / "markets.json")
    files.append({"path": "markets.json", "sha256": _sha256(staging / "markets.json")})
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]
    manifest = {"hash": digest, "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                "resolution": "60", "aggregation": "4h/1D built from 1h on UTC boundaries",
                "note": note, "files": files, "ranges": ranges}
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    final = SNAP_DIR / digest
    if final.exists():
        shutil.rmtree(staging)
    else:
        staging.rename(final)
    (SNAP_DIR / "LATEST").write_text(digest, encoding="utf-8")
    return digest, final


def load_snapshot(digest: str) -> tuple[dict[str, pd.DataFrame], Path, dict]:
    d = SNAP_DIR / digest
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    for f in manifest["files"]:
        if _sha256(d / f["path"]) != f["sha256"]:
            raise ValueError(f"snapshot {digest}: {f['path']} does not match its manifest hash")
    candles = {p.stem: pd.read_parquet(p) for p in sorted((d / "candles").glob("*.parquet"))}
    return candles, d / "markets.json", manifest
