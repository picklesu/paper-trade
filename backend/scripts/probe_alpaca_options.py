"""Fetch one symbol's option chain from Alpaca's free (indicative) feed and summarize
what it contains: contract count, pages, rate limit, field coverage, expirations.

    cd backend && uv run python scripts/probe_alpaca_options.py SPY

Uses the keys in backend/.env (see README, Real prices). Never prints them.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from paper_trade.realprices import credentials, tls_context

BASE = "https://data.alpaca.markets/v1beta1"


def get(creds, path: str, **query) -> tuple[int, dict, dict | bytes]:
    url = f"{BASE}{path}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"APCA-API-KEY-ID": creds.key_id, "APCA-API-SECRET-KEY": creds.secret})
    try:
        with urllib.request.urlopen(request, timeout=20, context=tls_context()) as response:
            return response.status, dict(response.headers), json.load(response)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()[:300]


def main(symbol: str) -> None:
    creds = credentials()
    if creds is None:
        sys.exit("no Alpaca keys in backend/.env")
    start, pages, snapshots, token, headers = time.time(), 0, {}, None, {}
    while True:
        query = {"feed": "indicative", "limit": 1000} | ({"page_token": token} if token else {})
        code, headers, body = get(creds, f"/options/snapshots/{symbol}", **query)
        if code != 200:
            sys.exit(f"HTTP {code}: {body!r}")
        pages += 1
        snapshots.update(body.get("snapshots", {}))
        token = body.get("next_page_token")
        if not token:
            break
    print(f"{symbol}: {len(snapshots)} contracts, {pages} pages, {time.time() - start:.1f}s, rate limit {headers.get('X-Ratelimit-Limit')}/min")
    fields = ("latestQuote", "latestTrade", "greeks", "impliedVolatility", "dailyBar", "minuteBar", "prevDailyBar")
    print("contracts with each field:", {f: sum(1 for v in snapshots.values() if v.get(f)) for f in fields})
    expirations = sorted({k[len(symbol) : len(symbol) + 6] for k in snapshots})
    print(f"{len(expirations)} expirations (YYMMDD):", expirations[:6], "…", expirations[-3:])
    if snapshots:
        sample = sorted(snapshots)[len(snapshots) // 2]
        print(sample, json.dumps(snapshots[sample])[:600])


if __name__ == "__main__":
    main(sys.argv[1].upper() if len(sys.argv) > 1 else "SPY")
