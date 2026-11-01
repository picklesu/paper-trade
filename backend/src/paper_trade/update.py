"""Fetch real data from the APIs and save it: `./server.sh update`.

The app itself never calls an API: it only reads what is saved. Run this when you
want newer data, then restart the server (it reads the data at startup).

    ./server.sh update            everything below
    ./server.sh update prices     Alpaca's latest quotes
    ./server.sh update history    Tiingo's daily bars and FRED's economic series
    ./server.sh update options    Alpaca's option chains (fitted), the interest rate
"""

import argparse
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from paper_trade import macro as macro_mod
from paper_trade import optiondata, pricehistory, realprices
from paper_trade.macro import Macro
from paper_trade.market.calendar import MARKET_TZ
from paper_trade.market.symbols import SYMBOLS
from paper_trade.optiondata import Context, OptionData, fetch_bars, fetch_earnings, fetch_treasury_rate, summarize
from paper_trade.pricehistory import PriceHistory
from paper_trade.realprices import FetchError, FetchStop, RealPrices
from paper_trade.store import DEFAULT_DB, Store

PRICE_HISTORY_PAUSE_SECONDS = 2  # between Tiingo requests
MACRO_PAUSE_SECONDS = 1  # between FRED requests (it allows 120 a minute)

Say = Callable[[str], None]
STEPS = ("prices", "history", "options")


def update_prices(real: RealPrices, symbols: list[str], say: Say = print) -> list[str]:
    """Alpaca's latest quote for every symbol (one request). Returns problems."""
    if not real.configured:
        return ["Alpaca keys missing: add APCA_API_KEY_ID and APCA_API_SECRET_KEY to backend/.env"]
    try:
        prices = real.fetch(symbols)
    except Exception as e:
        real.failed(str(e))
        return [f"real prices: {e}"]
    real.record(prices, symbols)
    say(f"Real quotes: {len(prices)} of {len(symbols)} symbols")
    status = real.status(symbols)
    return [status["last_error"]] if status["last_error"] else []


def update_history(hist: PriceHistory, symbols: list[str], say: Say = print) -> dict:
    """Tiingo's daily bars: each symbol not yet downloaded in full, then only the days
    since each saved symbol's last bar. A split among the new days fetches that
    symbol's whole history again. Stops at a rate limit (run it again later)."""
    added: dict[str, int] = {}
    problems: list[str] = []
    if not hist.configured:
        return {"prices": added, "problems": ["TIINGO_API_KEY missing: add it to backend/.env"]}
    stopped = False
    first = hist.due(symbols)
    for symbol in first:
        try:
            bars = hist.fetch(symbol)
        except FetchStop as e:
            hist.failed(str(e))
            problems.append(f"{e} (Tiingo allows 50 requests an hour; run the update again later, what is saved is kept)")
            stopped = True
            break
        except FetchError as e:
            hist.skip(symbol, str(e))
            problems.append(f"{symbol}: {e}")
            continue
        except Exception as e:  # a malformed answer: carry on with the others
            problems.append(f"{symbol}: unexpected answer ({e!r})")
            continue
        hist.record(symbol, bars)
        added[symbol] = len(bars)
        say(f"{symbol}: downloaded {len(bars)} days")
        time.sleep(PRICE_HISTORY_PAUSE_SECONDS)
    if not stopped:
        for symbol in sorted((hist.fetched & set(symbols)) - set(first)):
            last = hist.last_day(symbol)
            if last is None:
                continue
            split = next((b.split for b in hist.bars(symbol, last, last)), 1.0)
            try:
                bars, whole = hist.fetch_new(symbol, last, split)
            except FetchStop as e:
                hist.failed(str(e))
                problems.append(str(e))
                break
            except FetchError as e:
                problems.append(f"{symbol}: {e}")
                continue
            except Exception as e:
                problems.append(f"{symbol}: unexpected answer ({e!r})")
                continue
            try:
                n = hist.record_new(symbol, bars, whole)
            except FetchError as e:
                problems.append(str(e))
                continue
            if n:
                added[symbol] = n
                say(f"{symbol}: {n} new days")
            time.sleep(PRICE_HISTORY_PAUSE_SECONDS)
    return {"prices": added, "problems": problems}


def update_macro(economy: Macro, say: Say = print) -> dict:
    """FRED's series: each one not yet downloaded in full, then only the days since."""
    added: dict[str, int] = {}
    problems: list[str] = []
    if not economy.configured:
        return {"macro": added, "problems": ["FRED_API_KEY missing: add it to backend/.env"]}
    stopped = False
    first = economy.due()
    for series in first:
        try:
            rows = economy.fetch(series)
        except FetchStop as e:
            economy.failed(str(e))
            problems.append(str(e))
            stopped = True
            break
        except FetchError as e:  # this series only: carry on with the others
            problems.append(f"{series}: {e}")
            continue
        except Exception as e:
            problems.append(f"{series}: unexpected answer ({e!r})")
            continue
        economy.record(series, rows)
        added[series] = len(rows)
        say(f"{series}: downloaded {len(rows)} readings")
        time.sleep(MACRO_PAUSE_SECONDS)
    if not stopped:
        for series in sorted(set(economy.fetched) - set(first)):
            before = economy.last_day(series)
            if before is None:
                continue
            try:
                rows = economy.fetch_new(series, before)
            except FetchStop as e:
                economy.failed(str(e))
                problems.append(str(e))
                break
            except FetchError as e:
                problems.append(f"{series}: {e}")
                continue
            except Exception as e:
                problems.append(f"{series}: unexpected answer ({e!r})")
                continue
            economy.record(series, rows)
            if n := sum(1 for d, _ in rows if d > before):
                added[series] = n
                say(f"{series}: {n} new readings")
            time.sleep(MACRO_PAUSE_SECONDS)
    return {"macro": added, "problems": problems}


def update_options(od: OptionData, symbols: list[str], say: Say = print) -> list[str]:
    """The interest rate, then each symbol whose option chain is missing or about a day old:
    fetch its chain and fit the option model to it (about a minute and a half for all 40).
    A failed request stops the fit; symbols done so far are kept. Returns problems."""
    if not od.configured:
        return ["Alpaca keys missing: option fits need APCA_API_KEY_ID and APCA_API_SECRET_KEY in backend/.env"]
    problems: list[str] = []
    due = od.due(symbols)
    if due:
        od.attempt()  # first: it clears the status, and the notes below must survive
    if od.rate_due() and od.earnings_key:
        try:
            day, rate = fetch_treasury_rate(od.earnings_key)
        except FetchError as e:
            od.note(f"interest rate: {e}")
            problems.append(f"interest rate: {e}")
        else:
            od.record_rate(day, rate)
            say(f"Interest rate: {rate:.2%} ({day})")
    if not due:
        say("Option fits: all less than a day old")
        return problems
    assert od.creds
    now = datetime.now(UTC)
    try:
        bars = fetch_bars(symbols, od.creds, now)
    except FetchError as e:
        od.failed(f"daily bars: {e}")
        return [*problems, f"daily bars: {e}"]
    earnings = None
    if od.earnings_key:
        try:
            earnings = fetch_earnings(od.earnings_key, symbols, now.astimezone(MARKET_TZ).date())
        except FetchError as e:
            od.note(f"earnings dates: {e}")
            problems.append(f"earnings dates: {e}")
    context = Context(now, bars, earnings)
    for n, symbol in enumerate(due, start=1):
        try:
            summary = summarize(symbol, od.creds, context, datetime.now(UTC))
        except FetchError as e:
            od.failed(f"{symbol}'s options: {e}")
            problems.append(f"{symbol}'s options: {e}")
            break
        except Exception as e:
            # Keep it from being retried straight away; the defaults apply.
            summary = {"symbol": symbol, "fetched": datetime.now(UTC).isoformat(), "shape": None}
            od.note(f"{symbol}'s options couldn't be calibrated")
            problems.append(f"{symbol}'s options couldn't be calibrated ({e})")
        od.record(symbol, summary)
        say(f"Option fits: {n} of {len(due)} ({symbol})")
    return problems


def run(store: Store, steps: tuple[str, ...] = STEPS, say: Say = print) -> list[str]:
    """Run the chosen steps against the store; returns every problem found."""
    symbols = [s.symbol for s in SYMBOLS]
    creds = realprices.credentials()
    problems: list[str] = []
    if "prices" in steps:
        problems += update_prices(RealPrices(store, creds), symbols, say)
    if "history" in steps:
        problems += update_history(PriceHistory(store, pricehistory.tiingo_key()), symbols, say)["problems"]
        problems += update_macro(Macro(store, macro_mod.fred_key()), say)["problems"]
    if "options" in steps:
        problems += update_options(OptionData(store, creds, optiondata.av_key()), symbols, say)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="update", description="Fetch real data and save it for the app.")
    parser.add_argument("steps", nargs="*", choices=STEPS, help="what to fetch (default: all)")
    args = parser.parse_args(argv)
    steps = tuple(args.steps) or STEPS
    path = Path(os.environ.get("PAPER_TRADE_DB") or DEFAULT_DB)
    store = Store(path)
    try:
        problems = run(store, steps)
    finally:
        store.close()
    for p in problems:
        print(f"Problem: {p}")
    print("Done." + (" Some things failed (above); what was fetched is saved." if problems else "") + " Restart the server (./server.sh restart) to use the new data.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
