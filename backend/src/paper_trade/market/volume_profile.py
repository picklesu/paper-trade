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
    def __init__(self, specs: list[SymbolSpec] | tuple[SymbolSpec, ...], minutes: int, states: tuple[str, ...], path: Path = PROFILE_FILE) -> None:
        data = json.loads(path.read_text())
        self.minutes = minutes
        block = data["block"]
        if data["minutes"] != minutes or tuple(data["states"]) != tuple(states) or minutes % block:
            raise ValueError(f"{path.name} was fitted for {data['minutes']} minutes and states {data['states']}; refit with scripts/fit_intraday_volume.py")
        fits = []
        for s in specs:
            fit = data["symbols"].get(s.symbol) or data["tiers"]["stock" if s.earnings is not None else "fund"]
            fits.append(fit)
        for f in fits:
            if np.shape(f["profile"]) != (len(states), minutes) or len(f["mode"]) * block != minutes:
                raise ValueError(f"{path.name}: a fit has the wrong shape; refit with scripts/fit_intraday_volume.py")
        # (symbols, states, minutes), each profile summing to 1
        self.profile = np.array([f["profile"] for f in fits])
        # (symbols, minutes): the day-to-day mode in log shares, stretched from blocks to minutes
        self.mode = np.array([np.repeat(f["mode"], block) for f in fits])
        self.mode_sd = np.array([f["mode_sd"] for f in fits])
        # the whole market's usual volume through the day, for each state: the average stock's profile
        self.market_profile = np.array(data["tiers"]["stock"]["profile"])
        # how much a minute's volume stays above or below its profile into the next minute
        self.minute_ar1 = np.array([f.get("minute_ar1", 0.3) for f in fits])

    def shape(self, state: int, score: np.ndarray) -> np.ndarray:
        """(minutes, symbols) multiples of an average minute's volume (mean 1 for each symbol) on a
        day in `state`; `score` is a unit normal for each symbol that sets how close-heavy the day is."""
        raw = self.profile[:, state, :] * np.exp((score * self.mode_sd)[:, None] * self.mode)
        return (raw / raw.sum(axis=1, keepdims=True) * self.minutes).T
