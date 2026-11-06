# Engine-level walk-forward checks (8g, second part)

Data through 2026-11-05; engine seeds 0-159; origins are the block ends nearest 2004-06-30, 2007-06-29, 2009-09-30, 2012-12-31, 2016-06-30, 2019-06-28, 2021-12-31, 2023-06-30.

8 origins (2004-06-29, 2007-06-28, 2009-10-01, 2012-12-27, 2016-06-27, 2019-06-27, 2022-01-03, 2023-07-03), 160 engine seeds each, 3 years forward; the regime model refit before each origin; the engine started as a new market there with only the real bars before it.

## Volatility and drawdown ranges: engine against the regime layer alone

Each cell: realized | simulated 10th / median / 90th percentile; PIT in brackets (0 to 1, where reality fell).

**1-year horizon**

| origin | start | stat | real | engine | regime layer |
|---|---|---|---|---|---|
| 2004-06-29 | calm | ret | +7% | -13% / +11% / +26% [0.40] | -0% / +18% / +33% [0.20] |
| 2004-06-29 | calm | vol | 10% | 7% / 10% / 20% [0.47] | 10% / 11% / 15% [0.22] |
| 2004-06-29 | calm | dd | 6% | 3% / 8% / 23% [0.31] | 4% / 7% / 15% [0.39] |
| 2007-06-28 | choppy | ret | -18% | -17% / +9% / +24% [0.09] | -10% / +12% / +27% [0.04] |
| 2007-06-28 | choppy | vol | 18% | 8% / 13% / 25% [0.76] | 10% / 13% / 19% [0.85] |
| 2007-06-28 | choppy | dd | 19% | 5% / 12% / 25% [0.80] | 5% / 10% / 21% [0.87] |
| 2009-10-01 | choppy | ret | +14% | -33% / +1% / +21% [0.81] | -29% / -4% / +22% [0.79] |
| 2009-10-01 | choppy | vol | 18% | 11% / 15% / 33% [0.62] | 17% / 20% / 24% [0.20] |
| 2009-10-01 | choppy | dd | 16% | 6% / 14% / 39% [0.53] | 11% / 19% / 33% [0.33] |
| 2012-12-27 | calm | ret | +26% | -11% / +14% / +26% [0.89] | -10% / +11% / +27% [0.88] |
| 2012-12-27 | calm | vol | 10% | 8% / 11% / 20% [0.44] | 10% / 13% / 20% [0.17] |
| 2012-12-27 | calm | dd | 4% | 3% / 7% / 21% [0.22] | 5% / 10% / 22% [0.07] |
| 2016-06-27 | choppy | ret | +19% | -20% / +5% / +22% [0.87] | -22% / +5% / +26% [0.82] |
| 2016-06-27 | choppy | vol | 9% | 10% / 14% / 27% [0.05] | 13% / 17% / 22% [0.00] |
| 2016-06-27 | choppy | dd | 4% | 6% / 12% / 30% [0.03] | 7% / 15% / 29% [0.01] |
| 2019-06-27 | calm | ret | +8% | -15% / +9% / +25% [0.46] | -14% / +9% / +26% [0.46] |
| 2019-06-27 | calm | vol | 29% | 8% / 13% / 25% [0.93] | 9% / 14% / 20% [0.98] |
| 2019-06-27 | calm | dd | 34% | 4% / 10% / 28% [0.94] | 4% / 11% / 23% [0.98] |
| 2022-01-03 | choppy | ret | -18% | -18% / +9% / +22% [0.09] | -11% / +10% / +29% [0.06] |
| 2022-01-03 | choppy | vol | 23% | 7% / 12% / 24% [0.88] | 10% / 15% / 20% [0.95] |
| 2022-01-03 | choppy | dd | 25% | 4% / 9% / 26% [0.88] | 5% / 11% / 24% [0.91] |
| 2023-07-03 | calm | ret | +24% | -9% / +9% / +24% [0.89] | -12% / +12% / +29% [0.81] |
| 2023-07-03 | calm | vol | 11% | 7% / 11% / 21% [0.49] | 10% / 14% / 20% [0.23] |
| 2023-07-03 | calm | dd | 9% | 3% / 9% / 21% [0.46] | 5% / 10% / 23% [0.39] |

**3-year horizon**

| origin | start | stat | real | engine | regime layer |
|---|---|---|---|---|---|
| 2004-06-29 | calm | ret | +23% | -12% / +32% / +68% [0.40] | +5% / +47% / +78% [0.22] |
| 2004-06-29 | calm | vol | 11% | 9% / 13% / 25% [0.27] | 10% / 13% / 17% [0.13] |
| 2004-06-29 | calm | dd | 7% | 8% / 20% / 43% [0.06] | 7% / 14% / 34% [0.09] |
| 2007-06-28 | choppy | ret | -29% | -30% / +19% / +51% [0.11] | -12% / +28% / +61% [0.05] |
| 2007-06-28 | choppy | vol | 27% | 9% / 17% / 26% [0.92] | 11% / 14% / 19% [1.00] |
| 2007-06-28 | choppy | dd | 56% | 11% / 25% / 47% [0.96] | 9% / 19% / 36% [0.98] |
| 2009-10-01 | choppy | ret | +33% | -53% / -4% / +37% [0.86] | -51% / -2% / +46% [0.82] |
| 2009-10-01 | choppy | vol | 17% | 12% / 18% / 28% [0.44] | 17% / 19% / 24% [0.12] |
| 2009-10-01 | choppy | dd | 19% | 13% / 30% / 57% [0.26] | 18% / 32% / 52% [0.11] |
| 2012-12-27 | calm | ret | +30% | -16% / +26% / +55% [0.56] | -23% / +27% / +58% [0.56] |
| 2012-12-27 | calm | vol | 12% | 9% / 13% / 22% [0.49] | 12% / 15% / 21% [0.19] |
| 2012-12-27 | calm | dd | 13% | 8% / 18% / 41% [0.29] | 10% / 20% / 41% [0.20] |
| 2016-06-27 | choppy | ret | +38% | -32% / +15% / +42% [0.85] | -21% / +20% / +58% [0.71] |
| 2016-06-27 | choppy | vol | 12% | 10% / 15% / 24% [0.19] | 13% / 16% / 21% [0.02] |
| 2016-06-27 | choppy | dd | 15% | 10% / 21% / 47% [0.25] | 12% / 21% / 40% [0.22] |
| 2019-06-27 | calm | ret | +34% | -36% / +21% / +49% [0.74] | -29% / +18% / +53% [0.70] |
| 2019-06-27 | calm | vol | 22% | 10% / 14% / 25% [0.84] | 12% / 16% / 21% [0.91] |
| 2019-06-27 | calm | dd | 34% | 9% / 21% / 51% [0.74] | 11% / 21% / 44% [0.79] |
| 2022-01-03 | choppy | ret | +23% | -15% / +24% / +52% [0.48] | -14% / +28% / +63% [0.42] |
| 2022-01-03 | choppy | vol | 16% | 9% / 13% / 22% [0.69] | 12% / 15% / 22% [0.65] |
| 2022-01-03 | choppy | dd | 25% | 9% / 19% / 40% [0.67] | 10% / 19% / 36% [0.71] |
| 2023-07-03 | calm | ret | +55% | -25% / +19% / +50% [0.94] | -14% / +28% / +62% [0.84] |
| 2023-07-03 | calm | vol | 13% | 9% / 13% / 22% [0.51] | 12% / 15% / 20% [0.27] |
| 2023-07-03 | calm | dd | 14% | 9% / 19% / 44% [0.34] | 10% / 20% / 39% [0.25] |

**Pooled over the origins** (share of outcomes inside the central 80% range; 80% is calibrated; with 8 origins a gap under about 20 points is noise)

| horizon | stat | engine | regime layer |
|---|---|---|---|
| 1y | ret | 75% | 75% |
| 1y | vol | 75% | 62% |
| 1y | dd | 75% | 50% |
| 3y | ret | 88% | 88% |
| 3y | vol | 88% | 62% |
| 3y | dd | 75% | 75% |

The engine's 1-year volatility range (10th to 90th percentile) is 1.90 times the regime layer's on average (per origin: 2.40, 1.81, 3.43, 1.22, 1.84, 1.56, 1.57, 1.39).

## STATE_LEVEL_ADJ: weekly volatility by state, annualized

| state | engine | fitted (mean of the refits) | history (full-sample labels) | engine vs history | within ±15% |
|---|---|---|---|---|---|
| calm | 10.6% | 9.6% | 8.9% | +20% | FAIL |
| choppy | 18.3% | 19.0% | 20.8% | -12% | PASS |
| crisis | 35.3% | 42.4% | 52.2% | -32% | FAIL |
History's states are the full-sample fit's smoothed labels, which have hindsight and make 'crisis' a narrow set of weeks; the refits' own volatility (third column) is the fairer yardstick for the engine, and a week counts toward a state only if all five sessions were in it.

## CRASH_BY_STATE: daily SPY moves of -3% or worse, and -5% or worse, per year

| state | level | engine per year | history per year | engine / history | within a factor of 1.5 |
|---|---|---|---|---|---|
| calm | -3% | 0.81 | 0.30 | 2.69 | FAIL |
| calm | -5% | 0.19 | 0.00 | inf | FAIL |
| choppy | -3% | 3.65 | 4.69 | 0.78 | PASS |
| choppy | -5% | 0.87 | 0.54 | 1.62 | FAIL |
| crisis | -3% | 16.67 | 49.07 | 0.34 | FAIL |
| crisis | -5% | 5.39 | 19.89 | 0.27 | FAIL |
| all | -3% | 3.19 | 3.08 | 1.03 | PASS |
| all | -5% | 0.86 | 0.65 | 1.32 | PASS |

History is every SPY day since 1993; its daily states are the weekly smoothed labels. A state with few real days (crisis: about 40 weeks) gives a noisy rate.

## Verdicts (criteria fixed in the script's docstring)

- STATE_LEVEL_ADJ: FAIL (every state's engine volatility within 15% of history)
- CRASH_BY_STATE: FAIL (every rate within a factor of 1.5 of history)
- SHOCK_IN_CRISIS: not checked (it only acts on a user-triggered shock; history has none)

Eight origins and 4-5 real crises: this shows a gross difference, not a fine one.
