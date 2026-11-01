from datetime import UTC, date, datetime, timedelta

import pytest

from paper_trade.macro import SERIES, Macro, parse_observations
from paper_trade import update
from paper_trade.realprices import FetchError, FetchStop
from paper_trade.store import Store

ROWS = [(date(2020, 1, 2), 1.55), (date(2020, 1, 3), 1.54), (date(2020, 1, 6), 1.56)]


def test_parse_leaves_out_missing_days() -> None:
    body = {"observations": [{"date": "2020-01-03", "value": "1.54"}, {"date": "2020-01-01", "value": "."}, {"date": "2020-01-02", "value": "1.55"}]}
    assert parse_observations(body) == [(date(2020, 1, 2), 1.55), (date(2020, 1, 3), 1.54)]
    with pytest.raises(FetchError):
        parse_observations({"error_message": "Bad Request"})


def test_series_and_what_was_known_on_a_day() -> None:
    macro = Macro(Store(":memory:"), "k")
    macro.record("DFF", ROWS)
    assert macro.series("DFF", date(2020, 1, 3)) == ROWS[1:]
    assert macro.latest("DFF", date(2020, 1, 4)) == (date(2020, 1, 3), 1.54)  # a weekend: Friday's
    assert macro.latest("DFF", date(2019, 12, 31)) is None and macro.latest("DGS2", date(2020, 1, 4)) is None


def test_monthly_values_count_only_once_published() -> None:
    macro = Macro(Store(":memory:"), "k")
    macro.record("UNRATE", [(date(2008, 9, 1), 6.1), (date(2008, 10, 1), 6.5)])
    assert macro.latest("UNRATE", date(2008, 10, 15)) is None or macro.latest("UNRATE", date(2008, 10, 15)) == (date(2008, 9, 1), 6.1)
    assert macro.latest("UNRATE", date(2008, 10, 15)) == (date(2008, 9, 1), 6.1)  # October's reading came out in November
    assert macro.latest("UNRATE", date(2008, 11, 7)) == (date(2008, 10, 1), 6.5)
    macro.record("USREC", [(date(2020, 4, 1), 1.0)])
    assert macro.latest("USREC", date(2020, 6, 1)) is None  # named a recession months later


def test_series_are_fetched_once_and_wait_after_a_failure() -> None:
    now = [datetime(2026, 1, 5, tzinfo=UTC)]
    macro = Macro(Store(":memory:"), "k", now=lambda: now[0])
    assert macro.due() == list(SERIES)
    macro.record("DFF", ROWS)
    assert "DFF" not in macro.due()
    now[0] += timedelta(days=3)
    assert "DFF" not in macro.due()  # later days come only on request
    macro.failed("down")
    assert macro.due() == [] and macro.status()["last_error"] == "down"
    now[0] += timedelta(hours=2)
    assert macro.due()


def test_the_update_saves_each_series_and_stops_on_a_refusal() -> None:
    calls = []

    def fetch(series, key):
        calls.append(series)
        if series == "DGS3MO":
            raise FetchStop("FRED rejected the API keys (400 Bad Request)")
        return ROWS

    macro = Macro(Store(":memory:"), "k", fetch=fetch)
    result = update.update_macro(macro, say=lambda m: None)
    assert calls == ["DFF", "DGS3MO"] and set(macro.fetched) == {"DFF"}
    assert "rejected" in macro.status()["last_error"] and macro.status()["available"] == 1
    assert result["macro"] == {"DFF": 3} and "rejected" in result["problems"][0]


def test_a_network_block_is_not_reported_as_a_rejected_key() -> None:
    from paper_trade.realprices import _refusal

    e = _refusal(403, b"Approval required for api.stlouisfed.org:443.\n\nReview and respond with:\n  sbx policy approval ls", "FRED")
    assert isinstance(e, FetchStop) and "network blocked FRED" in str(e) and "rejected" not in str(e)
    assert "rejected the API keys" in str(_refusal(403, b'{"message": "forbidden"}', "FRED"))


def test_one_failing_series_does_not_block_the_others() -> None:
    def fetch(series, key):
        if series == "DGS3MO":
            raise FetchError("FRED answered 500")
        return ROWS

    macro = Macro(Store(":memory:"), "k", fetch=fetch)
    result = update.update_macro(macro, say=lambda m: None)
    assert set(macro.fetched) == set(SERIES) - {"DGS3MO"} and "DGS3MO" in result["problems"][0]


def test_a_bad_fred_key_is_a_stop_with_a_hint() -> None:
    from paper_trade.realprices import _refusal

    e = _refusal(400, b'{"error_code": 400, "error_message": "Bad Request. The value for variable api_key is not registered."}', "FRED")
    assert isinstance(e, FetchStop) and "Check FRED_API_KEY" in str(e)


def test_fetch_new_asks_only_for_days_from_the_last_saved_one() -> None:
    asked = []
    macro = Macro(Store(":memory:"), "k", fetch_since=lambda series, key, since: asked.append(since) or [(date(2020, 1, 6), 1.57), (date(2020, 1, 7), 1.58)])
    macro.record("DFF", ROWS)
    rows = macro.fetch_new("DFF", macro.last_day("DFF"))
    assert asked == [date(2020, 1, 6)] and [d for d, _ in rows] == [date(2020, 1, 6), date(2020, 1, 7)]
    macro.record("DFF", rows)
    assert macro.series("DFF")[-2:] == rows and len(macro.series("DFF")) == 4  # the last day was replaced, not doubled


def test_update_fetches_only_new_days_for_downloaded_series_and_the_rest_in_full() -> None:
    asked = []

    def since(series, key, day):
        asked.append((series, day))
        return [(date(2020, 1, 7), 1.58)]

    macro = Macro(Store(":memory:"), "k", fetch=lambda series, key: ROWS, fetch_since=since)
    macro.record("DFF", ROWS)
    result = update.update_macro(macro, say=lambda m: None)
    assert asked == [("DFF", date(2020, 1, 6))] and result["problems"] == []
    assert result["macro"]["DFF"] == 1 and set(macro.fetched) == set(SERIES)  # the others were downloaded in full
    assert macro.last_day("DFF") == date(2020, 1, 7)


def test_an_earlier_rate_limit_does_not_hold_back_a_manual_update() -> None:
    macro = Macro(Store(":memory:"), "k", fetch=lambda series, key: ROWS)
    macro.failed("FRED says too many requests")  # the app used to wait an hour
    assert macro.due() == []
    update.update_macro(macro, say=lambda m: None)
    assert set(macro.fetched) == set(SERIES)


def test_update_without_keys_says_what_to_add() -> None:
    assert "FRED_API_KEY" in update.update_macro(Macro(Store(":memory:"), None))["problems"][0]
