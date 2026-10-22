from wallex_agent.data.quality import missing_pct, passes_quality

from .conftest import H1, make_ohlcv


def test_complete_window_has_no_missing():
    df = make_ohlcv(1000)
    now = int(df["close_time"].iloc[-1])
    assert missing_pct(df, now, 720) == 0.0
    assert passes_quality(df, now, 720, 2.0)


def test_thin_pair_with_many_gaps_is_rejected():
    gaps = tuple(range(500, 530))  # 30 missing hours inside the last 720
    df = make_ohlcv(1000, gaps=gaps)
    now = int(make_ohlcv(1000)["close_time"].iloc[-1])
    assert abs(missing_pct(df, now, 720) - 100 * 30 / 720) < 1e-9
    assert not passes_quality(df, now, 720, 2.0)


def test_only_trailing_window_counts():
    df = make_ohlcv(2000, gaps=tuple(range(100, 400)))  # old gaps, outside the last 720 hours
    now = int(df["close_time"].iloc[-1])
    assert passes_quality(df, now, 720, 2.0)


def test_short_history_counts_as_missing():
    df = make_ohlcv(300)
    now = int(df["close_time"].iloc[-1])
    assert missing_pct(df, now, 720) > 50
    assert not passes_quality(df, now, 720, 2.0)


def test_future_bars_are_ignored():
    df = make_ohlcv(1000)
    now = int(df["close_time"].iloc[700])
    assert missing_pct(df, now, 720) == 100 * (720 - 701) / 720
    assert missing_pct(df, now + H1 - 1, 720) == missing_pct(df, now, 720)  # mid-hour: same closed bars
