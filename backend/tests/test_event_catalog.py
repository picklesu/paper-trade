import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import event_catalog as ec  # noqa: E402

from paper_trade.market.calendar import is_trading_day  # noqa: E402

CATEGORIES = {"pandemic", "credit", "policy", "trade", "geopolitical", "political", "data", "technical", "bubble", "other"}


def test_the_catalog_is_well_formed() -> None:
    days = [row[0] for row in ec.CATALOG]
    assert days == sorted(days) and len(set(days)) == len(days)  # dated, in order, once each
    assert all(is_trading_day(d) for d in days)
    assert all(category in CATEGORIES and sure in {"clear", "probable", "ambiguous"} and text for _, category, sure, text in ec.CATALOG)


def test_measure_reads_a_synthetic_shock() -> None:
    import numpy as np
    from datetime import date, timedelta

    n = 300
    days = [date(2000, 1, 3) + timedelta(days=i) for i in range(n)]
    rng = np.random.default_rng(0)
    ret = rng.normal(0, 0.01, n)
    ret[100] = -0.06  # a 6-sigma day, then three loud days
    ret[101:104] = rng.normal(0, 0.03, 3)
    close = 100 * np.exp(np.cumsum(ret))
    volume = np.full(n, 1e6)
    volume[100] = 4e6
    vix = {d: 15.0 for d in days}
    vix[days[100]] = 30.0
    m = ec.measure(100, days, close, volume, vix, ret)
    assert abs(m["ret"] + 0.06) < 1e-9 and m["volume"] == 4.0 and abs(m["vix_jump"] - 1.0) < 1e-9 and m["drawdown"] < -0.05
    assert 5 <= m["persist"] < 40  # the loud days pass, the 10-day volatility settles
    ret[n - 19 :] = rng.normal(0, 0.05, 19)  # still loud when the data ends
    close = 100 * np.exp(np.cumsum(ret))
    tail = ec.measure(n - 20, days, close, volume, vix, ret)
    assert tail["persist"] is None  # too near the end of the data to know
