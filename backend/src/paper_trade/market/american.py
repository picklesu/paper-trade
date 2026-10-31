"""American-style option prices with discrete dividends (pure numpy, vectorized).

Dividends are escrowed: the stock's price is cut by the present value of the
dividends due before expiration (they are a share of the price when they go ex, so
that present value is the same share of today's price). Calls then follow Black's approximation: the
larger of the European call on that price and a European call expiring just
before each ex-dividend date (exercising early to collect the dividend). Puts
follow Bjerksund and Stensland (1993), which prices early exercise of a put
with no dividend yield (it captures most, not all, of the early-exercise premium:
about 1% of the price at the money); a put is never worth less than exercising it now.
"""

import math

import numpy as np

SQRT_2PI = math.sqrt(2 * math.pi)


def _cdf(x):
    """The standard normal CDF (Zelen and Severo, error below 7.5e-8)."""
    t = 1 / (1 + 0.2316419 * np.abs(x))
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    upper = np.exp(-0.5 * x * x) / SQRT_2PI * poly
    return np.where(x >= 0, 1 - upper, upper)


def european(spot, k, years, vol, rate, carry=None):
    """Black-Scholes-Merton (call, put) with cost of carry `carry` (default: the rate)."""
    b = rate if carry is None else carry
    t = np.maximum(years, 1e-9)
    sq = vol * np.sqrt(t)
    d1 = (np.log(spot / k) + (b + vol * vol / 2) * t) / sq
    d2 = d1 - sq
    growth, disc = np.exp((b - rate) * t), np.exp(-rate * t)
    call = spot * growth * _cdf(d1) - k * disc * _cdf(d2)
    put = k * disc * _cdf(-d2) - spot * growth * _cdf(-d1)
    return call, put


def _phi(s, t, gamma, h, i, r, b, vol):
    sq = vol * np.sqrt(t)
    kappa = 2 * b / vol**2 + (2 * gamma - 1)
    lam = (-r + gamma * b + 0.5 * gamma * (gamma - 1) * vol**2) * t
    d = -(np.log(s / h) + (b + (gamma - 0.5) * vol**2) * t) / sq
    return np.exp(lam) * s**gamma * (_cdf(d) - (i / s) ** kappa * _cdf(d - 2 * np.log(i / s) / sq))


def _bs93_call(s, k, t, r, b, vol):
    """Bjerksund-Stensland 1993 American call with carry b; where b >= r early
    exercise never pays and the European price is returned."""
    t = np.maximum(t, 1e-9)
    base = european(s, k, t, vol, r, b)[0]
    with np.errstate(all="ignore"):
        beta = (0.5 - b / vol**2) + np.sqrt((b / vol**2 - 0.5) ** 2 + 2 * r / vol**2)
        b_inf = beta / (beta - 1) * k
        b_zero = np.maximum(k, np.where(r != b, r / (r - b), np.inf) * k)
        h = -(b * t + 2 * vol * np.sqrt(t)) * k**2 / ((b_inf - b_zero) * b_zero)
        trigger = b_zero + (b_inf - b_zero) * (1 - np.exp(h))
        alpha = (trigger - k) * trigger ** (-beta)
        value = (
            alpha * s**beta
            - alpha * _phi(s, t, beta, trigger, trigger, r, b, vol)
            + _phi(s, t, 1, trigger, trigger, r, b, vol)
            - _phi(s, t, 1, k, trigger, r, b, vol)
            - k * _phi(s, t, 0, trigger, trigger, r, b, vol)
            + k * _phi(s, t, 0, k, trigger, r, b, vol)
        )
        value = np.where(s >= trigger, s - k, value)
    usable = (b < r) & np.isfinite(value) & (beta > 1)
    return np.where(usable, np.maximum(value, base), base)


def american_put(spot, k, years, vol, rate):
    """An American put on a stock with no dividend yield: the call formula with the
    stock and strike swapped, a zero rate and the carry set to minus the rate."""
    return _bs93_call(k, spot, years, 0.0, -rate, vol)


def prices(spot, k, years, vol, rate, fraction=0.0, ex_times=()):
    """(call, put) American prices. Each dividend is `fraction` of the stock's price
    when it goes ex, so its present value is that fraction of today's price and the
    escrowed price is spot x (1 - fraction) per dividend ahead. `ex_times`: years
    until each ex-date, earliest first (arrays broadcasting with `years`; zero or
    less: already past)."""
    keep = np.ones(np.broadcast(spot, years).shape)
    before = []  # what the dividends ahead of each ex-date leave of the price
    for t in ex_times:
        before.append(keep)
        keep = keep * np.where(t > 0, 1 - fraction, 1.0)
    s_star = spot * keep
    call, put = european(s_star, k, years, vol, rate)
    for t, earlier in zip(ex_times, before, strict=True):
        # Exercise just before the ex-date: a call on the price less earlier dividends.
        early = european(spot * earlier, k, np.where(t > 0, t, 1.0), vol, rate)[0]
        call = np.where(t > 0, np.maximum(call, early), call)
    put = np.maximum(put, american_put(s_star, k, years, vol, rate))
    return np.maximum(call, spot - k), np.maximum(put, k - spot)
