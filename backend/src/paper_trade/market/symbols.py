"""Instruments available in the simulated market.

Prices are synthetic; tickers are used only so the watchlist feels familiar.
"""

from dataclasses import dataclass, replace


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
    # The symbol's sector, and the correlation of the rest of its moves (what the
    # market doesn't explain) with that sector's.
    sector: str | None = None
    sector_corr: float = 0.0
    # Dividends: the yield a year, the months of the ex-dividend dates (1-12) and the
    # day of the month (the ex-date is the first session on or after it). Each
    # payment is the yield divided by the payments a year, times the previous close.
    dividend_yield: float = 0.0
    dividend_months: tuple[int, ...] = ()
    dividend_day: int = 10

    @property
    def dividend_fraction(self) -> float:
        """Each payment as a fraction of the stock's previous close."""
        return self.dividend_yield / len(self.dividend_months) if self.dividend_months else 0.0


TECH, COMM, DISC, STAPLES, FIN, ENERGY, HEALTH, INDUSTRIAL = (
    "Technology",
    "Communication",
    "Consumer discretionary",
    "Consumer staples",
    "Financials",
    "Energy",
    "Health care",
    "Industrials",
)

S = SymbolSpec
SYMBOLS: tuple[SymbolSpec, ...] = (
    # Funds
    S("SPY", "S&P 500 ETF", 580.00, 0.16, 0.07, 1.00, 60_000_000),
    S("QQQ", "Nasdaq-100 ETF", 500.00, 0.21, 0.09, 0.92, 40_000_000, sector=TECH, sector_corr=0.8),
    S("IWM", "Russell 2000 ETF", 225.00, 0.24, 0.06, 0.85, 30_000_000),
    S("XLF", "Financials Sector ETF", 48.00, 0.19, 0.07, 0.80, 40_000_000, sector=FIN, sector_corr=0.9),
    S("XLE", "Energy Sector ETF", 90.00, 0.26, 0.05, 0.55, 15_000_000, sector=ENERGY, sector_corr=0.9),
    S("XLV", "Health Care Sector ETF", 145.00, 0.15, 0.06, 0.70, 8_000_000, sector=HEALTH, sector_corr=0.9),
    S("GLD", "Gold ETF", 240.00, 0.15, 0.04, 0.05, 8_000_000),
    S("TLT", "20+ Year Treasury ETF", 92.00, 0.15, 0.02, -0.25, 35_000_000),
    # Technology
    S("AAPL", "Apple", 230.00, 0.26, 0.10, 0.70, 50_000_000, 0.04, (0, 26), TECH, 0.5),
    S("MSFT", "Microsoft", 420.00, 0.24, 0.10, 0.70, 20_000_000, 0.045, (0, 23), TECH, 0.5),
    S("NVDA", "NVIDIA", 140.00, 0.50, 0.15, 0.60, 250_000_000, 0.07, (1, 22), TECH, 0.5),
    S("AMD", "Advanced Micro Devices", 150.00, 0.50, 0.10, 0.55, 45_000_000, 0.07, (0, 24), TECH, 0.5),
    S("AVGO", "Broadcom", 170.00, 0.40, 0.12, 0.60, 25_000_000, 0.06, (2, 10), TECH, 0.5),
    S("ORCL", "Oracle", 170.00, 0.32, 0.10, 0.55, 9_000_000, 0.06, (2, 12), TECH, 0.45),
    # Communication
    S("GOOGL", "Alphabet", 175.00, 0.30, 0.10, 0.65, 25_000_000, 0.05, (0, 25), COMM, 0.45),
    S("META", "Meta Platforms", 570.00, 0.36, 0.10, 0.60, 15_000_000, 0.07, (0, 25), COMM, 0.45),
    S("NFLX", "Netflix", 700.00, 0.38, 0.10, 0.50, 3_000_000, 0.08, (0, 18), COMM, 0.4),
    S("DIS", "Walt Disney", 100.00, 0.26, 0.06, 0.60, 9_000_000, 0.05, (1, 8), COMM, 0.4),
    # Consumer discretionary
    S("AMZN", "Amazon", 190.00, 0.32, 0.10, 0.65, 40_000_000, 0.06, (0, 26), DISC, 0.45),
    S("TSLA", "Tesla", 250.00, 0.60, 0.08, 0.45, 90_000_000, 0.08, (0, 20), DISC, 0.4),
    S("HD", "Home Depot", 400.00, 0.22, 0.07, 0.65, 3_500_000, 0.03, (1, 16), DISC, 0.5),
    S("NKE", "Nike", 80.00, 0.30, 0.05, 0.50, 10_000_000, 0.06, (2, 20), DISC, 0.45),
    # Consumer staples
    S("WMT", "Walmart", 85.00, 0.18, 0.07, 0.45, 15_000_000, 0.03, (1, 18), STAPLES, 0.5),
    S("COST", "Costco", 900.00, 0.20, 0.09, 0.50, 2_000_000, 0.03, (2, 25), STAPLES, 0.45),
    S("KO", "Coca-Cola", 70.00, 0.15, 0.05, 0.40, 13_000_000, 0.02, (1, 11), STAPLES, 0.55),
    S("PG", "Procter & Gamble", 170.00, 0.15, 0.05, 0.40, 6_000_000, 0.025, (0, 22), STAPLES, 0.55),
    # Financials
    S("JPM", "JPMorgan Chase", 240.00, 0.22, 0.08, 0.70, 9_000_000, 0.03, (0, 14), FIN, 0.65),
    S("BAC", "Bank of America", 45.00, 0.26, 0.07, 0.70, 35_000_000, 0.035, (0, 15), FIN, 0.65),
    S("GS", "Goldman Sachs", 580.00, 0.26, 0.08, 0.70, 2_000_000, 0.04, (0, 15), FIN, 0.6),
    S("V", "Visa", 300.00, 0.20, 0.09, 0.60, 6_000_000, 0.03, (0, 24), FIN, 0.4),
    # Energy
    S("XOM", "Exxon Mobil", 115.00, 0.24, 0.05, 0.50, 15_000_000, 0.025, (1, 1), ENERGY, 0.75),
    S("CVX", "Chevron", 155.00, 0.25, 0.05, 0.50, 8_000_000, 0.025, (1, 1), ENERGY, 0.75),
    S("COP", "ConocoPhillips", 105.00, 0.30, 0.05, 0.50, 7_000_000, 0.035, (1, 6), ENERGY, 0.75),
    # Health care
    S("UNH", "UnitedHealth", 520.00, 0.25, 0.07, 0.45, 4_000_000, 0.05, (0, 15), HEALTH, 0.45),
    S("JNJ", "Johnson & Johnson", 155.00, 0.16, 0.05, 0.40, 7_000_000, 0.025, (0, 21), HEALTH, 0.5),
    S("LLY", "Eli Lilly", 850.00, 0.32, 0.12, 0.40, 3_000_000, 0.06, (1, 4), HEALTH, 0.45),
    S("PFE", "Pfizer", 28.00, 0.24, 0.03, 0.40, 40_000_000, 0.035, (1, 2), HEALTH, 0.5),
    # Industrials
    S("CAT", "Caterpillar", 380.00, 0.28, 0.08, 0.65, 3_000_000, 0.045, (1, 3), INDUSTRIAL, 0.5),
    S("BA", "Boeing", 160.00, 0.35, 0.05, 0.55, 8_000_000, 0.05, (0, 24), INDUSTRIAL, 0.4),
    S("GE", "GE Aerospace", 190.00, 0.30, 0.09, 0.60, 5_000_000, 0.05, (0, 22), INDUSTRIAL, 0.45),
)
del S

QUARTERLY = (3, 6, 9, 12)
FEB_QUARTERLY = (2, 5, 8, 11)
JAN_QUARTERLY = (1, 4, 7, 10)
# Roughly the real payers' yields and ex-dividend months (yield, months, day);
# symbols not listed pay nothing.
DIVIDENDS: dict[str, tuple[float, tuple[int, ...], int]] = {
    "SPY": (0.012, QUARTERLY, 20), "QQQ": (0.006, QUARTERLY, 20), "IWM": (0.012, QUARTERLY, 20),
    "XLF": (0.015, QUARTERLY, 20), "XLE": (0.033, QUARTERLY, 20), "XLV": (0.015, QUARTERLY, 20),
    "TLT": (0.040, tuple(range(1, 13)), 1),
    "AAPL": (0.0045, FEB_QUARTERLY, 10), "MSFT": (0.0075, FEB_QUARTERLY, 15), "NVDA": (0.0003, QUARTERLY, 10),
    "AVGO": (0.013, QUARTERLY, 21), "ORCL": (0.010, JAN_QUARTERLY, 10),
    "GOOGL": (0.004, QUARTERLY, 9), "META": (0.0035, QUARTERLY, 16), "DIS": (0.009, (6, 12), 15),
    "HD": (0.023, QUARTERLY, 5), "NKE": (0.018, QUARTERLY, 1),
    "WMT": (0.010, (3, 5, 8, 12), 10), "COST": (0.005, FEB_QUARTERLY, 1), "KO": (0.029, (3, 6, 9, 11), 13),
    "PG": (0.024, JAN_QUARTERLY, 20),
    "JPM": (0.020, JAN_QUARTERLY, 5), "BAC": (0.023, QUARTERLY, 5), "GS": (0.022, QUARTERLY, 1), "V": (0.008, FEB_QUARTERLY, 10),
    "XOM": (0.034, FEB_QUARTERLY, 14), "CVX": (0.042, FEB_QUARTERLY, 15), "COP": (0.030, FEB_QUARTERLY, 18),
    "UNH": (0.016, QUARTERLY, 10), "JNJ": (0.032, FEB_QUARTERLY, 25), "LLY": (0.007, FEB_QUARTERLY, 14),
    "PFE": (0.060, (1, 5, 8, 11), 7),
    "CAT": (0.016, JAN_QUARTERLY, 20), "GE": (0.006, QUARTERLY, 10),
}
SYMBOLS = tuple(
    replace(s, dividend_yield=d[0], dividend_months=d[1], dividend_day=d[2]) if (d := DIVIDENDS.get(s.symbol)) else s
    for s in SYMBOLS
)
