# Walk-forward validation (8g)

87 quarter-end origins, 2003-05-27 to 2025-08-13; 500 paths each; training from 1993 (at least 520 weeks).

## Pass criteria on the base model (fixed before running)

- PASS: 1y return: 80% interval covers 65%-95% (got 83%)
- PASS: 1y return: outer 5% tails hold at most 20% (got 6%)
- FAIL: 1y volatility: outer 5% tails hold at most 20% (got 21%)
- PASS: 1y drawdown: outer 5% tails hold at most 20% (got 17%)
- PASS: 3y return: 80% interval covers 65%-95% (got 85%)
- PASS: 3y return: outer 5% tails hold at most 20% (got 8%)
- FAIL: 3y volatility: outer 5% tails hold at most 20% (got 24%)
- FAIL: 3y drawdown: outer 5% tails hold at most 20% (got 22%)
- PASS: 12-month crisis odds: Brier skill >= 0 against the base rate (got +0.19)

## Calibration (base)

| horizon | origins | stat | 50% covered | 80% covered | below 10% | above 90% | outer 5% |
|---|---|---|---|---|---|---|---|
| 1y | 87 | ret | 51% | 83% | 9% | 8% | 6% |
| 1y | 87 | vol | 43% | 69% | 16% | 15% | 21% |
| 1y | 87 | dd | 44% | 72% | 14% | 14% | 17% |
| 3y | 79 | ret | 59% | 85% | 11% | 4% | 8% |
| 3y | 79 | vol | 33% | 63% | 11% | 25% | 24% |
| 3y | 79 | dd | 38% | 70% | 15% | 15% | 22% |

Well calibrated: 50% / 80% covered, 10% below and above, 10% in the outer 5%. Too many outside both ends = too confident; many on one end = biased.

## Crisis within 3 / 12 months (base)

**3 months**: Brier 0.0693 against 0.0565 for the base rate known at the origin (skill -0.23); mean predicted 12.0%, observed 5.7%.

| predicted | origins | mean predicted | observed |
|---|---|---|---|
| 0.00-0.05 | 53 | 2.0% | 0.0% |
| 0.05-0.10 | 16 | 7.5% | 6.2% |
| 0.10-0.20 | 8 | 12.4% | 12.5% |
| 0.20-0.40 | 3 | 26.1% | 0.0% |
| 0.40-1.00 | 7 | 91.2% | 42.9% |

**12 months**: Brier 0.1001 against 0.1236 for the base rate known at the origin (skill +0.19); mean predicted 24.6%, observed 12.6%.

| predicted | origins | mean predicted | observed |
|---|---|---|---|
| 0.00-0.05 | 16 | 3.5% | 0.0% |
| 0.05-0.10 | 23 | 7.3% | 0.0% |
| 0.10-0.20 | 12 | 14.2% | 0.0% |
| 0.20-0.40 | 20 | 30.0% | 25.0% |
| 0.40-1.00 | 16 | 71.6% | 37.5% |

## Macro inputs and the constant-volatility baseline (lower CRPS / Brier is better)

| model | 1y CRPS | 3y CRPS | 3m Brier | 12m Brier |
|---|---|---|---|---|
| base | 0.0918 | 0.1684 | 0.0693 | 0.1001 |
| nomacro | 0.0848 | 0.1534 | 0.0611 | 0.1126 |
| lognormal | 0.0854 | 0.1454 | - | - |

Macro inputs do NOT improve both the return CRPS and the crisis Brier score over the no-macro model.

## STICKY

| STICKY | 1y CRPS | 3y CRPS | 3m Brier | 12m Brier | 1y 80% cover |
|---|---|---|---|---|---|
| 10 | 0.0944 | 0.1807 | 0.0768 | 0.1958 | 85% |
| 20 | 0.0893 | 0.1606 | 0.0753 | 0.1421 | 85% |
| 40 (current) | 0.0918 | 0.1684 | 0.0693 | 0.1001 | 83% |
| 80 | 0.0880 | 0.1566 | 0.0682 | 0.0860 | 84% |

A different STICKY would be warranted only by a margin of 5% in both CRPS and Brier.

## Reading this

- This tests the regime layer (weekly returns by state), not the whole engine: the engine adds fat-tailed gaps, volatility clustering inside a state and per-symbol noise, so its volatility and drawdown ranges are wider than these.
- A 'crisis' here is a week the full-sample fit's smoothed path calls crisis (a narrow label, 38 weeks since 1993). A model refit on early data may call a milder bear (2002) a crisis, so some of the crisis-odds miss is a label mismatch, not only model error.
- Origins a quarter apart overlap for 1 and 3 years, so the effective sample is about 25 years and 4-5 crises: gross miscalibration shows, fine tuning does not.
