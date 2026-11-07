from datetime import UTC, date, datetime

import pytest

from paper_trade.intraday import SLOTS
from paper_trade.realprices import Credentials, FetchError, FetchStop
from paper_trade.research import EarningsHistory, Flow, Releases, classify, parse_earnings_history, parse_release_dates
from paper_trade.store import Store

CREDS = Credentials("k", "s")


def test_release_dates_parse_and_save() -> None:
    body = {"release_dates": [{"release_id": 50, "date": "2020-03-06"}, {"date": "2020-02-07"}, {"date": "bad"}, {"date": "2020-03-06"}]}
    assert parse_release_dates(body) == [date(2020, 2, 7), date(2020, 3, 6)]
    with pytest.raises(FetchError):
        parse_release_dates({})
    store = Store(":memory:")
    calls = []

    def fetch(release_id, key):
        calls.append(release_id)
        if release_id == 10:
            raise FetchError("boom")
        return {50: "Employment Situation", 10: "Consumer Price Index", 101: "FOMC Press Release", 53: "Gross Domestic Product", 54: "Personal Income and Outlays", 9: "Advance Retail Sales"}[release_id], [date(2024, 1, 5)]

    releases = Releases(store, "key", fetch)
    problems = releases.update(lambda m: None)
    assert len(calls) == 6 and len(problems) == 1 and "cpi" in problems[0]  # one release failing leaves the others
    assert releases.days("jobs") == [date(2024, 1, 5)] and releases.days("cpi") == []


def test_earnings_history_parses_and_resumes_after_the_daily_limit() -> None:
    body = {"quarterlyEarnings": [
        {"reportedDate": "2024-05-02", "reportTime": "post-market", "reportedEPS": "1.53", "surprisePercentage": "4.1"},
        {"reportedDate": "2024-02-01", "reportTime": "pre-market", "reportedEPS": "None", "surprisePercentage": "None"},
        {"reportedDate": "oops"},
    ]}  # fmt: skip
    rows = parse_earnings_history(body)
    assert rows == [(date(2024, 2, 1), "pre", None, None), (date(2024, 5, 2), "post", 1.53, 4.1)]
    with pytest.raises(FetchStop):
        parse_earnings_history({"Information": "rate limit of 25 requests per day"})
    store = Store(":memory:")
    answers = {"AAPL": rows, "MSFT": FetchStop("limit"), "KO": rows}

    def fetch(symbol, key):
        a = answers[symbol]
        if isinstance(a, Exception):
            raise a
        return a

    history = EarningsHistory(store, "k", fetch)
    problems = history.update(["AAPL", "MSFT", "KO"], lambda m: None)
    assert len(problems) == 1 and history.days("AAPL") == [date(2024, 2, 1), date(2024, 5, 2)] and history.days("KO") == []
    assert history.due(["AAPL", "MSFT", "KO"]) == ["MSFT", "KO"]  # resumes where it stopped


def test_tick_test_classifies_buys_and_sells_by_minute_and_continues_across_pages() -> None:
    def trade(minute: int, price: float, size: int) -> dict:
        return {"t": f"2020-03-16T{13 + (30 + minute) // 60}:{(30 + minute) % 60:02d}:00Z", "p": price, "s": size}  # 9:30 New York is 13:30 UTC in March 2020 (EDT)

    day = date(2020, 3, 16)
    days, price, side = classify([trade(0, 100.0, 5), trade(0, 100.1, 10), trade(1, 100.1, 7), trade(1, 100.0, 3)])
    buy, sell = days[day]
    assert buy[0] == 10 and buy[1] == 7 and sell[1] == 3 and buy[0] + sell[0] == 10  # the first print has no direction; a zero tick keeps the side
    more, _, _ = classify([trade(2, 100.0, 4)], price, side)  # next page: 100.0 against 100.0 keeps the last side (sell)
    assert more[day][1][2] == 4
    outside, _, _ = classify([{"t": "2020-03-16T12:00:00Z", "p": 99.0, "s": 5}], 100.0, 1)
    assert outside == {}  # before the open


def test_flow_saves_each_day_and_keeps_what_came_before_a_limit() -> None:
    store = Store(":memory:")
    day1, day2, day3 = date(2020, 3, 9), date(2020, 3, 12), date(2020, 3, 16)

    def fetch(symbol, day, creds, now, partial=None, checkpoint=None):
        if day == day3:
            raise FetchStop("rate limit")
        buy, sell = [0] * SLOTS, [0] * SLOTS
        buy[0], sell[0] = 7, 3
        return buy, sell

    flow = Flow(store, CREDS, fetch)
    problems = flow.update("SPY", (day1, day2, day3), datetime(2026, 1, 1, tzinfo=UTC), lambda m: None)
    assert len(problems) == 1 and flow.saved("SPY") == {day1, day2}
    assert flow.day("SPY", day1)[0][0] == 7 and flow.day("SPY", day1)[1][0] == 3
    assert flow.update("SPY", (day1, day2), datetime(2026, 1, 1, tzinfo=UTC), lambda m: None) == []  # nothing left to fetch


def test_a_wrong_release_id_is_refused_and_conditions_are_skipped_and_a_stopped_day_resumes() -> None:
    store = Store(":memory:")
    releases = Releases(store, "k", lambda i, k: ("Something Else Entirely", [date(2024, 1, 5)]))
    problems = releases.update(lambda m: None)
    assert len(problems) == 6 and releases.days("jobs") == []  # nothing saved under a wrong label

    # an odd lot, an out-of-sequence print and a closing cross neither count nor set the direction
    trades = [
        {"t": "2020-03-16T13:31:00Z", "p": 100.0, "s": 5, "c": ["@"]},
        {"t": "2020-03-16T13:31:01Z", "p": 101.0, "s": 9, "c": ["@"]},
        {"t": "2020-03-16T13:31:02Z", "p": 90.0, "s": 400, "c": ["Z"]},
        {"t": "2020-03-16T13:31:03Z", "p": 100.5, "s": 7, "c": ["I"]},
        {"t": "2020-03-16T13:31:04Z", "p": 100.5, "s": 6, "c": ["@"]},  # below 101: a sell
    ]
    days, _, _ = classify(trades)
    buy, sell = days[date(2020, 3, 16)]
    assert buy[1] == 9 and sell[1] == 6

    store2 = Store(":memory:")
    seen = []

    def fetch(symbol, day, creds, now, partial=None, checkpoint=None):
        seen.append(partial)
        if partial is None:
            checkpoint(("t2", 100.0, 1, [1] + [0] * (SLOTS - 1), [0] * SLOTS))
            raise FetchError("dropped")
        return [partial[3][0] + 5] + [0] * (SLOTS - 1), [0] * SLOTS

    flow = Flow(store2, CREDS, fetch)
    day = date(2020, 3, 16)
    assert flow.update("SPY", (day,), datetime(2026, 1, 1, tzinfo=UTC), lambda m: None)
    assert flow.update("SPY", (day,), datetime(2026, 1, 1, tzinfo=UTC), lambda m: None) == []
    assert seen[1][0] == "t2" and flow.day("SPY", day)[0][0] == 6  # the second run resumed from the page it had reached


def test_a_per_second_limit_waits_and_retries_but_the_daily_limit_stops() -> None:
    from paper_trade.research import Burst

    with pytest.raises(Burst):
        parse_earnings_history({"Information": "Please spread out your requests more sparingly (1 request per second). The daily limit is 25."})
    rows = [(date(2024, 5, 2), "post", 1.5, 4.0)]
    calls, waits = [], []

    def fetch(symbol, key):
        calls.append(symbol)
        if symbol == "AAPL" and calls.count("AAPL") < 3:
            raise Burst("fast")
        if symbol == "KO":
            raise FetchStop("daily limit")
        return rows

    history = EarningsHistory(Store(":memory:"), "k", fetch, sleep=waits.append)
    problems = history.update(["AAPL", "KO", "MSFT"], lambda m: None)
    assert calls == ["AAPL", "AAPL", "AAPL", "KO"] and history.days("AAPL") == [date(2024, 5, 2)]  # two bursts retried, then saved
    assert len(problems) == 1 and "MSFT" in history.due(["AAPL", "KO", "MSFT"]) and len(waits) >= 3  # the daily limit stopped the run; pauses between requests
