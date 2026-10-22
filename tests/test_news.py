"""News gate rules (spec 09 §7.8; team answer Q-18)."""

import time
from types import SimpleNamespace

import pytest

from wallex_agent.config import load_config
from wallex_agent.context.news import AssetMatcher, NewsItem, NewsStore, item_id, parse_feed

CFG = load_config(apply_env=False).news
NOW = time.time()

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Major exchange hacked, $400M in ETH drained</title><link>https://x.test/a</link>
<pubDate>Thu, 22 Oct 2026 18:00:00 GMT</pubDate></item>
<item><title><![CDATA[Solana &amp; XRP rally as ETF flows return]]></title><link>https://x.test/b</link></item>
</channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Cardano upgrade ships</title>
<link href="https://y.test/c"/><updated>2026-10-22T10:00:00Z</updated></entry></feed>"""


def metas(*bases):
    return {f"{b}TMN": SimpleNamespace(base=b, quote="TMN") for b in bases}


@pytest.fixture
def store(tmp_path):
    s = NewsStore(CFG, tmp_path / "news.sqlite")
    s.last_ok_poll = time.time()
    return s


def item(title, assets, direction="negative", severity="3", tier=1, age_h=1.0, systemic=False, sp=0.1):
    return NewsItem(item_id("https://n/" + title, title), "Src", tier, "https://n/" + title, title,
                    int(NOW - age_h * 3600), int(NOW), assets, True,
                    {"event_type": "security_incident", "direction": direction, "severity": severity,
                     "systemic": systemic, "systemic_prob": sp})


def test_parse_rss_and_atom():
    r = parse_feed(RSS)
    assert r[0][0].startswith("Major exchange hacked") and r[0][2] is not None
    assert r[1][0] == "Solana & XRP rally as ETF flows return"
    a = parse_feed(ATOM)
    assert a[0] == ("Cardano upgrade ships", "https://y.test/c", a[0][2]) and a[0][2] is not None


def test_asset_matching_names_and_tickers():
    m = AssetMatcher(metas("BTC", "ETH", "SOL", "XRP", "ADA", "S"))
    assert m.match("Major exchange hacked, $400M in ETH drained") == ["ETH"]
    assert m.match("Solana & XRP rally as ETF flows return") == ["SOL", "XRP"]
    assert m.match("Cardano upgrade ships") == ["ADA"]
    assert m.match("Bitcoin slips") == ["BTC"]
    assert m.match("a sonic boom in markets") == []          # ignored generic name / 1-letter ticker
    assert m.match("ethics in finance") == []                # whole words only


def test_strong_negative_blocks_named_asset_only(store):
    store.save(item("ETH exploit", ["ETH"]))
    assert store.gate("ETH", NOW)["blocking_items"]
    g = store.gate("BTC", NOW)
    assert not g["blocking_items"] and not g["event_shock"]


@pytest.mark.parametrize("kw", [{"direction": "positive"}, {"direction": "unclear"}, {"severity": "2"},
                                {"severity": "unclear"}, {"tier": 3}, {"age_h": 7.0}])
def test_weaker_items_never_block(store, kw):
    store.save(item("ETH story " + str(kw), ["ETH"], **kw))
    assert not store.gate("ETH", NOW)["blocking_items"]


def test_block_expires_after_window(store):
    store.save(item("old ETH exploit", ["ETH"], age_h=5.9))
    assert store.gate("ETH", NOW)["blocking_items"]
    assert not store.gate("ETH", NOW + 7 * 3600)["blocking_items"]  # older than max_age_hours now


def test_systemic_event_blocks_everything(store):
    store.save(item("Binance halts all withdrawals after $2 billion hot wallet hack", [], systemic=True, sp=0.83))
    g = store.gate("ANY", NOW)
    assert g["event_shock"] and g["blocking_items"][0]["kind"] == "EVENT_SHOCK"


def test_low_confidence_systemic_does_not_shock(store):
    store.save(item("Lightning Labs Urges Immediate Updates Over Certificate Vulnerability", [], systemic=True,
                    sp=0.68))
    assert not store.gate("ANY", NOW)["event_shock"]


@pytest.mark.parametrize("title", ["Tether loses its dollar peg as USDT trades at $0.91",
                                   "Coinbase suspends trading after major security breach",
                                   "Ethereum network halts block production for six hours"])
def test_keyword_confirms_systemic_at_lower_confidence(store, title):
    store.save(item(title, [], severity="unclear", sp=0.45))
    assert store.gate("ANY", NOW)["event_shock"]


def test_systemic_needs_negative_direction(store):
    store.save(item("Coinbase suspends trading for scheduled upgrade", [], direction="neutral", sp=0.9))
    assert not store.gate("ANY", NOW)["event_shock"]


def test_feed_silence_fails_closed(store):
    store.last_ok_poll = time.time() - (CFG.max_feed_silence_min + 1) * 60
    assert store.gate("BTC", NOW)["blocking_items"][0]["reason"] == "NEWS_UNAVAILABLE"


def test_related_headlines_are_context(store):
    store.save(item("ETH devs schedule upgrade", ["ETH"], direction="positive", severity="2"))
    g = store.gate("ETH", NOW)
    assert not g["blocking_items"] and g["context_items"][0]["title"] == "ETH devs schedule upgrade"


def test_dedupe_by_url_and_title(store):
    it = item("Same headline", ["BTC"])
    store.save(it)
    assert store.has(it.item_id, "Same headline")
    assert store.has("other-id", "same   HEADLINE!")
