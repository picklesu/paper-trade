import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import walk_forward as wf  # noqa: E402
from paper_trade.market.regime import load_params  # noqa: E402


def test_crps_and_pit() -> None:
    x = np.array([1.0, 2.0, 3.0, 4.0])
    assert wf.crps(np.full(50, 2.0), 2.0) == 0.0  # a point forecast that is right
    assert wf.crps(np.full(50, 2.0), 5.0) == 3.0  # and the distance when it is wrong
    assert wf.crps(x, 2.5) < wf.crps(x + 10, 2.5)
    rng = np.random.default_rng(0)
    assert wf.pit(x, 0.5, rng) == 0.0 and wf.pit(x, 9.0, rng) == 1.0
    assert 0.25 <= wf.pit(x, 2.0, rng) <= 0.5  # a tie lands inside its share


def test_path_stats() -> None:
    ret, vol, dd = wf.path_stats(np.array([[0.1, -0.2, 0.05, 0.0]]), 3)
    assert ret[0] == np.float64(0.1 - 0.2 + 0.05)
    assert dd[0] == np.float64(1 - np.exp(-0.2))  # the fall from the peak after the first week
    assert vol[0] > 0


def test_simulation_is_seeded_and_starts_from_the_given_state() -> None:
    p = load_params()
    z = np.zeros(len(p.center))
    a, sa = wf.simulate(p, 2, z, 12, 200, np.random.default_rng(5))
    b, sb = wf.simulate(p, 2, z, 12, 200, np.random.default_rng(5))
    assert np.array_equal(a, b) and np.array_equal(sa, sb)
    assert a.shape == (200, 12) and sa.min() >= 0 and sa.max() < len(p.mean)
    # Crisis lasts: from a crisis most paths are still there next week, from calm few are.
    _, from_crisis = wf.simulate(p, 2, z, 1, 2000, np.random.default_rng(1))
    _, from_calm = wf.simulate(p, 0, z, 1, 2000, np.random.default_rng(1))
    assert (from_crisis == 2).mean() > 0.5 > (from_calm == 2).mean() + 0.3
