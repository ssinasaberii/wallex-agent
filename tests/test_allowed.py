from wallex_agent.data.allowed import MarketRules, load_rules


def test_denylist_from_challenge_rules():
    r = load_rules()
    assert r.mode == "denylist"
    for blocked in ("ZECTMN", "XMRTMN", "DASHTMN", "QNTTMN", "ORCATMN", "1BBABYDOGETMN", "PAXGTMN"):
        assert not r.is_allowed(blocked), blocked
    for ok in ("BTCTMN", "ETHTMN", "PEPETMN", "SOLTMN", "DOGETMN", "XRPTMN"):
        assert r.is_allowed(ok), ok


def test_modes():
    assert MarketRules("allowlist", {"BTCTMN"}, "").is_allowed("BTCTMN")
    assert not MarketRules("allowlist", {"BTCTMN"}, "").is_allowed("ETHTMN")
    assert MarketRules("all", set(), "").is_allowed("ANY")
