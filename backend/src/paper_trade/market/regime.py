"""The market's long-lived regime (8f): calm bull, correction, crisis.

A hidden Markov model on SPY's daily returns whose transition odds depend on the
macro picture known the day before (yield curve, credit spread, VIX, unemployment
trend). `scripts/fit_regime.py` fits it offline and saves `regime_params.json`
beside this file; nothing is fitted at runtime. This module is pure numpy: the
features, the transition odds and the parameter file's reading.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np

STATES = ("calm", "correction", "crisis")
FEATURES = ("curve", "credit", "vix", "jobs")
PARAMS_FILE = Path(__file__).with_name("regime_params.json")
TRADING_DAYS = 252

# Publication lags for the series the features use (the same as macro.LAG_DAYS).
LAG_DAYS = {"UNRATE": 35}


def feature_panel(days: list[date], series: dict[str, list[tuple[date, float]]]) -> np.ndarray:
    """Raw features (curve, credit, log VIX, unemployment 12-month change) for each
    day, using only what was published before that day. NaN where not yet known."""

    def known(name: str, shift: int = 0) -> np.ndarray:
        rows = series.get(name, [])
        lag = LAG_DAYS.get(name, 0)
        ordinals = np.array([d.toordinal() + lag for d, _ in rows])
        values = np.array([v for _, v in rows], dtype=float)
        query = np.array([d.toordinal() - 1 - shift for d in days])  # the day before
        idx = np.searchsorted(ordinals, query, side="right") - 1
        out = np.full(len(days), np.nan)
        ok = idx >= 0
        out[ok] = values[idx[ok]]
        return out

    curve = known("T10Y2Y")
    credit = known("BAA10Y")
    vix = np.log(np.where(known("VIXCLS") > 0, known("VIXCLS"), np.nan))
    jobs = known("UNRATE") - known("UNRATE", 365)
    return np.column_stack([curve, credit, vix, jobs])


@dataclass(frozen=True)
class RegimeParams:
    mean: np.ndarray  # (K,) daily log-return drift per state
    sigma: np.ndarray  # (K,) daily volatility per state
    bias: np.ndarray  # (K, K) transition logits from state i to j
    weights: np.ndarray  # (K, K, F) logit slopes on the standardized features
    center: np.ndarray  # (F,) feature means
    scale: np.ndarray  # (F,) feature standard deviations
    period: int = 5  # sessions per step: mean, sigma and transitions are per step
    # How the standardized features move in each state (the future has no real macro
    # path): z' = level[state] + persistence[state] * z + noise[state] * N(0, 1).
    level: np.ndarray | None = None  # (K, F)
    persistence: np.ndarray | None = None  # (K, F)
    noise: np.ndarray | None = None  # (K, F)
    share: np.ndarray | None = None  # (K,) long-run share of time in each state (what the fit was calibrated to)

    def standardize(self, raw: np.ndarray) -> np.ndarray:
        x = (np.asarray(raw, dtype=float) - self.center) / self.scale
        return np.clip(np.nan_to_num(x, nan=0.0), -4.0, 4.0)

    def transition(self, raw: np.ndarray) -> np.ndarray:
        """Row-stochastic (K, K) odds of moving between states tomorrow, given the
        raw features known today (leading axes allowed: (..., F) -> (..., K, K))."""
        x = self.standardize(raw)
        logits = self.bias + np.einsum("ijf,...f->...ij", self.weights, x)
        logits = logits - logits.max(axis=-1, keepdims=True)
        p = np.exp(logits)
        return p / p.sum(axis=-1, keepdims=True)

    def to_json(self) -> dict:
        return {
            "states": list(STATES),
            "features": list(FEATURES),
            "mean": self.mean.tolist(),
            "sigma": self.sigma.tolist(),
            "bias": self.bias.tolist(),
            "weights": self.weights.tolist(),
            "center": self.center.tolist(),
            "scale": self.scale.tolist(),
            "period": self.period,
            "level": self.level.tolist(),
            "persistence": self.persistence.tolist(),
            "noise": self.noise.tolist(),
            "share": self.share.tolist(),
        }

    @staticmethod
    def from_json(d: dict) -> RegimeParams:
        arrays = {k: np.array(d[k], dtype=float) for k in ("mean", "sigma", "bias", "weights", "center", "scale")}
        dynamics = {k: np.array(d[k], dtype=float) for k in ("level", "persistence", "noise", "share")}
        return RegimeParams(**arrays, period=int(d["period"]), **dynamics)

    @property
    def variance_multiple(self) -> np.ndarray:
        """(K,) each state's variance as a multiple of the long-run average (the engine's
        market variance centers on it, so the average stays 1)."""
        v = self.sigma**2
        return v / float(self.share @ v)

    @property
    def drift_tilt(self) -> np.ndarray:
        """(K,) each state's annual drift less the long-run average."""
        return (self.mean - float(self.share @ self.mean)) * (TRADING_DAYS / self.period)

    def step(self, state: int, z: np.ndarray, rng: np.random.Generator) -> tuple[int, np.ndarray, float]:
        """One period from `state` with standardized features `z`: the next state, its
        features, and the period's log return."""
        p = self.transition(z * self.scale + self.center)[state]
        nxt = int(rng.choice(len(p), p=p))
        z2 = self.level[nxt] + self.persistence[nxt] * z + self.noise[nxt] * rng.standard_normal(len(z))
        return nxt, np.clip(z2, -4.0, 4.0), float(self.mean[nxt] + self.sigma[nxt] * rng.standard_normal())


def load_params(path: Path = PARAMS_FILE) -> RegimeParams:
    return RegimeParams.from_json(json.loads(path.read_text()))


def stationary(p: np.ndarray) -> np.ndarray:
    """The long-run share of time in each state for a fixed transition matrix."""
    w, v = np.linalg.eig(p.T)
    s = np.real(v[:, np.argmin(np.abs(w - 1))])
    return s / s.sum()


class RealRegime:
    """The regime through the real past: SPY's real weekly returns, filtered through the
    model (with the macro picture known at each step), give the state and features at
    every block end. The future starts from the last one (no look-ahead)."""

    def __init__(self, params: RegimeParams, days: list[date], closes: np.ndarray, series: dict[str, list[tuple[date, float]]]) -> None:
        period = params.period
        marks = np.arange(0, len(closes) - period, period)
        self.ends: list[date] = []
        self.states = np.zeros(0, dtype=int)
        self.z = np.zeros((0, len(params.center)))
        if len(marks) < 2:
            return
        r = np.log(closes[marks + period] / closes[marks])
        starts = [days[m + 1] for m in marks]
        trans = params.transition(feature_panel(starts, series))
        logb = -0.5 * ((r[:, None] - params.mean) / params.sigma) ** 2 - np.log(params.sigma)
        b = np.exp(logb - logb.max(axis=1, keepdims=True))
        alpha = np.zeros_like(b)
        a = b[0] / b[0].sum()
        alpha[0] = a
        for t in range(1, len(r)):
            a = (alpha[t - 1] @ trans[t]) * b[t]
            alpha[t] = a / a.sum()
        self.ends = [days[m + period] for m in marks]
        self.states = alpha.argmax(axis=1)
        after = [d + timedelta(days=1) for d in self.ends]
        self.z = np.array([params.standardize(row) for row in feature_panel(after, series)])

    def at(self, day: date) -> tuple[int, np.ndarray] | None:
        """The state and standardized features at the last block end on or before `day`."""
        k = bisect.bisect_right(self.ends, day) - 1
        return (int(self.states[k]), self.z[k]) if k >= 0 else None
