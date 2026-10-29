"""Instruments available in the simulated market.

Prices are synthetic; tickers are used only so the watchlist feels familiar.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    name: str
    price: float  # starting price
    vol: float  # annualized volatility
    drift: float  # annualized expected return
    market_corr: float  # correlation with the overall market factor
    daily_volume: int  # average shares traded per sim day
    # Typical earnings move (standard deviation of the log gap at the open), and when:
    # (month of the quarter, 0-2, and day of that month, at most 26; the move comes
    # at the first session on or after it, as if reported the evening before).
    earnings_move: float = 0.0
    earnings: tuple[int, int] | None = None


SYMBOLS: tuple[SymbolSpec, ...] = (
    SymbolSpec("SPY", "S&P 500 ETF", 580.00, 0.16, 0.07, 1.00, 60_000_000),
    SymbolSpec("QQQ", "Nasdaq-100 ETF", 500.00, 0.21, 0.09, 0.92, 40_000_000),
    SymbolSpec("IWM", "Russell 2000 ETF", 225.00, 0.24, 0.06, 0.85, 30_000_000),
    SymbolSpec("AAPL", "Apple", 230.00, 0.26, 0.10, 0.70, 50_000_000, 0.04, (0, 26)),
    SymbolSpec("MSFT", "Microsoft", 420.00, 0.24, 0.10, 0.70, 20_000_000, 0.045, (0, 23)),
    SymbolSpec("NVDA", "NVIDIA", 140.00, 0.50, 0.15, 0.60, 250_000_000, 0.07, (1, 22)),
    SymbolSpec("AMZN", "Amazon", 190.00, 0.32, 0.10, 0.65, 40_000_000, 0.06, (0, 26)),
    SymbolSpec("META", "Meta Platforms", 570.00, 0.36, 0.10, 0.60, 15_000_000, 0.07, (0, 25)),
    SymbolSpec("TSLA", "Tesla", 250.00, 0.60, 0.08, 0.45, 90_000_000, 0.08, (0, 20)),
    SymbolSpec("AMD", "Advanced Micro Devices", 150.00, 0.50, 0.10, 0.55, 45_000_000, 0.07, (0, 24)),
)
