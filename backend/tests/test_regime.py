from datetime import date, datetime, timedelta

import numpy as np
import pytest

from paper_trade.market import engine as engine_mod
from paper_trade.market.calendar import MARKET_TZ
from paper_trade.market.engine import MarketEngine
from paper_trade.market.regime import STATES, RealRegime, feature_panel, load_params

PARAMS = load_params()
START = datetime(2026, 1, 5, 9, 30, tzinfo=MARKET_TZ)


def test_transition_odds_are_probabilities_and_the_averages_are_neutral() -> None:
    p = PARAMS.transition(np.array([0.5, 2.0, np.log(18.0), 0.1]))
    assert p.shape == (3, 3) and np.allclose(p.sum(axis=1), 1) and (p >= 0).all()
    assert PARAMS.share @ PARAMS.variance_multiple == pytest.approx(1.0)
    assert PARAMS.share @ PARAMS.drift_tilt == pytest.approx(0.0, abs=1e-12)
    assert PARAMS.sigma[0] < PARAMS.sigma[1] < PARAMS.sigma[2]  # ordered calm, correction, crisis


def test_missing_features_count_as_average() -> None:
    p = PARAMS.transition(np.full(4, np.nan))
    assert np.allclose(p, PARAMS.transition(PARAMS.center))


def test_features_use_only_what_was_published_before_the_day() -> None:
    series = {
        "T10Y2Y": [(date(2020, 3, 2), 0.3), (date(2020, 3, 3), 0.4)],
        "BAA10Y": [(date(2020, 3, 2), 2.0)],
        "VIXCLS": [(date(2020, 3, 2), 20.0)],
        "UNRATE": [(date(2019, 3, 1), 4.0), (date(2020, 3, 1), 3.5)],  # March's figure comes out in April
    }
    row = feature_panel([date(2020, 3, 3), date(2020, 4, 20)], series)
    assert row[0, 0] == 0.3 and row[0, 1] == 2.0  # the 3rd sees the 2nd, not the 3rd
    assert np.isnan(row[0, 3])  # no unemployment figure published a year before yet
    assert row[1, 3] == pytest.approx(-0.5)  # by April 20 the March 2020 figure (published ~Apr 5) is known


def test_the_real_past_is_filtered_into_states() -> None:
    days, k = [], date(2020, 1, 1)
    while len(days) < 400:
        if k.weekday() < 5:
            days.append(k)
        k += timedelta(days=1)
    rng = np.random.default_rng(0)
    calm = rng.normal(0.0006, 0.004, 400)
    calm[200:230] = rng.normal(-0.01, 0.03, 30)  # a month-long crash
    closes = 100 * np.exp(np.concatenate([[0.0], np.cumsum(calm)]))[:400]
    series = {"VIXCLS": [(days[0], 15.0)]}
    real = RealRegime(PARAMS, days, closes, series)
    assert real.at(days[1]) is None  # before the first block ends
    assert real.at(days[100])[0] == 0
    assert real.at(days[232])[0] == 2
    assert real.at(days[100])[1].shape == (4,)


def test_states_change_by_period_and_replay_the_same() -> None:
    a, b = MarketEngine(START, seed=3), MarketEngine(START, seed=3)
    a.advance(START + timedelta(days=400))
    b.advance(START + timedelta(days=400))
    days = [d for d in sorted(a._starts) if d <= a.time.date()]
    seq = [a._skeleton(d).state for d in days]
    assert seq == [b._skeleton(d).state for d in days]
    changes = [i for i in range(1, len(seq)) if seq[i] != seq[i - 1]]
    assert all(a._starts[days[i]].phase == 0 for i in changes)  # only at the start of a period
    assert a.regime in STATES


def test_a_triggered_shock_sends_the_market_into_a_crisis() -> None:
    engine = MarketEngine(START, seed=7)
    engine.advance(START + timedelta(days=10))
    day = engine.trigger_shock()
    engine.advance(engine.time + timedelta(days=4))
    assert engine._skeleton(day).state == len(STATES) - 1
    assert engine._skeleton(day).shock > 0


def test_the_regime_is_saved_with_the_market() -> None:
    engine = MarketEngine(START, seed=5)
    engine.advance(START + timedelta(days=120))
    again = MarketEngine.from_state(engine.to_state())
    for d, start in engine._starts.items():
        saved = again._starts[d]
        assert saved.state == start.state and saved.phase == start.phase
        assert (saved.z is None) == (start.z is None)
    assert again.regime == engine.regime


def test_a_state_sets_the_variance_level() -> None:
    v = engine_mod.MarketEngine(START)._state_variance
    assert v[0] < v[1] < v[2]


def test_the_index_can_fall_to_the_real_lows() -> None:
    engine = MarketEngine(START, seed=2)
    engine._starts[START.date()].variance = engine_mod.VARIANCE_RANGE[0]
    index = engine._skeleton(START.date()).vix[0]
    assert 8.5 < index < 11.5  # the real index has bottomed at 9-10; it used to stop near 11.8


def test_a_trigger_sends_the_market_into_a_crisis_even_over_a_stronger_leftover_shock() -> None:
    engine = MarketEngine(START, seed=4)
    day = START.date()
    engine._shocks[day] = (0.01, START)
    engine._starts[day].shock = 0.5  # an earlier shock still fading, stronger than the trigger
    assert engine._skeleton(day).state == len(STATES) - 1


def test_a_backfill_keeps_the_regime_chain() -> None:
    engine = MarketEngine(START, seed=6)
    list(engine.backfill_iter(80))
    starts = [engine._starts[d] for d in sorted(engine._starts) if d < START.date()]
    assert {s.phase for s in starts} == set(range(PARAMS.period))  # the period counter ran on
    assert all(s.z is not None for s in starts[1:])


def test_option_and_index_inputs_use_the_variance_in_force() -> None:
    engine = MarketEngine(START, seed=8)
    sk = engine._skeleton(START.date())
    assert sk.variance == pytest.approx(engine._starts[START.date()].variance * engine._state_variance[sk.state] / engine._state_variance[engine._starts[START.date()].state])
