"""Each symbol's volume through the session (step 3 of the intraday volume work).

`scripts/fit_intraday_volume.py` fits `volume_profile.json` offline from real one-minute
bars: per symbol a 390-minute share profile for each regime state, the leading way days
differ from it (a close-heavy day against an open-heavy one, in 5-minute blocks) and how
widely that varies. A symbol with no fit of its own takes its tier's average (funds report
no earnings). Nothing is fitted at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from paper_trade.market.symbols import SymbolSpec

PROFILE_FILE = Path(__file__).with_name("volume_profile.json")


class VolumeProfiles:
    def __init__(self, specs: list[SymbolSpec] | tuple[SymbolSpec, ...], path: Path = PROFILE_FILE) -> None:
        data = json.loads(path.read_text())
        self.minutes: int = data["minutes"]
        block = data["block"]
        fits = []
        for s in specs:
            fit = data["symbols"].get(s.symbol) or data["tiers"]["stock" if s.earnings is not None else "fund"]
            fits.append(fit)
        # (symbols, states, minutes), each profile summing to 1
        self.profile = np.array([f["profile"] for f in fits])
        # (symbols, minutes): the day-to-day mode in log shares, stretched from blocks to minutes
        self.mode = np.array([np.repeat(f["mode"], block) for f in fits])
        self.mode_sd = np.array([f["mode_sd"] for f in fits])

    def shape(self, state: int, score: np.ndarray) -> np.ndarray:
        """(minutes, symbols) multiples of an average minute's volume (mean 1 for each symbol) on a
        day in `state`; `score` is a unit normal for each symbol that sets how close-heavy the day is."""
        raw = self.profile[:, state, :] * np.exp((score * self.mode_sd)[:, None] * self.mode)
        return (raw / raw.sum(axis=1, keepdims=True) * self.minutes).T
