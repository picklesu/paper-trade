import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fit_intraday_volume as fit  # noqa: E402


def days(n: int, close_heavy: float, seed: int = 0) -> np.ndarray:
    """n sessions whose last minutes carry `close_heavy` extra weight."""
    rng = np.random.default_rng(seed)
    shape = np.ones(fit.MINUTES)
    shape[-5:] *= close_heavy
    raw = shape * rng.lognormal(0, 0.3, (n, fit.MINUTES))
    return raw / raw.sum(axis=1, keepdims=True)


def test_profiles_sum_to_one_and_follow_each_states_data() -> None:
    S = np.vstack([days(300, 1.0), days(300, 6.0, 1), days(200, 1.0, 2)])
    st = np.array([0] * 300 + [1] * 300 + [2] * 200)
    out = fit.fit_symbol(S, st)
    profile = np.array(out["profile"])
    assert np.allclose(profile.sum(axis=1), 1, atol=1e-5)
    close = profile[:, -5:].sum(axis=1)
    assert close[1] > 3 * close[0]  # the state with a heavy close shows it
    assert out["days"] == [300, 300, 200]


def test_a_state_with_few_days_leans_on_the_calmer_one() -> None:
    S = np.vstack([days(500, 1.0), days(5, 8.0, 1)])
    st = np.array([0] * 500 + [2] * 5)
    profile = np.array(fit.fit_symbol(S, st)["profile"])
    assert profile[2, -5:].sum() < 3 * profile[0, -5:].sum()  # shrunk (a day count of 5 against a prior of 100)
    assert profile[2, -5:].sum() > profile[0, -5:].sum()


def test_the_day_to_day_mode_points_at_the_close_when_days_differ_there() -> None:
    rng = np.random.default_rng(3)
    heavy = rng.lognormal(0, 0.7, 600)
    shape = np.ones((600, fit.MINUTES))
    shape[:, -5:] *= heavy[:, None]
    S = shape * rng.lognormal(0, 0.1, shape.shape)
    S /= S.sum(axis=1, keepdims=True)
    out = fit.fit_symbol(S, np.zeros(600, dtype=int))
    mode = np.array(out["mode"])
    assert mode[-1] > 0 and abs(mode[-1]) > 3 * abs(mode[:-1]).mean()


def test_the_old_u_shape_gives_the_close_almost_nothing() -> None:
    u = fit.u_shape()
    assert abs(u.sum() - 1) < 1e-9 and u[-5:].sum() < 0.03


def test_tier_average_of_nothing_is_empty() -> None:
    assert fit.tier_average({}, ("SPY",)) == {}
