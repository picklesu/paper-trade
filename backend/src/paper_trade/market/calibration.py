"""Fitting the option model to real option prices (Phase 7.5).

A real chain (Alpaca's indicative snapshots for one underlying) becomes a small
summary of the symbol's *shape*; the simulation keeps setting the level:

  - each expiration's forward from put-call parity (which absorbs dividends and
    the real rate) and implied volatilities from out-of-the-money mid prices;
  - an SSVI smile fitted to each expiration (skew rho, curvature phi), then one
    surface per symbol: rho by tenor and phi(theta) = eta / (theta^gamma (1 +
    theta)^(1 - gamma)), so the smile scales with whatever level the simulation
    sets;
  - the volatility risk premium by tenor: real ATM IV / real realized volatility
    over a window matched to the tenor (earnings excluded from both);
  - the implied earnings move, from the jump in ATM variance across the real
    report date;
  - which expirations the symbol lists (the pattern, relative to the day);
  - how its option volume and open interest spread across expirations,
    strikes, calls and puts.

Everything here is pure (no network, no store); `paper_trade.optiondata` fetches
and saves, and `options.Calibration.from_summary` reads the summary back.
"""

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np

from paper_trade.market.calendar import (
    MARKET_TZ,
    is_trading_day,
    next_trading_day,
    nth_weekday,
    session_close,
    trading_day_on_or_before,
)
from paper_trade.market.options import (
    RATE,
    TENOR_NODES,
    VOLUME_BUCKETS,
    YEAR_SECONDS,
    _cdf,
    volume_bucket,
    ssvi,
    ssvi_phi_cap,
)

MIN_PRICE = 0.05  # quotes cheaper than this say little about volatility
MAX_RELATIVE_SPREAD = 0.5  # of the mid; wider quotes are dropped
QUOTE_MAX_AGE = timedelta(days=1)  # older than the chain's newest quote: stale
SHAPE_MIN_DAYS = 5  # expirations sooner than this are too noisy to fit a smile to
SHAPE_MAX_YEARS = 2.6
MIN_POINTS = 6  # strikes an expiration needs for its smile fit
EARNINGS_WINDOW_DAYS = 30  # expirations this soon after a report are left out of shape fits
NODE_BANDWIDTH = 0.5  # in log tenor: how far each tenor node looks for expirations
MIN_NODE_WEIGHT = 0.3
GAMMA_RANGE = (0.2, 0.8)
RHO_RANGE = (-0.95, 0.95)
TRADING_DAYS = 252
REALIZED_MIN_DAYS = 126
DAYS_BETWEEN_REPORTS = 91
MAX_ZERO_DAY_SHARE = 0.5  # scaling a session's volume up for its expired same-day options: at most double


@dataclass
class Slice:
    """One real expiration's usable quotes."""

    expiry: date
    years: float
    strikes: np.ndarray
    call_bid: np.ndarray  # NaN where there's no usable quote
    call_ask: np.ndarray
    put_bid: np.ndarray
    put_ask: np.ndarray
    # Contracts traded on each day asked for, and open interest ("oi"): (calls, puts) by strike.
    volumes: dict[date | str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)


@dataclass(frozen=True)
class Earnings:
    """A real report: `day` and whether before the open, after the close or not said."""

    day: date
    timing: str  # "pre-market", "post-market" or ""

    @property
    def event_day(self) -> date:
        """The first session that trades on the news."""
        return self.day if self.timing == "pre-market" else next_trading_day(self.day)

    def spans(self, expiry: date) -> bool | None:
        """Whether options expiring that day include the report (None: can't tell)."""
        if self.timing == "" and expiry == self.day:
            return None
        return expiry >= self.event_day


# ----- parsing -------------------------------------------------------------------


def parse_occ(occ: str) -> tuple[str, date, str, float] | None:
    """OCC option symbol -> (root, expiration, "C" or "P", strike)."""
    if len(occ) < 16 or occ[-9] not in "CP":
        return None
    try:
        day = date(2000 + int(occ[-15:-13]), int(occ[-13:-11]), int(occ[-11:-9]))
        strike = int(occ[-8:]) / 1000
    except ValueError:
        return None
    return occ[:-15], day, occ[-9], strike


def _bar_day(bar: dict | None) -> date | None:
    if not bar or not bar.get("t"):
        return None
    return datetime.fromisoformat(bar["t"]).astimezone(MARKET_TZ).date()


def slices(
    symbol: str,
    snapshots: dict[str, dict],
    fetched: datetime,
    volume_days: tuple[date, ...] = (),
    open_interest: dict[str, int] | None = None,
) -> list[Slice]:
    """Each expiration still open at `fetched`, with quotes that pass the checks
    (two-sided, not too wide, not stale), volumes traded on `volume_days` and
    open interest (by OCC symbol), if given.
    Contracts on other roots (adjusted after splits and the like) are skipped."""
    rows: dict[date, dict[float, dict]] = {}
    newest = None
    for occ, snap in snapshots.items():
        parsed = parse_occ(occ)
        if parsed is None or parsed[0] != symbol:
            continue
        _, expiry, side, strike = parsed
        q = snap.get("latestQuote") or {}
        row = rows.setdefault(expiry, {}).setdefault(strike, {})
        if q.get("t"):
            t = datetime.fromisoformat(q["t"])
            newest = t if newest is None or t > newest else newest
            row[side + "q"] = (float(q.get("bp") or 0), float(q.get("ap") or 0), t)
        for bar in (snap.get("dailyBar"), snap.get("prevDailyBar")):
            if (day := _bar_day(bar)) in volume_days:
                row[(side, day)] = float(bar.get("v") or 0)
        if open_interest:
            row[(side, "oi")] = float(open_interest.get(occ, 0))
    out = []
    for expiry in sorted(rows):
        years = (session_close(expiry) - fetched).total_seconds() / YEAR_SECONDS
        if years <= 0:
            continue
        ks = np.array(sorted(rows[expiry]))
        cols = {}
        for side in ("C", "P"):
            bid, ask = np.full(len(ks), np.nan), np.full(len(ks), np.nan)
            for i, k in enumerate(ks):
                row = rows[expiry][k]
                if (q := row.get(side + "q")) is None:
                    continue
                b, a, t = q
                mid = (a + b) / 2
                if b > 0 and a > b and mid >= MIN_PRICE and (a - b) <= MAX_RELATIVE_SPREAD * mid and newest - t <= QUOTE_MAX_AGE:
                    bid[i], ask[i] = b, a
            cols[side] = (bid, ask)
        volumes = {
            key: tuple(np.array([rows[expiry][k].get((side, key), 0.0) for k in ks]) for side in ("C", "P"))
            for key in volume_days + (("oi",) if open_interest else ())
        }
        out.append(Slice(expiry, years, ks, *cols["C"], *cols["P"], volumes))
    return out


# ----- one expiration ------------------------------------------------------------


def implied_forward(s: Slice, rate: float = RATE) -> float | None:
    """Put-call parity at the strikes where calls and puts are closest in price."""
    cm, pm = (s.call_bid + s.call_ask) / 2, (s.put_bid + s.put_ask) / 2
    both = np.isfinite(cm) & np.isfinite(pm)
    if not both.any():
        return None
    k, diff = s.strikes[both], (cm - pm)[both]
    nearest = np.argsort(np.abs(diff))[:5]
    return float(np.median(k[nearest] + math.exp(rate * s.years) * diff[nearest]))


def black(forward: float, k: np.ndarray, years: float, vol: np.ndarray, call: np.ndarray) -> np.ndarray:
    """Undiscounted Black prices."""
    sq = vol * math.sqrt(years)
    d1 = (np.log(forward / k) + sq * sq / 2) / sq
    d2 = d1 - sq
    c = forward * _cdf(d1) - k * _cdf(d2)
    return np.where(call, c, c - forward + k)


def implied_vols(forward: float, k: np.ndarray, years: float, price: np.ndarray, call: np.ndarray) -> np.ndarray:
    """Black volatilities for undiscounted prices (bisection, every strike at once);
    NaN where the price is outside the no-arbitrage bounds."""
    intrinsic = np.where(call, np.maximum(forward - k, 0), np.maximum(k - forward, 0))
    upper = np.where(call, forward, k)
    ok = np.isfinite(price) & (price > intrinsic + 1e-9) & (price < upper)
    lo, hi = np.full(k.shape, 1e-3), np.full(k.shape, 5.0)
    p = np.where(ok, price, 0)
    for _ in range(50):
        mid = (lo + hi) / 2
        below = black(forward, k, years, mid, call) < p
        lo, hi = np.where(below, mid, lo), np.where(below, hi, mid)
    vol = (lo + hi) / 2
    return np.where(ok & (vol > 2e-3) & (vol < 4.9), vol, np.nan)


@dataclass(frozen=True)
class Smile:
    """An expiration's implied volatilities from out-of-the-money options."""

    expiry: date
    years: float
    forward: float
    k: np.ndarray  # log-moneyness ln(K / F)
    iv: np.ndarray
    theta: float  # ATM total variance

    @property
    def atm_vol(self) -> float:
        return math.sqrt(self.theta / self.years)


def smile_of(s: Slice, rate: float = RATE) -> Smile | None:
    forward = implied_forward(s, rate)
    if forward is None or forward <= 0:
        return None
    df = math.exp(-rate * s.years)
    call = s.strikes >= forward
    mid = np.where(call, (s.call_bid + s.call_ask) / 2, (s.put_bid + s.put_ask) / 2)
    iv = implied_vols(forward, s.strikes, s.years, mid / df, call)
    good = np.isfinite(iv)
    if good.sum() < 3:
        return None
    k, iv = np.log(s.strikes[good] / forward), iv[good]
    theta = atm_total_variance(k, iv * iv * s.years)
    if theta is None:
        return None
    return Smile(s.expiry, s.years, forward, k, iv, theta)


def atm_total_variance(k: np.ndarray, w: np.ndarray) -> float | None:
    """Total variance at k = 0: a quadratic through the six strikes nearest the
    money (a line through the nearest one on each side if that's all there is)."""
    if k.min() > 0 or k.max() < 0:
        return None
    near = np.argsort(np.abs(k))[:6]
    if len(near) >= 4:
        c = np.polyfit(k[near], w[near], 2)[-1]
    else:
        below, above = k <= 0, k >= 0
        i, j = np.flatnonzero(below)[np.argmax(k[below])], np.flatnonzero(above)[np.argmin(k[above])]
        c = w[i] if k[i] == k[j] else w[i] + (w[j] - w[i]) * (0 - k[i]) / (k[j] - k[i])
    return float(c) if c > 0 else None


def fit_slice(sm: Smile) -> tuple[float, float, float] | None:
    """SSVI rho and phi for one expiration, its ATM variance held at the observed
    one; least squares in volatility over strikes within about 3.5 standard
    deviations. Returns (rho, phi, root-mean-square error in vol points)."""
    sd = math.sqrt(sm.theta)
    use = np.abs(sm.k) <= min(0.9, max(3.5 * sd, 0.05))
    if use.sum() < MIN_POINTS:
        return None
    k, iv, theta, t = sm.k[use], sm.iv[use], sm.theta, sm.years

    def search(rhos: np.ndarray, us: np.ndarray) -> tuple[float, float, float]:
        rho, u = np.meshgrid(rhos, us, indexing="ij")  # u = phi * sqrt(theta)
        phi = np.minimum(u / sd, ssvi_phi_cap(theta, rho))
        w = ssvi(theta, k[None, None, :], rho[..., None], phi[..., None])
        err = np.sqrt(np.mean((np.sqrt(np.maximum(w, 0) / t) - iv) ** 2, axis=-1))
        i, j = np.unravel_index(np.argmin(err), err.shape)
        return float(rho[i, j]), float(phi[i, j]), float(err[i, j])

    rho, phi, _ = search(np.linspace(*RHO_RANGE, 39), np.geomspace(0.02, 2.0, 50))
    u = phi * sd
    rho, phi, err = search(
        np.clip(np.linspace(rho - 0.05, rho + 0.05, 21), *RHO_RANGE), np.geomspace(u / 1.2, u * 1.2, 21)
    )
    return rho, phi, err * 100


# ----- realized volatility -------------------------------------------------------


def realized_vol(closes: np.ndarray, days: int, drop: int = 0) -> float | None:
    """Annualized volatility of the last `days` daily log returns, leaving out the
    `drop` largest moves (earnings gaps)."""
    if len(closes) < days + 1:
        days = len(closes) - 1
    if days < 15:
        return None
    r = np.diff(np.log(closes[-days - 1 :]))
    if drop:
        r = r[np.argsort(np.abs(r))[: len(r) - drop]]
    return float(np.sqrt(np.mean(r * r) * TRADING_DAYS))


def realized_window(years: float) -> int:
    """Trading days of history to compare with an expiration this far out: as
    long as the expiration, but at least half a year (a calm or wild month alone
    made the premium swing from about 1 to 2)."""
    return int(np.clip(round(years * TRADING_DAYS), REALIZED_MIN_DAYS, TRADING_DAYS))


# ----- the whole chain -----------------------------------------------------------


def _reports_before(earnings: Earnings | None, today: date, expiry: date) -> int:
    """Real reports between now and `expiry`: the known next one, then one a quarter."""
    if earnings is None or earnings.event_day > expiry or earnings.event_day <= today:
        return 0
    return 1 + (expiry - earnings.event_day).days // DAYS_BETWEEN_REPORTS


def earnings_move(smiles: list[Smile], earnings: Earnings | None, today: date) -> float | None:
    """The implied move: ATM variance of the first expiration after the report,
    less the variance of the days without news, at the rate the last expiration
    before the report implies (or, if there's none at least 3 days out, the
    two expirations after it). None if the report is past or can't be measured."""
    if earnings is None or earnings.event_day <= today:
        return None
    before = [s for s in smiles if earnings.spans(s.expiry) is False and s.years >= 3 / 365]
    after = [s for s in smiles if earnings.spans(s.expiry) and s.expiry < earnings.event_day + timedelta(days=DAYS_BETWEEN_REPORTS - 7)]
    if not after:
        return None
    first = after[0]
    if before:
        rate = before[-1].theta / before[-1].years
    elif len(after) >= 2:
        rate = (after[1].theta - first.theta) / (after[1].years - first.years)
    else:
        return None
    jump = first.theta - rate * first.years
    return round(math.sqrt(jump), 4) if rate > 0 and jump > 0 else None


def _nodes(xs: list[float], ys: list[float], lo: float | None = None, hi: float | None = None) -> list[list[float]]:
    """Kernel-smoothed values at the TENOR_NODES (in log tenor) that have data near them."""
    if not xs:
        return []
    lx, y = np.log(np.array(xs)), np.array(ys)
    out = []
    for node in TENOR_NODES:
        wt = np.exp(-0.5 * ((lx - math.log(node)) / NODE_BANDWIDTH) ** 2)
        if wt.sum() >= MIN_NODE_WEIGHT:
            v = float(np.sum(wt * y) / wt.sum())
            if lo is not None or hi is not None:
                v = float(np.clip(v, lo, hi))
            out.append([node, round(v, 4)])
    return out


def surface(fits: list[tuple[Smile, float, float]]) -> dict | None:
    """One SSVI surface from per-expiration fits: rho by tenor, and eta and gamma
    from regressing log phi on log theta."""
    if len(fits) < 2:
        return None
    rho = _nodes([f[0].years for f in fits], [f[1] for f in fits], *RHO_RANGE)
    theta = np.array([f[0].theta for f in fits])
    phi = np.array([f[2] for f in fits])
    # log phi + log(1 + theta) = log eta - gamma (log theta - log(1 + theta))
    x = np.log(theta) - np.log1p(theta)
    y = np.log(phi) + np.log1p(theta)
    if np.ptp(x) > 0.5:
        gamma = float(np.clip(-np.polyfit(x, y, 1)[0], *GAMMA_RANGE))
    else:
        gamma = 0.5
    eta = float(np.exp(np.median(y + gamma * x)))
    return {"rho": rho, "eta": round(eta, 4), "gamma": round(gamma, 4)}


def premium(smiles: list[Smile], closes: np.ndarray | None, earnings: Earnings | None, move: float | None, today: date) -> list[list[float]]:
    """The volatility risk premium at each tenor node: ATM IV over realized
    volatility. When the implied earnings move is known, its variance comes out
    of the IV and the biggest moves (a report a quarter) out of the realized
    volatility; otherwise both keep their earnings."""
    if closes is None or len(closes) < 22:
        return []
    xs, ys = [], []
    for sm in smiles:
        if not SHAPE_MIN_DAYS / 365 <= sm.years <= SHAPE_MAX_YEARS:
            continue
        theta = sm.theta - _reports_before(earnings, today, sm.expiry) * (move or 0) ** 2
        days = min(realized_window(sm.years), len(closes) - 1)
        rv = realized_vol(closes, days, math.ceil(days / 63) if move is not None else 0)
        if theta <= 0 or not rv:
            continue
        xs.append(sm.years)
        ys.append(math.sqrt(theta / sm.years) / rv)
    return _nodes(xs, ys)


def expiration_pattern(expiries: list[date], today: date) -> dict:
    """Which expirations the symbol lists, relative to `today`: options.expirations
    lays the same pattern over any simulated date."""

    def months_ahead(d: date) -> int:
        return (d.year - today.year) * 12 + d.month - today.month

    listed = set(expiries)
    # Weeklies: Fridays from this week's on, as long as each is listed.
    friday = today + timedelta(days=(4 - today.weekday()) % 7)
    if (this_week := trading_day_on_or_before(friday)) not in listed and this_week <= today:
        friday += timedelta(weeks=1)  # this week's expired before the fetch (today, or a holiday's Thursday)
    weekly_days, run_fridays = 0, set()
    while (d := trading_day_on_or_before(friday)) in listed:
        weekly_days, friday = (d - today).days, friday + timedelta(weeks=1)
        run_fridays.add(d)
    short, monthly, quarter_ends, month_ends = set(), set(), [], []
    short_days = 0
    for d in expiries:
        third = trading_day_on_or_before(nth_weekday(d.year, d.month, 4, 3))
        end = trading_day_on_or_before(date(d.year + d.month // 12, d.month % 12 + 1, 1) - timedelta(days=1))
        week_friday = trading_day_on_or_before(d + timedelta(days=4 - d.weekday()))
        if d == third:
            monthly.add(months_ahead(d))
        elif d in run_fridays:
            continue
        elif d == end:
            (quarter_ends if d.month % 3 == 0 else month_ends).append(months_ahead(d))
        elif d != week_friday:
            short.add(d.weekday())
            short_days = max(short_days, (d - today).days)
    run = 0  # consecutive monthlies (this month's may have expired already)
    while run + 1 in monthly:
        run += 1
    far = sorted(m for m in monthly if m > run)
    return {
        "short_weekdays": sorted(short),
        "short_days": short_days,
        "weekly_days": weekly_days,
        "monthly_months": run,
        "quarterly_months": sorted({(today.month - 1 + m) % 12 + 1 for m in far if m <= 12}),
        "leaps_months": sorted({(today.month - 1 + m) % 12 + 1 for m in far if m > 12}),
        "max_months": max(monthly, default=0),
        "quarter_end_months": max(quarter_ends, default=-1),
        "month_end_months": max(month_ends, default=-1),
    }


def _spread_of(chain: list[Slice], smiles: dict[date, Smile], day: date, key: date | str | None = None) -> dict:
    """One day's option volume (or, with `key` "oi", open interest as of `day`):
    in total, per expiration in each tenor bucket (as a share of the total),
    the puts' share, and each side's mean and standard deviation across strikes
    in standard deviations of the move to expiry."""
    total = puts = 0.0
    by_bucket, counts = np.zeros(len(VOLUME_BUCKETS) + 1), np.zeros(len(VOLUME_BUCKETS) + 1)
    z_sum = {"call": [0.0, 0.0, 0.0], "put": [0.0, 0.0, 0.0]}  # weight, sum z, sum z^2
    for s in chain:
        calls, put_volume = s.volumes[day if key is None else key]
        here = float(calls.sum() + put_volume.sum())
        total, puts = total + here, puts + float(put_volume.sum())
        b = volume_bucket((s.expiry - day).days)
        by_bucket[b] += here
        counts[b] += 1
        sm = smiles.get(s.expiry)
        if sm is None:
            continue
        z = np.log(s.strikes / sm.forward) / math.sqrt(sm.theta)
        keep = np.abs(z) < 6
        for side, v in (("call", calls), ("put", put_volume)):
            acc = z_sum[side]
            acc[0] += float(v[keep].sum())
            acc[1] += float((v * z)[keep].sum())
            acc[2] += float((v * z * z)[keep].sum())
    strikes = {}
    for side, (wt, sz, szz) in z_sum.items():
        if wt > 0:
            mean = sz / wt
            strikes[side] = [round(mean, 3), round(max(0.3, math.sqrt(max(szz / wt - mean * mean, 0))), 3)]
    per_expiry = np.where(counts > 0, by_bucket / np.maximum(counts, 1) / max(total, 1), 0)
    return {"total": total, "puts": puts, "per_expiry": per_expiry, "zero_day": by_bucket[0] / max(total, 1), "strikes": strikes}


def volume_shape(
    chain: list[Slice], smiles: dict[date, Smile], complete: date, live: date | None, stock_volume: float | None
) -> dict | None:
    """How the symbol's option volume spreads out, from the last complete session
    (`complete`, when `stock_volume` shares traded) and, if given, today so far
    (`live`). The complete session's options that expired that day are gone from
    the chain, so the mix comes from today when there's enough of it, and the
    complete session's total is scaled up by today's share of same-day options;
    without today, same-day expirations get the next bucket's share."""
    done = _spread_of(chain, smiles, complete)
    if done["total"] <= 0 or not stock_volume:
        return None
    today = _spread_of(chain, smiles, live) if live else None
    mix = today if today and today["total"] > 0 and today["strikes"] else done
    per_share = done["total"] / stock_volume
    per_expiry = mix["per_expiry"].copy()
    if mix is today:
        per_share /= max(1 - today["zero_day"], 1 - MAX_ZERO_DAY_SHARE)
    elif per_expiry[0] == 0:
        per_expiry[0] = per_expiry[1]
    return {
        "per_share": round(float(per_share), 5),
        "put_share": round(mix["puts"] / mix["total"], 3),
        "per_expiry": [round(float(x), 4) for x in per_expiry],
        "strikes": mix["strikes"],
        "day": (live if mix is today else complete).isoformat(),
    }


def open_interest_shape(chain: list[Slice], smiles: dict[date, Smile], today: date, average_volume: float | None) -> dict | None:
    """Like volume_shape, for open interest: contracts open per share the stock
    trades on an average day, spread out across expirations, sides and strikes."""
    if not chain or "oi" not in chain[0].volumes or not average_volume:
        return None
    oi = _spread_of(chain, smiles, today, "oi")
    if oi["total"] <= 0 or not oi["strikes"]:
        return None
    return {
        "per_share": round(float(oi["total"] / average_volume), 5),
        "put_share": round(oi["puts"] / oi["total"], 3),
        "per_expiry": [round(float(x), 4) for x in oi["per_expiry"]],
        "strikes": oi["strikes"],
    }


def calibrate(
    symbol: str,
    snapshots: dict[str, dict],
    fetched: datetime,
    closes: np.ndarray | None = None,
    volume_day: date | None = None,
    stock_volume: float | None = None,
    live_day: date | None = None,
    earnings: Earnings | None = None,
    open_interest: dict[str, int] | None = None,
    average_volume: float | None = None,
) -> dict:
    """The summary saved for one symbol (see the module docstring). `closes` are
    its real daily closes, oldest first; `volume_day` the last complete session
    and `stock_volume` the shares traded in it; `live_day` today, if the
    session has been open long enough to measure its volume; `open_interest`
    each contract's (by OCC symbol) and `average_volume` the shares the stock
    trades on an average day."""
    today = fetched.astimezone(MARKET_TZ).date()
    days = tuple(d for d in (volume_day, live_day) if d is not None)
    chain = slices(symbol, snapshots, fetched, days, open_interest)
    smiles = [sm for s in chain if (sm := smile_of(s)) is not None]
    by_expiry = {sm.expiry: sm for sm in smiles}
    move = earnings_move(smiles, earnings, today)
    fits = []
    for sm in smiles:
        if not SHAPE_MIN_DAYS / 365 <= sm.years <= SHAPE_MAX_YEARS:
            continue
        if earnings and earnings.event_day > today:
            spans = earnings.spans(sm.expiry)
            if spans is None or (spans and sm.expiry < earnings.event_day + timedelta(days=EARNINGS_WINDOW_DAYS)):
                continue
        if (fit := fit_slice(sm)) is not None:
            fits.append((sm, fit[0], fit[1], fit[2]))
    shape = surface([(f[0], f[1], f[2]) for f in fits])
    return {
        "symbol": symbol,
        "fetched": fetched.isoformat(),
        "as_of": today.isoformat() if is_trading_day(today) else trading_day_on_or_before(today).isoformat(),
        "contracts": sum(len(s.strikes) for s in chain),
        "expirations": len(chain),
        "fitted": len(fits),
        "fit_error": round(float(np.median([f[3] for f in fits])), 2) if fits else None,
        "shape": shape,
        "premium": premium(smiles, closes, earnings, move, today),
        "earnings": {"day": earnings.day.isoformat(), "timing": earnings.timing, "move": move} if earnings else None,
        "pattern": expiration_pattern([s.expiry for s in chain], today) if chain else None,
        "volume": volume_shape(chain, by_expiry, volume_day, live_day, stock_volume) if volume_day else None,
        "open_interest": open_interest_shape(chain, by_expiry, today, average_volume),
    }
