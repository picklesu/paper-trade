"""Runs the market and the account in the background and fans updates out to
subscribers. Everything here runs on the event loop's thread."""

import asyncio
import contextlib
import logging
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import numpy as np

from paper_trade.market.calendar import (
    MARKET_TZ,
    SESSION_SECONDS,
    market_day,
    previous_trading_day,
    session_close,
    session_open,
    session_seconds,
    snap,
    trading_days_between,
)
from paper_trade.market.clock import SPEEDS, SimClock
from paper_trade.market.engine import HISTORY_DAYS, MarketEngine
from paper_trade.market.history import History
from paper_trade.market import options
from paper_trade.optiondata import Context, OptionData, fetch_bars, fetch_earnings, fetch_treasury_rate, summarize
from paper_trade.pricehistory import PriceHistory, back_cast
from paper_trade.realprices import STATUS_REFRESH_AFTER, FetchError, FetchStop, RealPrices
from paper_trade.store import Store
from paper_trade.trading.broker import Broker
from paper_trade.watchlists import Watchlists

TICK_SECONDS = 0.5
SAVE_EVERY_TICKS = 10  # save the market state about every 5 seconds while it runs
MAX_JUMP_DAYS = 252 * 5
PRICE_HISTORY_PAUSE_SECONDS = 2  # between a symbol's price history requests
PRICE_HISTORY_CHECK_SECONDS = 600  # between looks for symbols still to fetch
PROGRESS_EVERY_DAYS = 5
SPARKLINE_MINUTES = 5
SUPERSEDED = {"tick", "progress", "history"}  # messages a later one of the same kind replaces
REAL_PRICES_CHECK_SECONDS = 60  # the refresher's longest wait before checking the schedule
STATUS_FETCH_WAIT_SECONDS = 5  # the Sync dialog's status waits this long for a fetch
CALIBRATION_BATCH = 10  # symbols calibrated between repricing chains during a sync
OPTION_DATA_CHECK_SECONDS = 600  # the option sync's longest wait before checking the schedule

log = logging.getLogger(__name__)

GotoAction = Literal["forward", "rewind", "reset", "none"]


class BusyError(Exception):
    """A time jump or the history build is running."""

    def __init__(self, message: str = "a time jump is already in progress") -> None:
        super().__init__(message)


class MarketService:
    def __init__(
        self,
        clock: SimClock,
        engine: MarketEngine,
        store: Store,
        history_days: int = HISTORY_DAYS,
        real: RealPrices | None = None,
        option_data: OptionData | None = None,
        price_history: PriceHistory | None = None,
    ) -> None:
        self.clock = clock
        self.engine = engine
        self.store = store
        self.broker = Broker(engine, store)
        self.history = History(store.connection, engine)
        engine.on_day_complete = self.history.record
        # Anything saved after the market's last saved time never happened.
        self.broker.rewind(engine.time)
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._task: asyncio.Task | None = None
        self._jumping = False
        self._ticks_since_save = 0
        self._saved_starts_version: int | None = None
        self._history_task: asyncio.Task | None = None
        self._building = False
        # Bumped whenever market time jumps or history changes, so charts reload.
        self.timeline = 0
        self.history_days = history_days
        self.real = real
        self._real_task: asyncio.Task | None = None
        self.watchlists = Watchlists(store, self.symbols + list(self.indexes))
        self._stats: tuple[tuple, dict] | None = None
        self._chains: dict[tuple[str, date], tuple[tuple, dict]] = {}
        self._real_lock = asyncio.Lock()
        self.option_data = option_data
        self.price_history = price_history
        self._history_prices_task: asyncio.Task | None = None
        self.calibrations = options.calibrations(option_data.saved, options.RATE if option_data.rate is None else option_data.rate) if option_data else {}
        self._calibration_version = 0
        self._option_task: asyncio.Task | None = None
        self._option_lock = asyncio.Lock()
        self._prices_tried = asyncio.Event()  # the option sync starts after the first price fetch
        self.broker.calibration = self.calibration
        self.broker.listed = self.is_listed

    @property
    def jumping(self) -> bool:
        return self._jumping

    @property
    def building_history(self) -> bool:
        return self._building

    # ----- messages ----------------------------------------------------------------

    def listed_from(self, symbol: str) -> date | None:
        """The first day the symbol traded for real (None: unknown, always listed)."""
        return self.price_history.listed_from(symbol) if self.price_history else None

    def is_listed(self, symbol: str, day: date | None = None) -> bool:
        """Whether the symbol existed on `day` (default: the market's day)."""
        first = self.listed_from(symbol)
        return first is None or first <= (day or market_day(self.engine.time))

    def listed_quotes(self) -> list[dict]:
        """Quotes of symbols that exist now, then the indexes."""
        return [q.to_dict() for s, q in self.engine.quotes.items() if self.is_listed(s)] + self.index_quotes()

    def bars(self, symbol: str, interval: str, end: datetime | None, count: int) -> dict:
        """Chart bars, none from before the symbol was listed."""
        out = self.history.bars(symbol, interval, end, count)
        first = self.listed_from(symbol)
        if first is not None:
            start = session_open(first).timestamp()
            span = 7 * 86400 if interval == "1W" else 0  # a week's bar counts if the listing is in it
            kept = [b for b in out["bars"] if b[0] + span > start or b[0] == start]
            if len(kept) < len(out["bars"]):
                out = {"bars": kept, "more": False}
        return out

    def snapshot(self) -> dict:
        quotes = self.engine.quotes
        return {
            "type": "tick",
            "clock": self.clock_state(),
            "regime": self.engine.regime,
            "shock": round(self.engine.shock, 2),
            "shock_pending": self.engine.shock_pending,
            "quotes": self.listed_quotes(),
            "account": self.broker.summary(quotes),
        }

    @property
    def indexes(self) -> tuple[str, ...]:
        """Symbols that are quoted and charted but can't be traded."""
        return (options.VIX_SYMBOL,) if any(s == "SPY" for s in self.symbols) else ()

    def index_quotes(self) -> list[dict]:
        quote = self.engine.vix_quote() if self.indexes else None
        return [quote] if quote else []

    def quote_stats(self) -> dict[str, dict]:
        """52-week ranges and average volumes (cached until the day or the history changes)."""
        key = (market_day(self.engine.time), self.timeline)
        if self._stats is None or self._stats[0] != key:
            self._stats = (key, self.history.quote_stats())
        return self._stats[1]

    def sparklines(self, symbols: list[str]) -> dict:
        """Today's prices every SPARKLINE_MINUTES so far, ending with the latest."""
        closes = self.engine.day_closes(SPARKLINE_MINUTES)
        cols = {s: i for i, s in enumerate(self.symbols)}
        series = {s: [round(float(p), 2) for p in closes[:, cols[s]]] for s in symbols if s in cols}
        if options.VIX_SYMBOL in symbols and self.indexes:
            day, now = market_day(self.engine.time), int(session_seconds(self.engine.time))
            step = SPARKLINE_MINUTES * 60  # the same grid as the stocks: each stretch's last second, then now
            secs = np.append(np.arange(step - 1, now, step), now)
            series[options.VIX_SYMBOL] = [round(float(v), 2) for v in self.engine.vix_at(day, np.minimum(secs, SESSION_SECONDS - 1))]
        return {
            "step_minutes": SPARKLINE_MINUTES,
            "session_open": session_open(market_day(self.engine.time)).isoformat(),
            "series": series,
        }

    def calibration(self, symbol: str) -> options.Calibration:
        found = self.calibrations.get(symbol)
        if found is not None:
            return found
        rate = self.option_data.rate if self.option_data else None
        return replace(options.DEFAULT, rate=rate) if rate is not None else options.DEFAULT

    def option_expirations(self, symbol: str) -> list[dict]:
        today = market_day(self.engine.time)
        pattern = self.calibration(symbol).pattern
        listed = {e.day: e for e in options.expirations(symbol, self.engine.time, pattern)}
        # Expirations of contracts held stay listed (a new calibration's pattern may drop them).
        for c in self.broker.held_contracts(symbol):
            if c.expiry not in listed and session_close(c.expiry) > self.engine.time:
                listed[c.expiry] = options.Expiration(c.expiry, "weekly")
        return [listed[d].to_dict(today) for d in sorted(listed)]

    def option_chain(self, symbol: str, expiration: date) -> dict:
        """One expiration's chain now (computed once per market time and timeline,
        however many clients show it: a new future can come back to the same time
        with other prices, and a new calibration changes them). Raises ValueError
        for an unknown symbol or expiration."""
        if symbol not in self.symbols:
            raise ValueError(f"unknown symbol: {symbol}")
        if not self.is_listed(symbol):
            raise ValueError(f"{symbol} wasn't trading yet")
        held = frozenset(c.strike for c in self.broker.held_contracts(symbol) if c.expiry == expiration)
        key, now = (symbol, expiration), (self.engine.time, self.timeline, self._calibration_version, held)
        cached = self._chains.get(key)
        if cached is None or cached[0] != now:
            if len(self._chains) > 16:
                self._chains.clear()
            self._chains[key] = (now, options.chain(self.engine, symbol, expiration, self.calibration(symbol), set(held)))
        return self._chains[key][1]

    def watchlists_changed(self) -> None:
        self.publish(self.watchlists.message(), force=True)

    def orders_message(self) -> dict:
        orders = sorted(self.broker.orders.values(), key=lambda o: o.id, reverse=True)
        return {"type": "orders", "orders": [self.broker.order_dict(o) for o in orders]}

    def clock_state(self) -> dict:
        return {
            "time": self.engine.time.isoformat(),
            "speed": self.clock.speed,
            "paused": self.clock.paused,
            "speeds": SPEEDS,
            "origin": self.engine.origin.isoformat(),
            "session_open": session_open(market_day(self.engine.time)).isoformat(),
            "timeline": self.timeline,
            # Bumped when an option calibration changes (expirations may change too).
            "calibration": self._calibration_version,
            "building_history": self.building_history,
        }

    def publish(self, msg: dict, force: bool = False) -> None:
        """Send to every subscriber. A slow client just misses messages, since the
        next tick carries full state; with `force`, a queued tick makes room
        instead, for messages that must arrive."""
        for q in self._subscribers:
            self.send(q, msg, force)

    @staticmethod
    def send(q: asyncio.Queue[dict], msg: dict, force: bool = True) -> None:
        """Queue `msg` for one subscriber. When the queue is full and `force` is
        set, the oldest message a later one supersedes (a tick or progress) makes
        room, so `orders` and `watchlists` messages already queued still arrive."""
        if q.full() and force:
            held = [q.get_nowait() for _ in range(q.qsize())]
            stale = next((i for i, m in enumerate(held) if m["type"] in SUPERSEDED), 0)
            del held[stale]
            for m in held:
                q.put_nowait(m)
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(msg)

    def publish_all(self) -> None:
        """Full state that must reach every client: after orders or time change."""
        self.publish(self.orders_message(), force=True)
        self.publish(self.snapshot(), force=True)

    # ----- running -----------------------------------------------------------------

    def sync(self) -> None:
        """Bring the market and orders up to the clock. If any order filled,
        triggered or expired, save and tell every client, whoever called this."""
        if self._jumping:
            raise BusyError
        t0 = self.engine.time
        self.engine.advance(self.clock.now())
        if self.broker.process(t0, self.engine.time):
            self.save()
            self.publish(self.orders_message(), force=True)

    def tick(self) -> None:
        if self._jumping:
            return
        before = self.engine.time
        self.sync()
        # Nothing changes while paused; clock updates publish on their own.
        if self.engine.time != before:
            self.publish(self.snapshot())
            self._ticks_since_save += 1
            if self._ticks_since_save >= SAVE_EVERY_TICKS:
                self.save()

    def save(self) -> None:
        state = self.engine.to_state()
        # Session start states grow with history: write them only when they change.
        starts = state.pop("starts")
        if self.engine.starts_version != self._saved_starts_version:
            self.store.set("market_starts", starts)
            self._saved_starts_version = self.engine.starts_version
        self.store.set("market", state)
        self.store.set("clock", {"speed": self.clock.speed, "paused": self.clock.paused})
        self._ticks_since_save = 0

    # ----- time travel -------------------------------------------------------------

    def preview_goto(self, target: datetime) -> tuple[GotoAction, datetime]:
        """What going to `target` would do, and the market time it lands on."""
        target = snap(target)
        now = max(self.engine.time, snap(self.clock.now()))
        if target < self.engine.origin:
            return "reset", target
        if target < now:
            return "rewind", target
        if target > now:
            return "forward", target
        return "none", target

    def goto_details(self, action: GotoAction, target: datetime) -> dict:
        """For the confirmation: trades that would be erased and how holdings are revalued."""
        if action == "rewind":
            self.sync()
            # Recheck: syncing can move "now", but never past the target of a rewind preview.
            return self.broker.preview_rewind(min(target, self.engine.time))
        if action == "reset":
            fills = [o for o in self.broker.orders.values() if o.status == "filled"]
            return {
                "erased_fills": [self.broker.order_dict(o) for o in sorted(fills, key=lambda o: o.filled_at)],
                "erased_orders": len(self.broker.orders),
                "starting_cash": self.broker.starting_cash,
            }
        return {}

    async def goto(self, target: datetime, new_future: bool = False) -> tuple[GotoAction, datetime]:
        if self._jumping:
            raise BusyError
        if self.building_history:
            raise BusyError("price history is still being built; try again in a moment")
        action, target = self.preview_goto(target)
        # Only sessions not yet generated count: after a rewind, the way back is free.
        if action == "forward" and trading_days_between(self.engine.generated_through, market_day(target)) > MAX_JUMP_DAYS:
            raise ValueError(f"can jump at most {MAX_JUMP_DAYS} new trading days ahead")
        if action == "rewind":
            # Match orders up to now first, so what gets rolled back is complete.
            self.sync()
            target = min(target, self.engine.time)

        self._jumping = True
        try:
            if action == "reset":
                self.engine.reset(target, self.real_start_prices(market_day(target)))
                self.broker.reset()
                self.history.clear()
            elif action == "rewind":
                shocks = set(self.engine._shocks)
                self.engine.rewind(target, new_future)
                if new_future or set(self.engine._shocks) != shocks:
                    self.history.delete_from(market_day(target))
                self.broker.rewind(target)
            elif action == "forward":
                await self._fast_forward(target)
            self.clock.set_time(target)
            self.save()
            self.timeline += 1
        finally:
            self._jumping = False
            if action == "reset":
                self.start_history()
            # Always end with full state (even after an error) so clients leave the
            # fast-forward state; never drop it for a full queue.
            self.publish_all()
        return action, target

    def trigger_shock(self) -> date:
        """Start an uncertainty shock at the next open. Later sessions that a replay
        had already generated are generated again, and what was recorded of them goes."""
        if self._jumping:
            raise BusyError
        if self.building_history:
            raise BusyError("price history is still being built; try again in a moment")
        self.sync()
        day = self.engine.trigger_shock()
        self.history.delete_from(day)
        self.save()
        self.publish_all()
        return day

    async def _fast_forward(self, target: datetime) -> None:
        # Sessions still to generate (fewer than the distance if a replay already generated some).
        days = trading_days_between(self.engine.generated_through, market_day(target))
        t = self.engine.time
        # Session by session: generate it, match working orders against it while its
        # price path is still cached, and yield so progress keeps flowing.
        with self.history.batch():
            for i, day in enumerate(self.engine.advance_iter(target), start=1):
                self.broker.process(t, session_open(day))
                t = session_open(day)
                if i % PROGRESS_EVERY_DAYS == 0:
                    self.publish({"type": "progress", "done": i, "total": days})
                await asyncio.sleep(0)
        self.broker.process(t, target)

    # ----- real prices -------------------------------------------------------------

    @property
    def symbols(self) -> list[str]:
        return [s.symbol for s in self.engine.specs]

    def real_prices_status(self) -> dict:
        if self.real is None:
            status = {"configured": False, "available": 0, "total": len(self.symbols)}
        else:
            status = self.real.status(self.symbols)
        history = self.price_history.status(self.symbols) if self.price_history else {"configured": False, "available": 0, "total": len(self.symbols)}
        return status | {"options": self.option_data_status(), "history": history}

    def option_data_status(self) -> dict:
        if self.option_data is None:
            return {"configured": False, "calibrated": 0, "total": len(self.symbols)}
        return self.option_data.status(self.symbols)

    def real_start_prices(self, day: date | None = None) -> np.ndarray | None:
        """Previous closes for a new market starting on `day`: the real closes of the
        day before where they're saved, otherwise today's real price brought back by
        the symbol's expected return, otherwise the spec's price (None if there is
        nothing real at all)."""
        saved = self.real.saved if self.real else {}
        hist = self.price_history
        if not saved and not (hist and hist.fetched):
            return None
        before = previous_trading_day(day) if day else None
        prices = []
        for spec in self.engine.specs:
            price = hist.price_on(spec.symbol, before, spec.drift) if hist and before else None
            real = saved.get(spec.symbol)
            if real and before is not None and before >= real.as_of:
                price = real.price  # the saved real price is newer than any close fetched
            if price is None and real:
                price = real.price if before is None else back_cast(real.price, real.as_of, min(before, real.as_of), spec.drift)
            prices.append(spec.price if price is None else price)
        return np.array(prices)

    async def sync_real_prices(self) -> dict:
        """Continue the market from now as a new one whose prices are the saved
        real ones (symbols without one keep their current price). The account
        and orders are kept; the history is generated again to end here."""
        if self._jumping:
            raise BusyError
        if self.building_history:
            raise BusyError("price history is still being built; try again in a moment")
        saved = self.real.saved if self.real else {}
        if not saved:
            raise ValueError("no real prices have been fetched yet")
        self.sync()
        quotes = self.engine.quotes
        prices = np.array([saved[s].price if s in saved else quotes[s].last for s in self.symbols])
        self._jumping = True
        try:
            self.engine.reanchor(prices)
            self.history.clear()
            self.save()
            self.timeline += 1
        finally:
            self._jumping = False
            self.start_history()
            self.publish_all()
        return self.real_prices_status()

    async def refresh_real_prices(self, older_than: timedelta | None = timedelta(0)) -> None:
        """Fetch every symbol's real price (one request, in a worker thread) unless
        the last fetch was more recent than `older_than` (None: unless the
        refresher's schedule says it's not time yet). A fetch already running is
        waited for instead of repeated."""
        if not (self.real and self.real.configured):
            return
        async with self._real_lock:
            due = self.real.seconds_until_refresh() <= 0 if older_than is None else self.real.stale(older_than)
            if not due:
                return
            symbols = self.symbols
            self.real.attempt()
            try:
                prices = await asyncio.to_thread(self.real.fetch, symbols)
            except Exception as e:
                self.real.fetch_failed(str(e))
                log.warning("fetching real prices failed: %s", e)
            else:
                self.real.record(prices, symbols)

    async def refresh_real_prices_if_stale(self) -> None:
        await self.refresh_real_prices(STATUS_REFRESH_AFTER)

    async def _refresh_real_prices(self) -> None:
        """Keep the saved real prices recent, in the background: a fetch at startup,
        then on the schedule. Waits are checked against the wall clock at least
        every minute, as a sleeping computer pauses asyncio's clock."""
        assert self.real
        try:
            await self.refresh_real_prices()
        finally:
            self._prices_tried.set()
        while True:
            await asyncio.sleep(min(REAL_PRICES_CHECK_SECONDS, self.real.seconds_until_refresh()))
            await self.refresh_real_prices(None)

    # ----- real option data --------------------------------------------------------

    def _calibrations_changed(self) -> None:
        assert self.option_data
        self.calibrations = options.calibrations(self.option_data.saved, options.RATE if self.option_data.rate is None else self.option_data.rate)
        self._calibration_version += 1

    async def refresh_option_data(self) -> None:
        """Fetch and calibrate every symbol whose real option chain is due (see
        OptionData.due), one at a time in a worker thread, saving each as it's
        done. A failed request stops the sync (it's tried again later; symbols
        done so far are kept)."""
        od = self.option_data
        if not (od and od.configured):
            return
        async with self._option_lock:
            symbols = od.due(self.symbols)
            if not symbols:
                return
            assert od.creds
            od.attempt(len(symbols))
            try:
                now = datetime.now(UTC)
                try:
                    bars = await asyncio.to_thread(fetch_bars, self.symbols, od.creds, now)
                except FetchError as e:
                    od.failed(f"daily bars: {e}")
                    return
                earnings = None
                if od.earnings_key:
                    today = now.astimezone(MARKET_TZ).date()
                    try:
                        earnings = await asyncio.to_thread(fetch_earnings, od.earnings_key, self.symbols, today)
                    except FetchError as e:
                        od.note(f"earnings dates: {e}")
                context = Context(now, bars, earnings)
                for n, symbol in enumerate(symbols, start=1):
                    try:
                        summary = await asyncio.to_thread(summarize, symbol, od.creds, context, datetime.now(UTC))
                    except FetchError as e:
                        od.failed(f"{symbol}'s options: {e}")
                        log.warning("fetching %s's options failed: %s", symbol, e)
                        return
                    except Exception:
                        # Keep it from being retried straight away; the defaults apply.
                        log.exception("calibrating %s's options failed", symbol)
                        summary = {"symbol": symbol, "fetched": datetime.now(UTC).isoformat(), "shape": None}
                        od.note(f"{symbol}'s options couldn't be calibrated")
                    od.record(symbol, summary)
                    # Chains reprice as symbols come in, in batches (each change
                    # reloads every Options panel's expirations).
                    if n % CALIBRATION_BATCH == 0:
                        self._calibrations_changed()
            finally:
                od.finished()
                self._calibrations_changed()

    async def refresh_rate(self) -> None:
        """Fetch the real 3-month Treasury yield about daily; without it (or a key)
        the fixed rate stays."""
        od = self.option_data
        if not (od and od.rate_due() and od.earnings_key):
            return
        try:
            day, rate = await asyncio.to_thread(fetch_treasury_rate, od.earnings_key)
        except FetchError as e:
            od.note(f"interest rate: {e}")
            return
        od.record_rate(day, rate)
        self._calibrations_changed()

    async def _refresh_price_history(self) -> None:
        """Fetch each symbol's daily closes once, slowly, in the background."""
        hist = self.price_history
        assert hist
        while True:
            for symbol in hist.due(self.symbols):
                try:
                    closes = await asyncio.to_thread(hist.fetch, symbol)
                except FetchStop as e:
                    hist.failed(str(e))
                    log.warning("fetching %s's price history failed: %s", symbol, e)
                    break
                except FetchError as e:
                    hist.skip(symbol, str(e))
                    log.warning("no price history for %s: %s", symbol, e)
                    continue
                except Exception as e:
                    hist.failed(f"fetching price history failed ({e})")
                    log.exception("fetching price history failed")
                    break
                hist.record(symbol, closes)
                await asyncio.sleep(PRICE_HISTORY_PAUSE_SECONDS)
            await asyncio.sleep(PRICE_HISTORY_CHECK_SECONDS)

    async def _refresh_option_data(self) -> None:
        """Keep calibrations about a day old, in the background, after the first
        price fetch (they share Alpaca's rate limit)."""
        assert self.option_data
        await self._prices_tried.wait()
        while True:
            try:
                await self.refresh_rate()
                await self.refresh_option_data()
            except Exception as e:
                # Keep the schedule going: try again after RETRY_AFTER.
                log.exception("syncing option data failed")
                self.option_data.failed(f"the option sync failed ({e})")
            wait = self.option_data.seconds_until_due(self.symbols)
            await asyncio.sleep(min(OPTION_DATA_CHECK_SECONDS, max(5.0, wait)))

    # ----- trading -----------------------------------------------------------------

    def after_trade(self) -> None:
        self.save()
        self.publish_all()

    # ----- lifecycle ---------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=8)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(q)

    async def _run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                # Keep the market running; one bad tick shouldn't freeze every client.
                log.exception("market tick failed")
            await asyncio.sleep(TICK_SECONDS)

    def start_history(self) -> None:
        """Build missing chart history in the background (see build_history)."""
        self._building = True
        self._history_task = asyncio.create_task(self.build_history())

    async def build_history(self) -> None:
        """Store bars for finished sessions that have none, and generate the
        years of history before the market's start if not done yet."""
        try:
            backfill = 0 if self.engine.backfilled else self.history_days
            if backfill:
                # Bars before the origin are about to be generated again; any there now
                # (from a market continued from an older price model) no longer match.
                self.history.delete_before(market_day(self.engine.origin))
            missing = self.history.missing_days()
            total, done = len(missing) + backfill, 0
            with self.history.batch():
                for day in missing:
                    self.history.record(day)
                    done += 1
                    await self._history_progress(done, total)
                if backfill:
                    for _ in self.history.backfill_iter(backfill):
                        done += 1
                        await self._history_progress(done, total)
        except Exception:
            log.exception("building price history failed")
        finally:
            # Clear the flag before announcing, so the final snapshot says it's done.
            self._building = False
        self.save()
        self.timeline += 1
        self.publish_all()

    async def _history_progress(self, done: int, total: int) -> None:
        if done % PROGRESS_EVERY_DAYS == 0 or done == total:
            self.publish({"type": "history", "done": done, "total": total})
        await asyncio.sleep(0)

    def start(self) -> None:
        self.start_history()
        self._task = asyncio.create_task(self._run())
        if self.real and self.real.configured:
            self._real_task = asyncio.create_task(self._refresh_real_prices())
        if self.option_data and self.option_data.configured:
            self._option_task = asyncio.create_task(self._refresh_option_data())
        if self.price_history and self.price_history.configured:
            self._history_prices_task = asyncio.create_task(self._refresh_price_history())

    async def stop(self) -> None:
        if self._history_prices_task:
            self._history_prices_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._history_prices_task
        if self._option_task:
            self._option_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._option_task
        if self._real_task:
            self._real_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._real_task
        if self._history_task:
            self._history_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._history_task
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.save()
