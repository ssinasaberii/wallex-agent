"""News gate (spec 09 §7.8, shadow/live only).

Pipeline: English RSS feeds every `news.poll_seconds` -> parse (RSS 2.0 and Atom, stdlib XML) -> de-duplicate by
URL hash and normalised title -> match assets deterministically (names and tickers from config) -> interpret the
headline with Laya (event type, direction, severity, systemic) -> store in SQLite.

Rules (09 §7.8):
  * a negative, severity-3 item from a tier <= 2 source younger than `max_age_hours` blocks new entries on the
    assets it names for `block_asset_hours` after publication;
  * a systemic item (Laya says it threatens the whole market) of the same strength blocks every entry for
    `block_all_hours` (EVENT_SHOCK);
  * an "unclear" answer never blocks (team answer Q-18); positive news never creates a candidate;
  * if no feed was read successfully for `max_feed_silence_min`, new entries are blocked (fail closed).
Headline text is untrusted data: it is only classified, never followed, and it is stored and shown as text.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
FEEDS_FILE = ROOT / "config" / "rss_feeds.yaml"
NEWS_DIR = ROOT / "data" / "news"


@dataclass
class NewsItem:
    item_id: str
    source: str
    tier: int
    url: str
    title: str
    published_at: int          # epoch seconds (feed time; received_at if the feed has none)
    received_at: int
    assets: list[str] = field(default_factory=list)
    english: bool = True
    interp: dict[str, Any] | None = None   # event_type, direction, severity, systemic, probabilities, model

    def age_h(self, now: float) -> float:
        return (now - self.published_at) / 3600


# ---------------------------------------------------------------- parsing
def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _ts(s: str) -> int | None:
    if not s:
        return None
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def clean_title(t: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", " ", t or ""))
    return re.sub(r"\s+", " ", t).strip()[:300]


def parse_feed(xml_text: str) -> list[tuple[str, str, int | None]]:
    """Return (title, link, published_ts) for RSS 2.0 <item> or Atom <entry>."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    out = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = link = date = ""
        for ch in it:
            t = ch.tag.split("}")[-1]
            if t == "title":
                title = _text(ch)
            elif t == "link":
                link = (ch.get("href") or _text(ch)).strip()
            elif t in ("pubDate", "published", "updated", "date") and not date:
                date = _text(ch)
        if title:
            out.append((clean_title(title), link, _ts(date)))
    return out


def norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", t.lower())).strip()


def item_id(url: str, title: str) -> str:
    return hashlib.sha256((url.strip().lower() or norm_title(title)).encode()).hexdigest()[:16]


def is_english(t: str) -> bool:
    letters = [c for c in t if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) > 0.9


# ---------------------------------------------------------------- assets
class AssetMatcher:
    def __init__(self, metas: dict[str, Any], quote: str = "TMN", feeds_cfg: dict | None = None):
        cfg = feeds_cfg or yaml.safe_load(FEEDS_FILE.read_text(encoding="utf-8"))
        ignore = {x.lower() for x in cfg.get("ignore_names", [])}
        names: dict[str, set[str]] = {}
        for m in metas.values():
            if getattr(m, "quote", None) != quote:
                continue
            names.setdefault(m.base, set())
        for base, extra in (cfg.get("extra_aliases") or {}).items():
            names.setdefault(base, set()).update(e.lower() for e in extra)
        self.patterns: list[tuple[str, re.Pattern, bool]] = []
        for base, aliases in names.items():
            for a in sorted(aliases):
                if a in ignore or len(a) < 3:
                    continue
                name = re.compile(r"(?<![A-Za-z0-9])" + re.escape(a) + r"(?![A-Za-z0-9])", re.I)
                self.patterns.append((base, name, False))
            if len(base) >= 3 and base.lower() not in ignore:  # ticker: exact upper case, optional $
                ticker = re.compile(r"(?<![A-Za-z0-9])\$?" + re.escape(base) + r"(?![A-Za-z0-9])")
                self.patterns.append((base, ticker, True))

    def match(self, title: str) -> list[str]:
        return sorted({base for base, p, _ in self.patterns if p.search(title)})


# ---------------------------------------------------------------- store
class NewsStore:
    def __init__(self, cfg_news, path: Path | None = None, feeds_cfg: dict | None = None):
        self.cfg = cfg_news
        fc = feeds_cfg or yaml.safe_load(FEEDS_FILE.read_text(encoding="utf-8"))
        self.sys_strong = float(fc.get("systemic_strong_prob", 0.8))
        self.sys_kw_prob = float(fc.get("systemic_keyword_prob", 0.4))
        self.sys_kw = [re.compile(k, re.I) for k in fc.get("systemic_keywords", [])]
        p = path or (NEWS_DIR / "news.sqlite")
        p.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(p)
        self.db.execute("CREATE TABLE IF NOT EXISTS items "
                        "(id TEXT PRIMARY KEY, norm TEXT, published INTEGER, doc TEXT)")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_norm ON items(norm)")
        self.last_ok_poll: float = 0.0
        self.poll_errors = 0

    def has(self, iid: str, title: str) -> bool:
        row = self.db.execute("SELECT 1 FROM items WHERE id=? OR norm=?", (iid, norm_title(title))).fetchone()
        return row is not None

    def save(self, it: NewsItem) -> None:
        self.db.execute("REPLACE INTO items VALUES (?, ?, ?, ?)",
                        (it.item_id, norm_title(it.title), it.published_at, json.dumps(asdict(it), ensure_ascii=False)))
        self.db.commit()

    def recent(self, since: float) -> list[NewsItem]:
        rows = self.db.execute("SELECT doc FROM items WHERE published >= ? ORDER BY published DESC", (int(since),))
        return [NewsItem(**json.loads(r[0])) for r in rows]

    def pending(self, limit: int = 50) -> list[NewsItem]:
        rows = self.db.execute("SELECT doc FROM items ORDER BY published DESC LIMIT 500")
        out = [NewsItem(**json.loads(r[0])) for r in rows]
        return [x for x in out if x.english and x.interp is None][:limit]

    # ------------------------------------------------------------ polling
    def poll(self, feeds: list[dict], matcher: AssetMatcher, client: httpx.Client | None = None) -> int:
        """Fetch every feed once; store new items (uninterpreted). Returns the number of new items."""
        own = client is None
        client = client or httpx.Client(timeout=15, follow_redirects=True,
                                        headers={"User-Agent": "Mozilla/5.0 wallex-agent-news/0.1"})
        new, ok = 0, 0
        now = int(time.time())
        try:
            for f in feeds:
                try:
                    r = client.get(f["url"])
                    r.raise_for_status()
                    entries = parse_feed(r.text)
                    ok += 1
                except Exception as e:
                    self.poll_errors += 1
                    log.warning("news feed %s failed: %s", f["name"], type(e).__name__)
                    continue
                for title, link, ts in entries:
                    iid = item_id(link, title)
                    if self.has(iid, title):
                        continue
                    pub = ts if ts and ts <= now + 300 else now
                    it = NewsItem(iid, f["name"], int(f["tier"]), link, title, pub, now,
                                  assets=matcher.match(title), english=is_english(title))
                    self.save(it)
                    new += 1
        finally:
            if own:
                client.close()
        if ok:
            self.last_ok_poll = time.time()
        return new

    def interpret_pending(self, interpreter, limit: int = 50) -> int:
        n = 0
        for it in self.pending(limit):
            try:
                it.interp = interpreter.interpret(it.title)
            except Exception as e:  # Laya down: keep pending, retry next poll
                log.warning("news interpretation failed: %s", type(e).__name__)
                break
            self.save(it)
            n += 1
        return n

    # ------------------------------------------------------------ gate
    def _strong_negative(self, it: NewsItem, now: float) -> bool:
        c, i = self.cfg, it.interp or {}
        return (i.get("direction") == "negative" and str(i.get("severity")) == "3" and it.tier <= c.max_block_tier
                and it.age_h(now) <= c.max_age_hours)

    def is_systemic(self, it: NewsItem, now: float) -> bool:
        """Negative, tier <= 2, recent, and (Laya very sure with severity 3, or Laya fairly sure + a keyword)."""
        c, i = self.cfg, it.interp or {}
        if i.get("direction") != "negative" or it.tier > c.max_block_tier or it.age_h(now) > c.block_all_hours:
            return False
        p = float(i.get("systemic_prob", 0.0))
        if p >= self.sys_strong and str(i.get("severity")) == "3":
            return True
        return p >= self.sys_kw_prob and any(k.search(it.title) for k in self.sys_kw)

    def gate(self, base: str, now: float) -> dict[str, Any]:
        c = self.cfg
        if time.time() - self.last_ok_poll > c.max_feed_silence_min * 60:
            return {"blocking_items": [{"reason": "NEWS_UNAVAILABLE",
                                        "detail": f"no feed read for {c.max_feed_silence_min} min"}],
                    "context_items": [], "event_shock": False}
        horizon = max(c.block_asset_hours, c.block_all_hours, c.context_hours)
        items = self.recent(now - horizon * 3600)
        blocking, context, shock = [], [], False
        for it in items:
            if self.is_systemic(it, now):
                shock = True
                blocking.append(_brief(it, "EVENT_SHOCK"))
                continue
            if self._strong_negative(it, now):
                if base in it.assets and it.age_h(now) <= c.block_asset_hours:
                    blocking.append(_brief(it, "ASSET_NEWS"))
                    continue
            if base in it.assets and it.age_h(now) <= c.context_hours:
                context.append(_brief(it, "CONTEXT"))
        return {"blocking_items": blocking, "context_items": context[:5], "event_shock": shock}


def _brief(it: NewsItem, kind: str) -> dict[str, Any]:
    i = it.interp or {}
    return {"kind": kind, "item_id": it.item_id, "source": it.source, "tier": it.tier, "title": it.title,
            "published_at": it.published_at, "assets": it.assets, "event_type": i.get("event_type"),
            "direction": i.get("direction"), "severity": i.get("severity"), "systemic": i.get("systemic")}


def load_feeds() -> list[dict]:
    return yaml.safe_load(FEEDS_FILE.read_text(encoding="utf-8"))["feeds"]
