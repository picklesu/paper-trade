from datetime import date, datetime, timedelta

import pytest

from paper_trade.market.calendar import MARKET_TZ, is_trading_day, session_open
from paper_trade.market.engine import MarketEngine
from paper_trade.market.history import History
from paper_trade.store import Store


def et(*args: int) -> datetime:
    return datetime(*args, tzinfo=MARKET_TZ)


START = et(2026, 1, 5, 9, 30)
NOW = et(2026, 3, 4, 11, 7, 30)


def make(days: int = 60, now: datetime = NOW) -> History:
    engine = MarketEngine(START)
    history = History(Store(":memory:").connection, engine)
    engine.on_day_complete = history.record
    with history.batch():
        for _ in history.backfill_iter(days):
            pass
    engine.advance(now)
    return history


def times(page: dict) -> list[int]:
    return [b[0] for b in page["bars"]]


@pytest.fixture(scope="module")
def h() -> History:
    return make()


@pytest.mark.parametrize(
    ("interval", "step"),
    [("1m", 60), ("5m", 300), ("15m", 900), ("30m", 1800), ("1h", 3600)],
)
def test_intraday_bars_align_to_the_open(h: History, interval: str, step: int) -> None:
    page = h.bars("SPY", interval, count=50)
    ts = times(page)
    assert ts == sorted(ts) and len(ts) == 50
    opens = {int(session_open(d).timestamp()) for d in h.engine._starts}
    for t in ts:
        open_ = max(o for o in opens if o <= t)
        assert (t - open_) % step == 0


def test_bars_are_consistent(h: History) -> None:
    for interval in ("1m", "30m", "1D", "1W"):
        for _, o, hi, lo, c, v in h.bars("AAPL", interval, count=200)["bars"]:
            assert lo <= min(o, c) and hi >= max(o, c) and v >= 0


def test_live_bar_matches_the_quote(h: History) -> None:
    q = h.engine.quotes["NVDA"]
    for interval in ("1m", "5m", "30m", "1h", "1D"):
        last = h.bars("NVDA", interval, count=3)["bars"][-1]
        assert last[4] == round(q.last, 2)
    day = h.bars("NVDA", "1D", count=1)["bars"][-1]
    assert day[1:5] == [round(q.open, 2), round(q.high, 2), round(q.low, 2), round(q.last, 2)]
    assert day[5] == pytest.approx(q.volume, abs=3)


def test_daily_bars_cover_history_and_match_closes(h: History) -> None:
    page = h.bars("SPY", "1D", count=2000)
    assert page["more"] is False
    assert len(page["bars"]) == len(h.engine._starts)
    # Each day's close is the next day's previous close.
    days = sorted(h.engine._starts)
    for (d, nxt), bar in zip(zip(days, days[1:]), page["bars"]):
        assert bar[4] == round(float(h.engine._starts[nxt].prev_close[h.engine.symbol_index("SPY")]), 2)


def test_paging_back_has_no_gaps_or_duplicates(h: History) -> None:
    for interval, count in (("5m", 200), ("30m", 100), ("1D", 20), ("1W", 3)):
        everything = times(h.bars("SPY", interval, count=2000))
        paged: list[int] = []
        page = h.bars("SPY", interval, count=count)
        paged = times(page) + paged
        while page["more"] and page["bars"]:
            end = datetime.fromtimestamp(paged[0], MARKET_TZ)
            page = h.bars("SPY", interval, end=end, count=count)
            paged = times(page) + paged
        tail = everything[-len(paged) :] if interval in ("5m", "30m") else everything
        assert paged[-len(tail) :] == tail, interval
        assert len(set(paged)) == len(paged)


def test_weekly_bars_are_whole_weeks(h: History) -> None:
    weeks = times(h.bars("SPY", "1W", count=100))
    for t in weeks[1:]:
        # Each starts at its week's first session: Monday, or later after a holiday.
        day = datetime.fromtimestamp(t, MARKET_TZ).date()
        monday = day - timedelta(days=day.weekday())
        assert all(not is_trading_day(monday + timedelta(days=i)) for i in range(day.weekday())), day


def test_fine_bars_stop_at_their_lookback(h: History) -> None:
    bars: list = []
    page = h.bars("SPY", "1m", count=2000)
    bars = page["bars"] + bars
    while page["more"]:
        page = h.bars("SPY", "1m", end=datetime.fromtimestamp(bars[0][0], MARKET_TZ), count=2000)
        bars = page["bars"] + bars
    days = {datetime.fromtimestamp(b[0], MARKET_TZ).date() for b in bars}
    assert len(days) == 60  # FINE_LOOKBACK_DAYS["1m"], not all of history
    assert len(bars) == len({b[0] for b in bars})


def test_replay_rewind_hides_later_stored_days() -> None:
    h = make(5)
    h.engine.rewind(et(2026, 2, 2, 10, 0), new_future=False)
    last = h.bars("SPY", "1D", count=5)["bars"][-1]
    assert last[0] == int(session_open(date(2026, 2, 2)).timestamp())
    # At exactly 10:00 the live 30-minute bar is the one that just started.
    assert h.bars("SPY", "30m", count=1)["bars"][-1][0] == int(et(2026, 2, 2, 10, 0).timestamp())


def test_new_future_regenerates_later_days() -> None:
    h = make(5)
    before = h.bars("SPY", "1D", count=2000)["bars"]
    h.engine.rewind(et(2026, 2, 2, 10, 0), new_future=True)
    h.delete_from(date(2026, 2, 2))
    h.engine.advance(NOW)
    after = h.bars("SPY", "1D", count=2000)["bars"]
    feb2 = int(session_open(date(2026, 2, 2)).timestamp())
    assert [b for b in before if b[0] < feb2] == [b for b in after if b[0] < feb2]
    assert [b for b in before if b[0] > feb2] != [b for b in after if b[0] > feb2]


def test_missing_days_are_found_and_batches_are_visible() -> None:
    engine = MarketEngine(START)
    engine.advance(et(2026, 1, 9, 10, 0))
    h = History(Store(":memory:").connection, engine)
    assert h.missing_days() == [date(2026, 1, d) for d in (5, 6, 7, 8)]
    with h.batch():
        for d in h.missing_days():
            h.record(d)
        assert len(h.bars("SPY", "1D", count=10)["bars"]) == 5  # pending rows count too
    assert h.missing_days() == []


def test_rejects_unknown_interval(h: History) -> None:
    with pytest.raises(ValueError):
        h.bars("SPY", "2m")


def test_bars_endpoint(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from paper_trade.main import create_app

    with TestClient(create_app(history_days=5, db_path=tmp_path / "b.db")) as c:
        body = c.get("/api/bars", params={"symbol": "SPY", "interval": "1D", "count": 10}).json()
        assert body["bars"] and len(body["bars"][0]) == 6
        assert c.get("/api/bars", params={"symbol": "NOPE"}).status_code == 422
        assert c.get("/api/bars", params={"symbol": "SPY", "interval": "7m"}).status_code == 422
        clock = c.get("/api/clock").json()
        assert {"session_open", "timeline", "building_history"} <= set(clock)


def test_weekend_end_returns_fridays_bars(h: History) -> None:
    saturday = et(2026, 2, 28, 12, 0)
    page = h.bars("SPY", "5m", end=saturday, count=5)
    last = datetime.fromtimestamp(page["bars"][-1][0], MARKET_TZ)
    assert last.date() == date(2026, 2, 27) and last.hour == 15 and last.minute == 55


@pytest.mark.parametrize("interval", ["5m", "30m", "1h", "1D", "1W"])
def test_bars_before_a_mid_session_end_never_include_later_prices(h: History, interval: str) -> None:
    end = et(2026, 2, 25, 11, 47)  # a Wednesday, mid-morning
    page = h.bars("SPY", interval, end=end, count=50)
    durations = {"5m": 300, "30m": 1800, "1h": 3600}
    for t, *_ in page["bars"]:
        if interval in durations:
            assert t + durations[interval] <= end.timestamp()
        else:  # the day (or week) containing `end` isn't finished, so it's left out
            assert datetime.fromtimestamp(t, MARKET_TZ).date() < (date(2026, 2, 25) if interval == "1D" else date(2026, 2, 23))


def test_more_is_false_when_everything_fits(h: History) -> None:
    assert h.bars("SPY", "1W", count=500)["more"] is False
    assert h.bars("SPY", "1D", count=5)["more"] is True
