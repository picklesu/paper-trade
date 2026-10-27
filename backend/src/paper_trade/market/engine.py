"""Synthetic price engine.

Each symbol follows geometric Brownian motion driven by a shared market shock
(so symbols move together, like a real market) plus its own noise. The market
switches between calm, normal and volatile regimes. Everything is driven by a
seeded RNG, so the same seed and the same sequence of time steps always give
the same prices.
"""

import math
import random
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from paper_trade.market.symbols import SYMBOLS, SymbolSpec

# The market runs 24/7, so a year is every second of it.
SECONDS_PER_YEAR = 365 * 24 * 3600
SECONDS_PER_DAY = 24 * 3600

# A "trading day" rolls over at midnight New York time.
MARKET_TZ = ZoneInfo("America/New_York")

# (name, volatility multiplier, average sim-hours spent in the regime)
REGIMES = (("calm", 0.6, 12.0), ("normal", 1.0, 24.0), ("volatile", 2.0, 4.0))


@dataclass
class Quote:
    symbol: str
    name: str
    last: float
    bid: float
    ask: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: int

    @property
    def change(self) -> float:
        return self.last - self.prev_close

    @property
    def change_pct(self) -> float:
        return self.change / self.prev_close * 100

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "last": round(self.last, 2),
            "bid": self.bid,
            "ask": self.ask,
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "prev_close": round(self.prev_close, 2),
            "change": round(self.change, 2),
            "change_pct": round(self.change_pct, 2),
            "volume": self.volume,
        }


def bid_ask(price: float) -> tuple[float, float]:
    """Penny-wide spread for most prices, about 1bp for expensive ones."""
    spread = max(0.01, round(price * 0.0001, 2))
    # Round down to the cent; round() first so float noise like 57996.99999 floors correctly.
    bid = math.floor(round((price - spread / 2) * 100, 6)) / 100
    ask = round(bid + spread, 2)
    if ask < price:  # e.g. 1.234 floors to 1.22/1.23; shift up so the quote brackets the price
        bid, ask = round(bid + 0.01, 2), round(ask + 0.01, 2)
    return bid, ask


class MarketEngine:
    def __init__(
        self,
        start: datetime,
        seed: int = 42,
        specs: tuple[SymbolSpec, ...] = SYMBOLS,
    ) -> None:
        self._rng = random.Random(seed)
        self._specs = {s.symbol: s for s in specs}
        self._time = start
        self._day = start.astimezone(MARKET_TZ).date()
        self.regime = "normal"
        self.quotes: dict[str, Quote] = {}
        for s in specs:
            bid, ask = bid_ask(s.price)
            self.quotes[s.symbol] = Quote(
                symbol=s.symbol,
                name=s.name,
                last=s.price,
                bid=bid,
                ask=ask,
                open=s.price,
                high=s.price,
                low=s.price,
                prev_close=s.price,
                volume=0,
            )

    @property
    def time(self) -> datetime:
        return self._time

    def step(self, now: datetime) -> None:
        """Advance every price from the engine's current time to `now`."""
        dt = (now - self._time).total_seconds()
        if dt <= 0:
            return
        self._time = now

        day = now.astimezone(MARKET_TZ).date()
        if day != self._day:
            self._day = day
            self._roll_day()

        self._maybe_switch_regime(dt)
        vol_mult = next(m for name, m, _ in REGIMES if name == self.regime)

        dt_years = dt / SECONDS_PER_YEAR
        market_shock = self._rng.gauss(0, 1)
        for symbol, q in self.quotes.items():
            s = self._specs[symbol]
            rho = s.market_corr
            z = rho * market_shock + math.sqrt(1 - rho * rho) * self._rng.gauss(0, 1)
            sigma = s.vol * vol_mult
            q.last *= math.exp((s.drift - sigma * sigma / 2) * dt_years + sigma * math.sqrt(dt_years) * z)
            q.bid, q.ask = bid_ask(q.last)
            q.high = max(q.high, q.last)
            q.low = min(q.low, q.last)
            expected_volume = s.daily_volume * dt / SECONDS_PER_DAY * vol_mult
            q.volume += int(expected_volume * self._rng.lognormvariate(0, 0.5))

    def _roll_day(self) -> None:
        for q in self.quotes.values():
            q.prev_close = q.open = q.high = q.low = q.last
            q.volume = 0

    def _maybe_switch_regime(self, dt: float) -> None:
        avg_hours = next(h for name, _, h in REGIMES if name == self.regime)
        p_switch = 1 - math.exp(-dt / (avg_hours * 3600))
        if self._rng.random() < p_switch:
            others = [name for name, _, _ in REGIMES if name != self.regime]
            self.regime = self._rng.choice(others)
