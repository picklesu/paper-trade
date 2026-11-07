# Scheduled releases: SPY on release days

Event days are the release dates that fall on a session (1993 on); baseline: the 60 sessions before, without any release day. Multiples are of an ordinary day's size; the interval is a 90% bootstrap over events.


## fomc

| period | events | close-to-close | opening gap | after the open | volume vs 60d median | VIX change (points of log %) |
|---|---|---|---|---|---|---|
| 1993-2009 | 135 | 1.14x (1.00-1.28) | 0.86x (0.75-0.98) | 1.20x (1.05-1.37) | 1.41x (1.31-1.53) | -2.5 (-3.4 to -1.6) |
| 2010-2026 | 134 | 1.17x (1.01-1.33) | 0.81x (0.67-0.97) | 1.34x (1.18-1.51) | 1.31x (1.25-1.38) | -1.2 (-2.6 to +0.2) |
| all | 269 | 1.15x (1.05-1.25) | 0.83x (0.74-0.94) | 1.27x (1.16-1.39) | 1.36x (1.30-1.43) | -1.9 (-2.7 to -1.1) |

## jobs

| period | events | close-to-close | opening gap | after the open | volume vs 60d median | VIX change (points of log %) |
|---|---|---|---|---|---|---|
| 1993-2009 | 199 | 1.27x (1.14-1.40) | 1.59x (1.44-1.77) | 1.24x (1.12-1.37) | 1.35x (1.27-1.45) | -2.0 (-2.9 to -1.2) |
| 2010-2026 | 200 | 1.23x (1.11-1.35) | 1.27x (1.15-1.40) | 1.15x (1.03-1.27) | 1.24x (1.18-1.31) | -1.7 (-2.6 to -0.6) |
| all | 399 | 1.25x (1.16-1.34) | 1.43x (1.33-1.55) | 1.19x (1.11-1.29) | 1.30x (1.24-1.35) | -1.8 (-2.5 to -1.2) |

## cpi

| period | events | close-to-close | opening gap | after the open | volume vs 60d median | VIX change (points of log %) |
|---|---|---|---|---|---|---|
| 1993-2009 | 207 | 1.13x (1.03-1.24) | 1.30x (1.18-1.43) | 1.04x (0.94-1.14) | 1.34x (1.24-1.45) | -1.4 (-2.1 to -0.8) |
| 2010-2026 | 212 | 1.10x (0.99-1.21) | 1.15x (1.02-1.27) | 0.98x (0.88-1.08) | 1.17x (1.11-1.22) | -1.1 (-2.0 to -0.3) |
| all | 419 | 1.11x (1.04-1.19) | 1.22x (1.14-1.31) | 1.01x (0.93-1.08) | 1.25x (1.20-1.32) | -1.3 (-1.8 to -0.7) |

## gdp

| period | events | close-to-close | opening gap | after the open | volume vs 60d median | VIX change (points of log %) |
|---|---|---|---|---|---|---|
| 1993-2009 | 225 | 1.13x (1.02-1.24) | 1.06x (0.95-1.17) | 1.11x (1.01-1.21) | 1.25x (1.19-1.32) | -0.8 (-1.4 to -0.1) |
| 2010-2026 | 226 | 1.07x (0.95-1.19) | 0.97x (0.87-1.09) | 1.16x (1.04-1.28) | 1.19x (1.13-1.25) | -0.8 (-1.7 to -0.0) |
| all | 451 | 1.10x (1.02-1.18) | 1.02x (0.94-1.10) | 1.14x (1.06-1.22) | 1.22x (1.18-1.27) | -0.8 (-1.3 to -0.3) |

## pce

| period | events | close-to-close | opening gap | after the open | volume vs 60d median | VIX change (points of log %) |
|---|---|---|---|---|---|---|
| 1993-2009 | 210 | 1.02x (0.92-1.12) | 1.06x (0.93-1.19) | 1.02x (0.93-1.12) | 1.16x (1.09-1.24) | +0.8 (+0.2 to +1.5) |
| 2010-2026 | 200 | 1.17x (1.05-1.29) | 1.17x (1.04-1.30) | 1.15x (1.03-1.28) | 1.24x (1.17-1.31) | +0.3 (-0.6 to +1.2) |
| all | 410 | 1.09x (1.01-1.17) | 1.11x (1.02-1.21) | 1.08x (1.01-1.16) | 1.20x (1.15-1.25) | +0.6 (+0.0 to +1.1) |

## retail

| period | events | close-to-close | opening gap | after the open | volume vs 60d median | VIX change (points of log %) |
|---|---|---|---|---|---|---|
| 1993-2009 | 215 | 1.16x (1.07-1.27) | 1.21x (1.11-1.31) | 1.10x (1.01-1.20) | 1.28x (1.20-1.37) | -0.7 (-1.3 to -0.0) |
| 2010-2026 | 212 | 0.99x (0.90-1.08) | 1.01x (0.91-1.12) | 1.07x (0.97-1.18) | 1.13x (1.08-1.18) | -0.1 (-0.9 to +0.7) |
| all | 427 | 1.08x (1.01-1.15) | 1.11x (1.04-1.18) | 1.09x (1.02-1.16) | 1.21x (1.16-1.26) | -0.4 (-0.9 to +0.1) |

## Does the day's direction follow a crude surprise proxy?

* jobs (proxy: change in the unemployment rate (a rise is bad news)): 397 events (2 left out for a missing value), rank correlation of the proxy with the day's return -0.07; the day rose 55% of the 165 times the proxy was positive, 68% of the 124 times it was negative, and the 108 unchanged months are in the correlation only.
* cpi (proxy: acceleration of month-on-month inflation (a rise is bad news)): 417 events (2 left out for a missing value), rank correlation of the proxy with the day's return -0.04; the day rose 55% of the 213 times the proxy was positive, 58% of the 203 times it was negative, and the 1 unchanged months are in the correlation only.
