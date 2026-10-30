"""Compare Alpaca's free (indicative) option quotes with Cboe's public delayed quotes,
contract by contract: spread widths, mid prices and sizes for deltas 0.15-0.85.

    cd backend && uv run python scripts/compare_option_spreads.py AAPL SPY KO TSLA

Uses the keys in backend/.env. Cboe's quotes are 15 minutes old, which adds noise.
Checked 2026-10-30 (Phase 7.5): medians agreed for stocks (AAPL 0.96x, KO 1.00x, TSLA
1.00x; SPY 1.57x) and mids within 0.8%, but both sources were far wider than the real
best quotes (SPY at the money about $0.07 against a penny or two), so option spreads
and sizes stayed the model's.
"""

import json
import sys
import urllib.request

import numpy as np

from paper_trade.optiondata import fetch_chain
from paper_trade.realprices import credentials, tls_context

CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"


def main(symbols: list[str]) -> None:
    creds = credentials()
    if creds is None:
        sys.exit("no Alpaca keys in backend/.env")
    for symbol in symbols:
        with urllib.request.urlopen(CBOE.format(symbol), timeout=60, context=tls_context()) as response:
            cboe = {o["option"]: o for o in json.load(response)["data"]["options"]}
        rows = []
        for occ, snap in fetch_chain(symbol, creds).items():
            q, c = snap.get("latestQuote") or {}, cboe.get(occ)
            if not c or not q.get("bp") or not q.get("ap") or not c["bid"] or q["ap"] <= q["bp"] or c["ask"] <= c["bid"]:
                continue
            if 0.15 <= abs(c["delta"]) <= 0.85:
                rows.append((q["ap"] - q["bp"], c["ask"] - c["bid"], (q["ap"] + q["bp"]) / 2, (c["ask"] + c["bid"]) / 2,
                             q.get("bs", 0) + q.get("as", 0), c["bid_size"] + c["ask_size"]))
        if not rows:
            print(f"{symbol}: no contracts quoted by both")
            continue
        a = np.array(rows)
        ratio = a[:, 0] / a[:, 1]
        print(
            f"{symbol}: {len(a)} contracts | spread Alpaca/Cboe median {np.median(ratio):.2f} "
            f"(10th-90th percentile {np.percentile(ratio, 10):.2f}-{np.percentile(ratio, 90):.2f}) | "
            f"mids differ by {np.median(abs(a[:, 2] - a[:, 3]) / a[:, 3]):.2%} | "
            f"Cboe median spread {np.median(a[:, 1] / a[:, 3]):.2%} of the mid | "
            f"sizes Alpaca/Cboe {np.median((a[:, 4] + 1) / (a[:, 5] + 1)):.2f}"
        )


if __name__ == "__main__":
    main([s.upper() for s in sys.argv[1:]] or ["AAPL", "SPY"])
