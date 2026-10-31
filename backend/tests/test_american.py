import math

import numpy as np
import pytest

from paper_trade.market import american


def tree(spot, k, years, vol, rate, right, fraction=0.0, ex=(), steps=1500):
    """A Cox-Ross-Rubinstein tree, American exercise; each dividend cuts the price
    by `fraction` at its ex-date (the nearest step)."""
    dt = years / steps
    u = math.exp(vol * math.sqrt(dt))
    d, disc = 1 / u, math.exp(-rate * dt)
    p = (math.exp(rate * dt) - d) / (u - d)
    cuts = {max(1, round(t / dt)) for t in ex}
    sign = 1 if right == "call" else -1

    def stock(i: int) -> np.ndarray:
        j = np.arange(i + 1)
        return spot * u ** (2 * j - i) * (1 - fraction) ** sum(1 for c in cuts if c <= i)

    value = np.maximum(sign * (stock(steps) - k), 0)
    for i in range(steps - 1, -1, -1):
        value = disc * (p * value[1:] + (1 - p) * value[:-1])
        # The ex-date falls between steps i and i+1: values at i are for the price before the cut.
        value = np.maximum(value, sign * (stock(i) - k))
    return float(value[0])


@pytest.mark.parametrize("spot,k,years,vol", [(100, 100, 0.5, 0.25), (100, 120, 0.25, 0.3), (100, 140, 1.0, 0.2), (100, 80, 0.5, 0.4)])
def test_american_put_matches_a_tree(spot, k, years, vol) -> None:
    _, put = american.prices(np.array(spot, float), np.array(k, float), np.array(years), np.array(vol), 0.04)
    assert float(put) == pytest.approx(tree(spot, k, years, vol, 0.04, "put"), abs=0.03, rel=0.015)


def test_american_put_is_worth_more_than_european_and_exercise_when_deep_in_the_money() -> None:
    spot, k, years, vol = 60.0, 100.0, 1.0, 0.2
    call, put = american.prices(np.array(spot), np.array(k), np.array(years), np.array(vol), 0.04)
    assert float(put) >= k - spot  # never below exercising now
    assert float(put) > american.european(spot, k, years, vol, 0.04)[1]


def test_no_dividends_means_the_call_is_european() -> None:
    args = (np.array([[95.0]]), np.array([[90.0, 100.0, 110.0]]), np.array([[0.4]]), np.array([[0.3]]), 0.04)
    assert np.allclose(american.prices(*args)[0], american.european(*args)[0])


@pytest.mark.parametrize("k", [80.0, 95.0])
def test_dividends_cut_calls_and_raise_puts_and_a_deep_call_goes_early(k) -> None:
    spot, years, vol, q, ex = 100.0, 0.5, 0.25, 0.01, 0.2  # a big quarterly yield: Black's lower bound drifts from a tree as q grows
    base_call, base_put = american.prices(np.array(spot), np.array(k), np.array(years), np.array(vol), 0.04)
    call, put = american.prices(np.array(spot), np.array(k), np.array(years), np.array(vol), 0.04, q, (np.array(ex),))
    assert float(call) < float(base_call) and float(put) > float(base_put)
    assert float(call) == pytest.approx(tree(spot, k, years, vol, 0.04, "call", q, (ex,)), abs=0.05, rel=0.01)
    assert float(put) == pytest.approx(tree(spot, k, years, vol, 0.04, "put", q, (ex,)), abs=0.1, rel=0.015)


def test_a_past_ex_date_changes_nothing() -> None:
    args = (np.array([[100.0]]), np.array([[100.0]]), np.array([[0.25]]), np.array([[0.2]]), 0.04)
    assert np.allclose(american.prices(*args, 0.03, (np.array([[-0.01]]),)), american.prices(*args))
