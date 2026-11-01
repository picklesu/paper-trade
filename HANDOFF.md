# Handoff

_Last updated: 2026-11-01. Phases 8d (real daily data), the on-request update, 8e-1 (replay the real past), 8e-2 (real volatility) and the shock/sync fix (#49) are merged; the sync option was then removed, and all fetching moved out of the app into `./server.sh update` (see "Fetching real data" under Pick up here). The user restarts and tests them; **8f (the future model; the user approved 3 states with macro-driven transitions, the VIX-floor fix, and 8g as a separate PR) is built on `phase-8f-future-model`.** 8f is built (PR open; see its entry under Pick up here); **8g (walk-forward validation) is next, a separate PR.**_

## Where things stand

| Phase | Status | PR |
|---|---|---|
| 0–3. Skeleton, market engine, sessions, time travel, account, orders, order review | Merged; the user confirmed 2a–3 work | #1–#9 |
| CI (GitHub Actions: tests, build, lint on every PR) | Merged | #10 |
| 4. Interactive charts + 5 years of history | Merged | #11 |
| 5. Indicators | Merged | #12 |
| 5.5a. Customizable workspace (dockview): docking, floating windows, snapping, resize, maximize, Panels menu, saved layout | Merged | #13 |
| 5.5b. Collapse panels to edge strips (peek, pin; side panels keep their size) | Merged | #16 |
| 5.5b fixes from the user's testing: chart space (one-line quote, toolbar in the chart's tab bar, thinner bars, full screen), tab-overflow menu while peeking, resize snapping and bring-to-front | Merged | #17 |
| 5.5b redesign from the user's review: chart-first default (side panels collapsed on the left), no tab bars, Panels menu places panels, no date axis, range/bar size at the bottom | Merged | #18 |
| Fixes: divider drags snapping back on market ticks; Reset layout showing nothing after floating | Merged | #19 |
| Fix: with the chart floating, collapsed panels moved to the top/bottom and opened over the whole workspace | Merged | #20 |
| Activity panel removed (Orders' Filled list covers it); filled sells show their realized P/L | Merged | #21 |
| Panels on any edge (left, right, top, bottom), no floating windows; edge tabs open/collapse; Panels menu sets edge and On/Off | Merged | #22 |
| 5.6. More realistic prices (from the user's `improve_sim.docx` discussion) | Merged | #23 |
| 5.7. Engine redesign (performance for 40 symbols and options; user approved Claude's plan) | Merged | #25 |
| 5.8. Sync to real prices (the user chose Alpha Vantage; Stooq now blocks scripts) | Merged | #26 |
| 5.9. Real prices from Alpaca instead of Alpha Vantage (the user chose it over ThetaData and Yahoo) | Merged | #27 |
| Fix: Alpaca certificate checks on macOS (truststore) | Merged | #28 |
| 6. Watchlists and quotes (the user approved the proposal as is) | Merged | #29 |
| Fix: TradingView logo off the chart (user's request); the license's notice and link are a menu item at the bottom of the Panels menu; `NOTICE` holds the notice | Merged | #30 |
| 7. Options chain with Greeks, exchange holidays (the user approved Claude's proposal without trading) | Merged | #31 |
| 7.5. Option prices calibrated to real markets (the user approved the proposal as is) | Merged | #33 |
| 8a. Option trading, expiration, exercise, assignment (the user approved the proposal) | Merged | #35 |
| 8b. Dividends, American-style pricing, the real rate, a VIX-like index, uncertainty shocks (the user approved the proposal and added the shocks and a trigger button) | Merged | #39 |
| Fix: the jump cap counted the whole distance, so after a rewind you couldn't jump forward again; it now counts only sessions not yet generated | Merged | #40 |
| 8c. Real historical start prices from Tiingo (the user's request, after a jump to 2006 showed SPY at $757) | Merged | #41, #42 |
| Fix: the clock's date shows the year | Merged | #43 |
| 8d. Real daily data (Tiingo bars split-adjusted, FRED series) | Merged; the user restarts to download it | #44 |
| Update real history on request (fetch only new days; no automatic FRED refresh) | Merged | #46 |
| 8e-1. Replay the real past: prices, volume, closures, dividends, backfill (the user approved the design and the 8e-1/8e-2 split) | Merged; the user restarts to test | #47 |
| 8e-2. Volatility/options follow the real VIX; past earnings dates inferred | Merged; the user restarts to test | #48 |
| Fix (user's report): Back clears the shock error and the shock view explains the real past up front; Sync reworded ("latest real prices", no button in the real past); a backfill's bridging scale no longer rescales the real days (MODEL_VERSION 8) | Merged; the user restarts to test | #49 |
| README trimmed to the essentials (feature summary, optional keys, run/test, condensed roadmap) | Merged | #51 |
| Removed "Sync to latest real prices" (redundant with real-history replay); added the Last real day shortcut | Merged | #52 |
| All API fetching moved out of the app into `./server.sh update [prices\|history\|options]` (`update.py`); Real data dialog, `/api/real-prices*`, `/api/real-history/*` and background fetch tasks removed | Merged; the user restarts and runs `update` when wanted | #53 |
| Cleanup: fetch retry/progress bookkeeping removed | Merged | #55 |
| 8f. The future model: a long-lived regime (calm / correction / crisis) sets the market's drift, volatility level and selloff odds; starts from the regime filtered from the real past; VIX floor now about 9.8; top bar shows the regime | PR open; the user restarts to test | |

`main` is clean and up to date with GitHub.

## Pick up here

**Fetching real data (changed after 8e; the user's request): the app never calls an API.** Everything that
fetches lives in `backend/src/paper_trade/update.py` and runs on demand: `./server.sh update [prices|history|options]`
(default all). `update_prices` (Alpaca quotes, one request), `update_history` (Tiingo: symbols not yet downloaded in
full, then only the days since each last bar; a split refetches that symbol), `update_macro` (FRED likewise),
`update_options` (the Treasury rate, then Alpaca option chains fitted per symbol when older than 20 hours). They save into
the same SQLite tables as before (the data classes `RealPrices`, `PriceHistory`, `Macro`, `OptionData` are unchanged apart from
the removed scheduling); a manual run ignores earlier rate-limit holds; a rate limit stops the step and says to run it
again later. The server reads the saved data **at startup**, so restart after an update. The app has no sync, no Real data
dialog, no `/api/real-prices*` or `/api/real-history/*` endpoints, and no background fetch tasks; `engine.use_real` is gone
(RealBars is built once at startup). The Go to… dialog keeps a **Last real day** shortcut (the market's `real_through`).
**Sandbox warning:** the sandbox's `backend/.env` holds live keys, so running `update` there spends real requests
(Tiingo allows 50 an hour: a full first download is 40). Tests never read the keys (`tests/conftest.py`). Older notes
below that describe background tasks, `refresh_*`, `update_real_history`, `sync_real_prices` or the Real data dialog
describe what existed before this change.
**Cleanup done (fetch bookkeeping removed):** no retry holds (`*_retry` store keys), `RETRY_AFTER`, `OptionData.seconds_until_due`/`progress`/`finished`/`running`. A failure is only shown (`last_error`). Notes below that mention an hour's wait or retries describe the old app.

**The real-history plan (the user's request after 20 simulated years of SPY went $125 → $300; discussed
and agreed 2026-10-31; one PR per step, stop after each for the user to test):**
- *Goal:* the **past replays exactly** (every session up to the last real day follows its real daily
  bar and volume, so 2008, March 2020 and the bull and bear markets land on their real dates; the
  sim fills only the minutes and seconds inside each day). The **future is simulated by a model
  trained on the real history** (regime shifts = the dated big events, the flow between them = a
  slow drift and volatility driven by interest rates, the yield curve, credit spreads and so on),
  and it **starts from the real state of whatever date is simulated from** (regime odds, volatility,
  rates known up to that date; no look-ahead). Be honest with the user: nobody predicts a crash's
  date; the model gives calibrated odds and realistic bull/bear lengths, drawdowns and crash
  frequency. Accuracy is measured by walk-forward checks (train before a date, simulate forward, see
  how often reality falls inside the sim's range).
- *Decisions:* prices and volumes **split-adjusted** (dividends not; the engine models dividends
  itself); market-level regime-switching model (about 3 states: calm bull, correction/bear, crisis;
  transition odds depend on macro inputs; a crash is a jump with a state-dependent hazard); the 40
  symbols follow through the engine's betas and sector factors; training is an offline script that
  saves parameters in the repo (nothing trains at runtime); parameters fitted on the whole history
  have seen 2020 (fine for a simulator; the walk-forward checks use only earlier data).
- *Steps:* **8d real daily data** (merged, #44, #46) → **8e replay the real past** (merged: 8e-1 #47,
  8e-2 #48, fix #49) → **8f the future model** (**built, PR open**: fit, start state from any date,
  "Generate a new future" uses it) → **8g walk-forward validation**.
- **8d (merged, #44):** `pricehistory.py` now stores `Bar`s (day, open, high, low, close, volume,
  dividend, split) in a `daily` table (the old `closes` table is dropped on start and its symbols are
  fetched again, about 0.5 MB each, 1 request per symbol, 40 in all), split-adjusted by
  `parse_bars` (a bar before a split is divided by the factor, its volume multiplied; checked on
  AAPL 2014 and 2020: 2014-06-06 closes at $23.06). `PriceHistory.bars(symbol, start, end)`,
  `last_day`, `price_on`/`listed_from` as before. Store keys moved to `daily_history*`.
  `macro.py` (new): `Macro` fetches ten FRED series (`FRED_API_KEY` in `backend/.env`; DFF, DGS3MO,
  DGS2, DGS10, T10Y2Y, BAA10Y, VIXCLS, UNRATE, CPIAUCSL, USREC, from 1985) into a `macro` table, again
  about daily (`MarketService._refresh_macro`); `series(name, start, end)`, `latest(name, day)` (what
  was known on a day). `/api/real-prices` has `macro: {configured, available, total, last_error}`. The
  sandbox firewall blocks a new host until the user approves it (`Approval required for
  api.stlouisfed.org`); `_refusal` now says so instead of "rejected the API keys". Tests never read
  the Tiingo or FRED keys. Live check: AAPL and SPY bars and all ten FRED series fetched once
  (VIX 79.13 on 2008-10-24, the curve inverted in March 2007, recession flag on in April 2020).
  Review changes: `Macro.latest(name, day)` applies publication lags (`LAG_DAYS`: UNRATE 35 days, CPIAUCSL 45,
  USREC 365; market series 0; revisions ignored) so a start state never sees unpublished data; a failing
  series is noted and the rest carry on (only `FetchStop`, a refusal for every series, stops the loop for
  an hour); FRED's HTTP 400 for a bad key is a `FetchStop` saying to check `FRED_API_KEY`. Left as is:
  the key rides in FRED's query string (required; errors never show the URL); the 40-symbol refetch;
  rows are saved on the event loop (tens of ms per symbol).
  After merging, the user restarts; the app then downloads 40 symbols (Tiingo's allowance is 50 an
  hour: if it stops, it retries in an hour and keeps what it has) and the FRED series. A reset to the
  past is refused until the first symbol is saved.
- **Update on request (after 8d, the user's request):** nothing fetches new days by itself. Go to… → Real data… →
  **Update real history** (`POST /api/real-history/update`, `MarketService.update_real_history`)
  asks Tiingo only for the days from each saved symbol's last bar (that bar included, since it may have been
  saved before its close) and FRED only from each series' last value; symbols/series not yet downloaded are
  left to the background fetch. A split among the new days makes it fetch that symbol's whole history again
  (earlier bars need re-adjusting). The old automatic daily FRED refresh is gone (`Macro.due` = never fetched).
  Store workers never touch SQLite: `last_day` is read on the loop thread and passed to `fetch_new`.
  Status: `/api/real-prices` `history` has `newest` and `updated`. Backend 373, frontend 167.
- **8e-1 (built; the user approved the design and the split):** `market/realbars.py` `RealBars` (built at
  startup by `main` from `PriceHistory`; the engine never touches the database) gives `day(d)` (a `RealDay`:
  open/high/low/close/volume per symbol, `has` mask), `dividends`, `close_before`, `covers`, `closures()`.
  `engine._generate` calls `_pin` after the minute highs/lows exist: a linear correction through anchors (real
  open, the knots nearest the generated high and low, real close) keeps the generated noise in between; leftovers
  are folded into the real range; the minutes holding the extremes are set exactly; `_pin_volume` scales minute
  volumes to the real day total. Bad bars are sanitized (high ≥ open/close, low ≤). Checked on the real data:
  185 symbol-days in 2008–09 match exactly; 5-year backfill 4.0 s. `real_through` (saved in the market state)
  is the newest real day *when the market began*: a market keeps its data, `engine.use_real` (called after
  Update real history) only reaches markets started from then on (reset, rewinds before the history).
  `MODEL_VERSION` 8 (7 in 8e-2, 6 in 8e-1); `from_state` raises `ValueError` if the bars it replayed are gone, and `load_market` then
  continues as a new market. Backfill starts from the real close before the first history day (symbols
  without bars still start at 1.0 and are scaled; real symbols scale 1.0). Calendar: `calendar.set_overrides`
  (closed, opened) from `RealBars.closures()`, applied once at startup (real closures found: 1985-09-27, 1994-04-27,
  2001-09-11..14, 2004-06-11, 2007-01-02, 2012-10-29/30, 2018-12-05; rule holidays that traded, such as MLK Day
  before 1998, are opened); new closures after an update need a restart. Dividends: `engine.dividend` and
  `engine.dividend_schedule(col, start, end, spot)` give the real ex-dates and amounts on replayed days (broker
  payments, option escrow and early-assignment use them), the spec's model otherwise.
  **Known approximations (8e-2 and 8f deal with them):** the sim's own earnings/news/crash/dividend draws still feed the
  GARCH variance on replayed days (the pinned path overrides the prices only); option pricing's volatility,
  VIX and earnings calendar are still the model's; the order of a day's high and low is the generated one; the
  last real bar is re-fetched by an update, so its session regenerates if fetched before the close.
- **8e-2 (built; approved with 8e-1):** on replayed days `_vix_variance(day)` sets the market variance multiple
  from the real VIX close of `day` (`h = ((VIX / VIX_BIAS)² − VIX_BASE2) / VIX_SLOPE`, clipped to `VARIANCE_RANGE`;
  constants measured: sim index² ≈ 70 + 295·h, and over 2007–2012 once set from the real VIX the sim index has
  correlation 0.92 and +2% bias (`VIX_BIAS` 1.106 removes the +10% the day's own move added). It is the
  next session's start variance, and the first session of a backfill or reset uses the VIX before it, so there is no
  look-ahead. Each symbol's own variance is filtered from its real return less beta times SPY's real return
  (`_filter_own`). Replayed days have no shock, no sim crash, no sim earnings/news/dividend draws, so the sim's
  draws no longer feed the variance there; spreads, seconds' noise and options' `vol_ratio` follow the real
  state, and the state at the last real day is what 8f starts from. `RealBars` takes the VIX (`Macro.series("VIXCLS")`,
  passed from `main`/`update_real_history`). Earnings: `engine.earnings_dates(col, start, end)` infers the
  reaction day near each model date (±10 days) as the busiest-and-widest day relative to the previous 20
  sessions and to SPY's same-day ratio (`RealBars.spike_near`, score ≥ 1.1), else keeps the model's date;
  options (`reports`, spread widening, the chain's earnings flag) use it. Spot-check from memory of 7 stocks'
  2022–23 reports: 39 of 56 within a day vs 16 of 56 for the model's dates; megacaps and market-wide days are
  the misses. Live check: 2008-10-10 15:59 sim VIX 67.4 vs real close 69.95.
  `clock_state.real_through` says how far the real days run (the dialog uses it). A backfill's scale to the origin's prices now
  applies only to simulated days (`engine.backfill_scale_on(day)`; real days stay exactly real, the first simulated day opens with the gap).
  The real past can't be shocked (`trigger_shock` raises, the API says 422). Known limits: sim VIX can't go under about 11.8 (the real lows are 9–10); earnings reaction days are inferred, not
  looked up; no real earnings calendar before today.
- **8f (built; MODEL_VERSION 9).** `market/regime.py` (pure numpy) + `regime_params.json` (fitted offline by `backend/scripts/fit_regime.py --db PATH`; `scripts/check_regime.py` simulates it against SPY's history; both read a scratch copy of the `daily` and `macro` tables). A 3-state hidden Markov model on **weekly** SPY returns (`PERIOD` 5 sessions; daily fits flip states every few days and give only volatility levels): calm (about +16%/yr drift, 9% vol), correction (+1%, 20%), crisis (−46% floored, 52%); logit transitions on four macro features known the day before (10y-2y curve, BAA credit spread, log VIX, 12-month unemployment change; `feature_panel` applies the publication lags), a persistence prior (`STICKY`) and a calibration step to match history (time in each state, crisis drift floor, long-run return). The future has no macro path, so each state carries fitted AR(1) feature dynamics (`step`). Simulated 2000 years vs SPY 1993-2026: return 8.0% vs 8.5%, vol 16.8% vs 16.7%, drawdowns over 20% 2.3 vs 2.4 per 20 years, crisis time 2.5% vs 2.3%. **Macro inputs show weak effect (credit, VIX, jobs push toward crisis); whether they add skill is 8g's job.**
  - *Engine:* `_DayStart` has `state`, `z` (standardized features) and `phase` (the state may change when it is 0, every `period` sessions; saved in the market state's `starts`). `_generate` (simulated days only) steps the chain from `draws.regime_u/regime_z`, scales the market variance by the state's level (`_state_variance` = `variance_multiple` × `STATE_LEVEL_ADJ`, which makes crises hit the target; GARCH now centers on `omega × level`; `VARIANCE_RANGE` floor 0.03), adds the state's drift tilt (`_state_tilt`: fitted drifts plus the volatility drag and selloff loss the engine takes off again) and scales selloff odds (`CRASH_BY_STATE`). Random uncertainty shocks are off (`SHOCKS_PER_YEAR` 0: crisis entries replace them); the Trigger button still works and sends the market into the crisis state at the onset. Real days carry the state the filter finds from real returns (`RealBars.regime_on` → `RealRegime`; needs the macro series, so `RealBars.from_history` passes them), and the first simulated day steps from it. A market with no real data starts calm. Top bar: `engine.regime` is now the state (Calm / Correction / Crisis); the intraday regimes still drive minute texture and options.
  - *Checks:* history labels right (2008 and Mar 2020 crisis, 2017 calm, 2022 correction); real start (2026-10-30) is calm, VIX-derived variance 0.58; one 5-year path from it: SPY 758 → 1269, vol 10.7%, VIX 8.5 / 12 / 45. Pooled 16 seeds × 30 years: vol 17.3%, log price return 4.4% (what SPY's spec drift of 7% gives after dividend drops and volatility drag, so no bias), average variance 1.07, crisis realized vol 42% vs fitted 52%. Review fixes: backfilled starts keep the regime chain (`_start_at` builds origin and backfill starts alike); options, the VIX and `vol_ratio` use the variance in force (`_Skeleton.variance`); a trigger sets crisis even over a stronger leftover shock; the first simulated day after the real past keeps the VIX's variance un-rescaled.
  - *Known limits:* the weekly Gaussian emissions miss fat tails inside a state (the engine's t gaps cover part); `STATE_LEVEL_ADJ` and `CRASH_BY_STATE` are hand-tuned; the filter's state at the last real day is its argmax at the last week boundary.
- **8f plan (history; built above).** What existed to build on:
  - *Inputs:* `PriceHistory.bars(symbol)` (SPY from 1993, others from their listing), `Macro.series(name)` for
    DFF, DGS3MO, DGS2, DGS10, T10Y2Y, BAA10Y, VIXCLS, UNRATE, CPIAUCSL, USREC (from 1985), and
    `Macro.latest(name, day)` for what was known on a day (publication lags applied: no look-ahead).
  - *Where the future starts:* after `engine.real_through` (the newest real day when the market began) sessions
    are the old model: GARCH market variance, the three intraday regimes, random news/crash/shock/earnings
    draws, drift from each spec. The start state at the last real day is already real-derived: `_DayStart.variance`
    from the real VIX (`_vix_variance`), `own_variance` filtered from real returns (`_filter_own`), prev closes
    real. 8f should replace the sim's market-level drift, volatility level, crash/shock hazard (and the
    regime-switching between calm / correction / crisis) with a model fitted offline on the real history (script in
    the repo, parameters saved in the repo, nothing trains at runtime); "Generate a new future" and any session
    past the real days use it, starting from the real state of the date simulated from.
  - *Plumbing to remember:* `MODEL_VERSION` is 8 (bump it for any change to generated sessions: saved markets then
    continue as new ones); a market keeps its `real_through`; `engine.use_real` only reaches new markets;
    `calendar.set_overrides` closures are applied once at startup; the real past can't be shocked.
  - *Honest framing for the user:* nobody predicts a crash's date. The model gives calibrated odds, realistic bull/bear
    lengths, drawdowns and crash frequency; 8g checks it by walk-forward tests (train before a date, simulate forward,
    see how often reality falls in the sim's range).
  - *Known limits carried over:* the sim VIX can't go below about 11.8 (real lows are 9–10, `VIX_SLOPE`/`VIX_BASE2`
    were measured on one calibration); past earnings days are inferred from volume (about 70% within a day of the
    truth in a 56-report spot check; the model's own dates were about 29%); the order of a day's high and low is
    generated; one-off closures found after an update need a restart; the sim's own earnings/news/crash draws no
    longer feed variance on real days.
  - *Checking in the sandbox:* copy the `daily` and `macro` tables from the user's `backend/paper_trade.db`
    (open it read-only with `mode=ro`; never write to it) into a scratchpad database, build `RealBars` and an engine
    from that (scripts like the ones used for 8e: pin check, VIX tracking, earnings spot check). The user's
    real account is in that file.

**Phase 8c (merged, #41):** going before the
history used to reset the market at *today's* real prices (SPY at $757 in 2006). Now:
- `backend/src/paper_trade/pricehistory.py` (new): `PriceHistory` fetches each symbol's whole daily history
  once from Tiingo (`TIINGO_API_KEY` in `backend/.env`, already added by the user; one request per symbol,
  `columns=date,close`, about 160 KB) into a `closes` table (symbol, day, close; unadjusted for later splits).
  `price_on(symbol, day, drift)` gives the close on or before `day`, or, before the symbol's first close,
  that close brought back by the symbol's drift (`back_cast`).
- `MarketService._refresh_price_history` is the background task (2 s between symbols, stops on any error);
  `real_start_prices(day)` builds the previous closes for a reset: real close, else today's real price
  brought back by drift, else the spec price (None if nothing real at all). `/api/real-prices` has
  `history: {configured, available, total, last_error}`; no frontend uses it yet.
- Tests: `tests/test_pricehistory.py` (10); `test_realprices.py` updated (a reset now back-casts the saved price). Backend 326 pass.
- **Not listed yet (the user's request):** a symbol doesn't exist before its first real close
  (`PriceHistory.listed_from`, `MarketService.is_listed/listed_from`; unknown while its history isn't
  fetched = always listed). Hidden from quotes (`listed_quotes`: ticks, `/api/quotes`), chart bars
  (`MarketService.bars` cuts everything before the listing; a week's bar stays if the listing is in it),
  orders and options (`Broker.listed`, `option_chain`: "X wasn't trading yet"). `/api/symbols` has
  `listed_from`; the chart, ticket and options panels say "X wasn't trading yet on <date>"; watchlist
  rows show dashes. The engine still generates all 40, so other prices are unchanged. After listing the
  price follows the sim's path (not snapped to the real first close).
- **Live check (2026-10-31):** one run fetched 34 of 40 symbols, then Tiingo returned 429 at JNJ (the
  allowance was shared with earlier probes). SPY 2006-01-03 close is 126.70 as expected; a market at
  2006-01-03 quotes 37 symbols (AVGO, META, TSLA, V hidden). **Left:** the 6 symbols from JNJ on fetch in
  the app's background after the hourly retry (a symbol Tiingo doesn't know is skipped for good; only a rate limit or rejected key stops the loop for an hour).
- **Don't waste Tiingo/Alpha Vantage requests** (see the user's feedback): no probes or polling; make the
  one real fetch count and keep the data. If full-history requests turn out to count for more than one,
  switch to fetching only the date range a jump needs and cache it.

**Phase 8b (built as approved; the user added the shocks and the trigger button):**
- *Interest rate:* `optiondata.fetch_treasury_rate` (Alpha Vantage `TREASURY_YIELD`, daily 3-month, made continuous)
  is fetched about daily (`MarketService.refresh_rate`, saved as `risk_free_rate`); `Calibration.rate` carries it
  (default 4%) into every pricing call. No key: 4% stays.
- *Dividends:* `SymbolSpec.dividend_yield/_months/_day` (a built-in table `symbols.DIVIDENDS`, roughly the real
  payers' yields and ex-months; **not fetched from Alpaca**: its corporate-actions response format couldn't be
  checked from the sandbox; a real-schedule sync is still open). Each payment is yield ÷ payments × the previous
  close; the price drops by that at the ex-date's open (`engine.dividend_days`, `_dividend_on`, `dividend`).
  A spec's drift is its total return, so the continuous part makes up the difference. `MODEL_VERSION` 5.
  Cash: `Broker._pay_dividends` at each close pays `DIVIDEND_LAG` (3) sessions after the ex-date on shares held
  when the ex-date opened, as a filled `dividend` record (type in `EVENT_TYPES`; ledger adds `qty × price` to cash
  and changes no position; rewind erases it; `_busy` is true while stock is held). Orders shows "Dividend ·
  Paid 0.55 a share · +$55.00".
- *Option prices:* `market/american.py`: escrowed dividends (each is a share of the price when it goes ex, so
  the escrowed price is spot × (1 − q) per dividend ahead), Black's approximation for calls (max over each
  ex-date), Bjerksund–Stensland 1993 for puts (captures most, about 60%, of the early-exercise premium at the
  money: roughly 1% of the price off a 1500-step tree; its tests say so). `options.price` prices with it; the
  spread width still uses the European delta of the escrowed price; `price(..., greeks=True)` (chain and
  `contract_quote`) measures Greeks by bumping the American price. The sticky-strike anchor includes the
  dividends. **Dividend assignment:** the close before an ex-date a short ITM call whose time value is less than
  the dividend is assigned with chance 0.9 × (1 − time value / dividend) (`DIVIDEND_ASSIGNMENT`; reason
  "Assigned before the $X dividend"). Exercising a long call for the dividend is the existing manual exercise.
- *Uncertainty shocks (Bloom 2009):* `_DayStart.shock` is the extra volatility (excess; the multiple is
  1 + excess), set at onset to 0.4–0.9 and decaying with a 60-session half-life; it scales market and own
  loadings, volume and spread factors, and options' `vol_ratio`/`option_inputs`. At onset the market drops
  `SHOCK_DROP` (2.5%) × excess × beta, then drifts up a little (`SHOCK_REBOUND`). Random arrivals: 0.3 a year
  (`draws.shock_u`). The baseline volatility is divided by `SHOCK_VARIANCE` and the drift loss added back, so
  long-run volatility and return still match each spec. **Trigger a shock** (Go to… footer link →
  `POST /api/shock` → `MarketService.trigger_shock` → `engine.trigger_shock`): begins at the *next session's
  open* (not mid-session), saved in the market state (`shocks`: onset day → excess, trigger time); sessions
  already generated beyond today are regenerated; going back to before the trigger undoes it (the history from
  that day is deleted). Ticks carry `shock` and `shock_pending`; the top bar shows "Shock" / "Shock at open".
- *VIX-like index:* `options.vix`: Cboe's variance formula over a strip of strikes of a 30-day expiration of SPY
  (puts below the forward, calls above, the model's smile, European, default calibration so history and live
  agree; a year-long run gives a median near 15, a 95th percentile of 33–55). The regime in force counts only a
  quarter (`VIX_REGIME_WEIGHT`: it lasts hours, the index looks 30 days ahead). The engine samples it at the
  open, every half hour and the close (`_Skeleton.vix`, computed in `_generate`, so the 5-year history gets it
  too at no real cost), stores 30-minute bars as symbol `VIX` in `bars30` (volume 0), and quotes it
  (`vix_quote`, `MarketService.index_quotes`, appended to ticks and `/api/quotes`). `/api/symbols` marks it
  `tradable: false`; orders on it are refused; no options; the chart offers 30m and up (finer sizes map to 30m)
  and hides volume; sparklines work. "All symbols" gains it once (`Watchlists` `seen`). Seeds: a volatile
  regime on top of GARCH and a shock can still take the index past 100 (the base model's regimes did that
  before shocks existed).
- Tests: backend 316, frontend 164.

**Phase 8a (option trading) is done.** Phase 8b is built (above). Next: whatever the user tests and asks for;
the open item is syncing the real dividend schedule from Alpaca.

**Phase 8a (built as approved, with the review's changes; PR #35):** buy/sell to open and close;
covered calls and cash-secured puts only; market and limit; fills from `options.price` at each
second (the chain uses the same function, so a market order fills at the ask shown); expiration
by exception at the close; unexercisable longs sold at the 3:59 PM bid; early assignment of deep
in-the-money shorts (chance 0.2 × (1 − time value / $0.05) at each close, a draw keyed to the
session's last minute seed so replays repeat it); early exercise (`POST /api/options/exercise`);
going back in time removes exercise/assignment/expiration records. Decisions made while
building: exercise and assignment are booked as the option closing at its intrinsic value plus
the shares trading at the stock's price (cash nets to the strike; the option's P/L shows whether
the trade paid); the order's effect (open/close) is inferred from the position (an order can't
do both); a held contract's expiration and strike stay listed. Clicking a chain price fills the
ticket with a limit at that price, so it may rest if the quote moves before it's placed.

**Mid fills (after 8a, the user's request):** "Mark" is called **Mid** everywhere (chain field `mid`,
column between Bid and Ask, shown by default; saved columns moved to
`paper-trade.options.columns.v2` with Mid added). Clicking a Mid fills the ticket at the mid
rounded to the cent and keeps the ticket's side (`TicketRequest.side` null). A resting option
limit strictly inside the spread can fill at its price: per minute, a chance 1 - exp(-rate)
with rate = (contract's expected session volume, `options.expected_volume`, at least
`MIN_DAY_VOLUME` 40) / 390 / 2 x x² (x: how far into the spread toward the other side; a quarter
at the mid), priced at each minute's first second. The draw (the minute's seed and the order id)
decides the minute *and the second* it fills, worked out once per order and session
(`Broker._mid_fill_seconds`), so live play (about a second per tick) and jumps give the same
fills, and replays repeat them. With a one-cent spread there's no price inside it, so a mid click
rounds onto the bid or ask. The review gives the typical wait (`_wait`).

Decisions so far (2026-10-30):
- **Layout presets and "narrow screens" (one panel at a time over the chart) are dropped**
  (they were the 5.5c steps). Don't propose them again.
- **Done in 5.7:** 40 symbols, sector factors, volume following moves, spreads widening with
  volatility, single-stock clustering (the realism items discussed after 5.6).
- **Browser performance (discussed with the user):** done in 6: stream subscriptions (only
  on-screen symbols' quotes) and per-row re-renders (`mergeQuotes` keeps unchanged quotes as the
  same object; watchlist rows are memoized). Not done: deltas (at 1× nearly every quote changes
  each tick, so little to gain yet), virtualized long tables, options priced on the server
  (vectorized Black-Scholes; SVI smile) and what-if math in a Web Worker, for Phase 7+. WASM was
  ruled out: the costs are data and DOM, not math.
- **Real prices (5.8, 5.9):** the user asked for prices close to real ones and chose option 2 (sync
  to real closes now, keep the simulation). In 5.9 the user switched the source to Alpaca
  (free, all symbols per request; its free history back to ~2016 can serve Phase 12). Option 3, discussed and deferred to Phase 12: real
  5-year daily history (minutes bridged through real daily bars) plus parameters calibrated from
  it; real days would only replay.
- **Phase 7 (approved proposal, "without trading"):** chain + Greeks + holidays. Option trading
  moved to Phase 8, which the user's approval implies: buy calls/puts and sell to close, covered
  calls and cash-secured puts (a cash account allows no other option sales), positions with
  value/P&L/Greeks, clicking a chain bid/ask to fill the ticket, then expiration, exercise and
  assignment (shares delivered, early assignment). Propose the details before building.
- **Half days (early 1:00 PM closes) were not built** (in the Phase 7 proposal; told the user):
  the engine, 30-minute bars, clock arithmetic and day-order expiry all assume 390-minute
  sessions. Those days are full sessions. Revisit only if the user asks.
- **Later realism:** a VIX-like index and dividends with Phase 8 (dividends change option prices
  and early exercise).
- **Phase 7.5 (built as proposed; decisions made while building):**
  - *Order:* IV level = the sim's vol (Phase 7, with today's realized-move nudge) × the
    volatility risk premium for the tenor; the real fit gives only shape (SSVI rho by tenor,
    eta, gamma; phi(theta) so the smile scales with the sim's level); sticky strike (smile
    anchored at the day's open) and selloff skew last.
  - *Premium:* real ATM IV (earnings variance removed) ÷ realized vol over max(tenor, 126)
    trading days (the largest moves dropped for stocks with earnings). A one-month window made
    SPY's ratio swing to 1.6, so the window is at least half a year. Each symbol's value is
    pulled halfway to the median of all symbols' and held to 1.0–1.4; default 1.12. On
    2026-10-30 the results were 1.0–1.23.
  - *Earnings:* the real implied move is measured (Alpha Vantage calendar; the jump at the
    first expiration after the report, less the diffusive rate from the last expiration before
    it) and **shown only**. Pricing uses the sim's own earnings move × the premium: the sim's
    gaps come from its specs, so pricing with the real move would misprice them (a free edge to
    sellers or buyers). Expirations within 30 days after a real report are left out of shape fits.
  - *Spreads and sizes (item 5): the model's are kept.* Alpaca's indicative quotes matched Cboe's
    public delayed quotes in the median (AAPL 0.96×, KO 1.00×, TSLA 1.00×, SPY 1.57×; mids
    within 0.8%; `scripts/compare_option_spreads.py`), but both were far wider than the real
    best quotes (SPY ATM about $0.07, KO $0.11 on a $1.60 option), so neither is real NBBO.
    Spreads are now wider in the first minutes (×2 fading over ~10 min) and in the 3 sessions
    before the sim's earnings (×1.25). The model's SPY spreads (1% of price at the money) are
    too wide for real SPY options (a penny or two); tighten them if the user notices.
  - *Volume:* contracts per share from the last complete session (its same-day expirations are
    gone from the chain, so the total is scaled up by today's share of same-day volume, at
    most ×2), the mix (tenor buckets, put share, strike spread in sd) from today's volume
    once the session is an hour old. *Open interest* is real (Alpaca's trading API contract
    list, `paper-api.alpaca.markets`, needs `expiration_date_lte` or it lists only this week's).
  - *Expirations:* the real list becomes a pattern (`calibration.expiration_pattern`: short
    weekdays, weekly run, consecutive monthlies, quarterly months, LEAPS months, month/quarter
    ends) that `options.expirations` lays over any date; on the fetch date it reproduced the real
    lists of 8 symbols with nothing missing and at most 2 extras. New kind "quarterly" (Q).
    **Phase 8 must keep a held contract's expiration listed** even if a new calibration's
    pattern drops it.
- **Alpaca option data** (`backend/scripts/probe_alpaca_options.py SYMBOL`): snapshots
  `GET https://data.alpaca.markets/v1beta1/options/snapshots/{underlying}?feed=indicative&limit=1000`,
  paged with `next_page_token` (quotes with sizes on every contract, IV/Greeks on ~75%,
  `dailyBar`/`prevDailyBar` volumes); 200 requests/min. A full sync of 40 symbols took 87s.
- **Phase 6 decisions (Claude's, within the approved proposal):** the lookup opens on any letter
  except F (full screen's shortcut) or "/"; default lists "Main" (the original 10) and "All
  symbols"; columns and sort are per browser (localStorage), lists are on the server; default
  columns Last, Chg %, Today (sparkline), as the panel is narrow.

**The user is not QA**: verify everything (tests, build, lint with `--deny-warnings`, a
headless-browser check that **looks at screenshots**, green CI) before merging, and don't ask
them to test each phase.

## Going back in time (decided by the user; built in 2a + 2b)

Picking an earlier date/time shows a confirmation that lists the trades to be erased and refunded,
the cash before and after, and held positions valued at that time's prices. The user then picks
**Replay the same prices** or **Generate a new future**. Going before the market's history
starts a new market and resets the account. See `Broker.rewind` / `preview_rewind` and
`MarketEngine.rewind`.

## Phase 2b decisions (from the user)

- **Saved across restarts** in SQLite (`backend/paper_trade.db`, git-ignored; `PAPER_TRADE_DB`
  overrides the path). On restart the market resumes at its saved time.
- **Cash account only:** no margin and no short selling. Buying power = cash minus the cash set
  aside for working buy orders (limit/stop-limit at the limit, stop at the stop, market at the ask).
- Day orders expire at the 4:00 PM close. **Reset account…** is in the bottom tab bar (choose
  the starting cash).

## Architecture

- **backend/** (Python 3.14, FastAPI, numpy, uv)
  - `market/calendar.py`: sessions are 9:30 AM–4:00 PM ET, Monday–Friday, except NYSE holidays
    (7: `nyse_holidays(year)` with today's rules for every year 1900–3000, Juneteenth from 2022,
    Saturday holidays observed Friday except New Year's Day, Sunday ones Monday; `HOLIDAYS` and a
    numpy `busdaycalendar` passed to every busday call). No half days. `MODEL_VERSION` 4 marks
    the change: older saves continue as a new market and history is rebuilt.
    `snap`, `add_trading_seconds`, `trading_days_between` and so on use numpy business-day
    arithmetic, so they take constant time however far apart the dates are.
  - `market/clock.py`: `SimClock`. Market time = anchor + real elapsed × speed, counted in
    open-market seconds, so the 4:00 PM close continues at the next 9:30 AM. Supports
    pause/resume, `set_speed` and `set_time`. The real-time source is injectable for tests.
  - `market/engine.py`: `MarketEngine` (**redesigned in 5.7, `MODEL_VERSION = 3`**). A session is
    generated in one go at **one-minute resolution**, its `_Skeleton`: each minute's open, high,
    low and close (as log prices) and volume for every symbol, plus the regimes, spread factors and
    loadings. Charts, stored bars, history and day high/low all come from it. **Seconds are
    generated on demand, per symbol** (`_fine(day, m, cols)`, LRU of 64 minutes holding the
    columns generated so far; `_seconds` does the work): the current minute for quotes and
    minutes where an order could fill. A minute's seconds are a bridge through its open, high (at
    the peak of the shared noise), low (at its dip) and close, folded back inside the range, so
    seconds always agree exactly with the bars (`test_seconds_agree_with_minute_bars`); the noise is
    the same market/sector/own mix, so symbols co-move second to second. **How the skeleton is
    made:** unit paths (market factor, one per sector, one per symbol) are drawn at the knots (each
    minute's first and last second) with variances accumulated per second (regime, intraday shape,
    GARCH); symbol log prices = loadings × paths + drift (lognormal-corrected, regime tilt) +
    jumps; each minute's high/low is drawn from the **exact distribution of a Brownian bridge's
    maximum/minimum** given its open and close, shifted for once-a-second sampling
    (Broadie–Glasserman–Kou, 0.5826 σ√Δt), and snapped to the open/close when within half a
    second's move. **Model:** market factor + sector factor (`SymbolSpec.sector`,
    `sector_corr`) + own noise; regimes (scale the market factor more, tilt the drift; the move
    into second r follows the regime of minute (r-1)//60); GJR-GARCH on the market factor's daily
    variance and a GARCH on each symbol's own variance (`own_variance` in `_DayStart`);
    `VARIANCE_RANGE` (0.25, 25) is only a safety net (a cap of 8 biased volatility ~7% low); Student's
    t gaps; quarterly earnings gaps (`_earnings_on`), news jumps **between two minutes** (so no
    jump falls inside a minute), selloffs at the open; intraday volatility shape; volume U-shaped ×
    regime × √variance × activity (squared standardized move, `ACTIVITY_BASE` 0.7) × news burst ×
    earnings; spread factor per minute = market volatility vs normal, clipped to [1, 5]
    (`bid_ask(price, factor)`). Long-run vol and drift match each spec (tests over 10 years).
    **Random draws:** one stream per path keyed by name (`zlib.crc32`), for the skeleton and
    for seconds, so adding a symbol leaves other symbols' prices unchanged (tested) and one
    symbol's seconds can be generated alone. `quotes` is cached per market time. `bid_ask` and
    `bid_ask_arrays` work in whole cents with the same operations, so they agree exactly. **New future:** a new epoch takes over from the first whole minute at or
    after its start (that minute's moves from its open on), so prices up to `t` are unchanged with
    no mid-minute splicing. `backfill_iter(days)` generates history forward from prices of 1.0,
    oldest first, then sets `backfill_scale` and commits the scaled starts. Engine API used
    elsewhere: `quotes`, `minute_bars(day, through)`, `minute_range(day, col)` and
    `first_hit(day, col, lo, hi, candidates, test)` (order matching), `price_at`, `prices_at`,
    `spread_factor(t)`, `day_prices` (slow; tests only). **Saved markets** record `model` and
    `symbols`; `main.load_market` calls `continue_older` when either differs (old 10-symbol saves
    map by `LEGACY_SYMBOLS`; new symbols start at spec prices). `scripts/bench_engine.py [N]`
    benchmarks it. At 40 symbols, 5.6's engine vs 5.7's: fast-forward a year 9.1s → 0.58s,
    5-year history 49s → 3.1s (about 4s in the app including the database), memory 145MB → 7MB,
    a tick 0.09ms either way.
  - `market/history.py`: `History`, the chart bars. Finished sessions are stored as 30-minute
    bars in a `bars30` table (recorded via `engine.on_day_complete`, in batches during long
    jumps and the history build). `bars(symbol, interval, end, count)` serves 30m/1h/1D/1W from
    the table, plus today's live bars, and serves 1m/5m/15m from the engine's paths (looking back
    60/252/252 sessions). Paging back with `end` never splits a bar or a week. A replay rewind
    hides later stored days (`day < today`); a new future deletes them (`delete_from`); a reset
    clears everything.
  - `market/symbols.py`: 10 symbols (SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, META, TSLA, AMD).
    Prices are synthetic.
  - `market/service.py`: `MarketService` owns the clock, engine, store and `Broker`. A
    background loop runs `sync()` every 0.5s (advance the engine to `clock.now()`, then
    `broker.process(t0, t1)`; if orders changed, `sync()` itself saves and sends an `orders`
    message, so every caller gets that) and sends `tick` messages, which include the account. It saves the market about every 5s and after any trade,
    jump or clock change. It skips ticks while paused or during a jump. `goto()` handles
    forward (sessions, then order matching, both yielding), rewind (engine + broker) and reset
    (engine + broker). The final state after a jump is always sent with `force=True`.
    `goto_details()` builds the rewind/reset confirmation contents. `build_history()` runs in the
    background at startup and after a reset: it records missing finished sessions and backfills
    `HISTORY_DAYS` (5 years) if `engine.backfilled` is false (about 8s), sending `history`
    progress messages, via `History.backfill_iter` (it holds the unit-scale 30-minute bars
    and stores them scaled once `backfill_scale` is known). The backfill commits all at once
    at the end, so an interrupted build leaves the market unchanged and simply runs again on
    the next start; a build that needs the backfill clears `bars30` first (old bars from an
    interrupted build or an older model). Jumps are refused (409) while it runs. `clock_state` includes
    `session_open`, `timeline` (bumped on every jump and when history is done, so charts reload)
    and `building_history`.
  - `realprices.py` (5.8; **Alpaca since 5.9**, replacing Alpha Vantage's 25 requests a day): one
    request to `https://data.alpaca.markets/v2/stocks/snapshots?symbols=…&feed=iex` returns all
    symbols (free Basic plan: IEX prices in real time, 200 requests a minute). Price = latest trade,
    else the daily bar's close; trading day from its timestamp in New York. Keys from
    `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` (environment or `backend/.env`, git-ignored;
    **never commit them or put them in PR text**). Errors become user-readable `FetchError`s
    (401/403 rejected keys, 429, unreachable); some error bodies are HTML and aren't shown.
    `RealPrices` saves prices in the store (`real_prices`: price, trading day, fetched time), the
    last attempt (`real_prices_attempt`) and the last problem (`real_prices_error`, cleared by a
    success; missing symbols are named). `MarketService.refresh_real_prices(older_than)` fetches in
    a worker thread (the store is only touched on the loop thread: SQLite connections are per
    thread) under a lock, so a second caller waits instead of fetching again (the symbol list is
    captured before the request). The background task fetches at startup, then
    `seconds_until_refresh` decides: 15 minutes after the last fetch, or 1 minute after a failed
    one; it checks the wall clock at least every minute, since a sleeping Mac
    pauses asyncio's clock. GET `/api/real-prices` (the Real data dialog) fetches first when the last
    fetch is over a minute old, waiting at most 5s (the fetch carries on, shielded). The old "Sync to latest real prices" (`sync_real_prices`, `engine.reanchor`, POST
    `/api/real-prices/sync`) was removed once real history replay made it redundant. New markets (going back
    before the history) start from saved real prices (`real_start_prices`). `tests/conftest.py`
    stops tests from ever reading the keys or calling the service. UI: Go to… → "Real data…" (a footer link):
    real history through the newest trading day with Update real history, and the option-fit status.
    Alpaca's response format was checked against the official alpaca-py SDK (the docs site blocks
    scripts). Old store keys `real_prices_usage`/`_attempts`/`_pause` from 5.8 are deleted on load.
  - `market/options.py` (7): `expirations(symbol, now)` (dailies for SPY/QQQ/IWM over 10
    trading days, Friday weeklies 6 weeks, third-Friday monthlies 12 months, the next 2 Januaries after
    them (LEAPS); holiday → the trading day before; today's drops at the close), `strikes` (standard
    steps by price, doubled past 60 days; ±3 sd, at least ±12%, at most ±0.9 in log terms, ≤160),
    `atm_variance` (today's vol = spec vol × `engine.vol_ratio()` [market GARCH variance × regime
    for the ρ² part, own variance × regime for the rest] reverting to normal at κ=4/yr, plus
    `earnings_move²` per report between tomorrow and expiry: the IV crush is that term vanishing),
    `smile` (SSVI: ρ −0.7 index funds, −0.35 stocks, +0.1 GLD, −0.15 TLT; η 1.1/0.9; γ 0.5),
    `black_scholes` (vectorized; normal CDF by Zelen-Severo, error <1e-7; theta per day, vega/rho per
    point; RATE 4%, no dividends), `quote_prices` (ticks: pennies for SPY/QQQ/IWM and under $3,
    else nickels; width 1%/2% of price × (1..3 by |Δ|) × (1+0.3 yrs) × the stock's spread
    factor), `activity` (made-up OI anchored to the previous close, steady all day; volume grows
    with the session), `chain(engine, symbol, expiry)` (columns: strikes, call/put lists).
    Engine helpers: `vol_ratio`, `earnings_days(spec, start, end)`. `MarketService.option_chain`
    caches per (market time, timeline); chains ride only on ticks of the current market (not late
    or mid-jump ones; the browser keeps the last); `GET /api/options/expirations?symbol=`,
    `GET /api/options/chain?symbol=&expiration=`; a stream subscription may add
    `"chain": {symbol, expiration}` and its ticks then carry `chain` (null if no such expiration).
  - **Option calibration (7.5):** `market/calibration.py` (pure): `slices` (quotes checked:
    two-sided, ≤50% wide, ≥$0.05, not older than a day vs the newest), `implied_forward`
    (put-call parity), `implied_vols` (vectorized bisection on Black), `fit_slice` (SSVI rho,
    phi by grid search with the ATM variance fixed), `surface` (rho nodes at `TENOR_NODES`,
    eta/gamma by regression), `premium`, `earnings_move`, `expiration_pattern`,
    `volume_shape`, `open_interest_shape`, `calibrate` → a JSON summary per symbol.
    `optiondata.py`: fetching (`fetch_chain`, `fetch_open_interest`, `fetch_bars` SIP daily
    adjusted, `fetch_earnings` Alpha Vantage CSV), `RateLimit` from `X-Ratelimit-*` headers,
    `summarize` (one symbol, worker thread), `OptionData` (store keys `option_calibration`
    and `option_sync`; due after 20h, retry 30 min after a failure). `MarketService.
    refresh_option_data` runs after the first price fetch and then on the schedule, saving
    each symbol as it's done (an interrupted sync resumes); a FetchError stops the sync, other
    errors save a summary without shape so the symbol isn't retried at once.
    `options.Calibration` (from `options.calibrations(saved)`) carries rho/eta/gamma,
    premium, pattern, volume, open interest; `chain(engine, symbol, expiry, cal)`; chains
    include `calibration` (as_of, skew, premium, earnings, fit_error) and bid/ask sizes. The
    chain cache key includes `_calibration_version`, which is also `clock.calibration` (the
    frontend refetches expirations on it). `/api/real-prices` includes `options` status.
    `realprices.request_json` is shared by all requests. Tests: `conftest.py` blocks
    `optiondata.request_json`/`fetch_earnings` and the Alpha Vantage key.
  - `watchlists.py` (6): `Watchlists`, named lists in the store's `watchlists` key (`next_id`,
    `lists` of {id, name, symbols in order}); ids never reused; names tidied, unique ignoring
    case, ≤40 chars; symbols must be known, duplicates dropped; the last list can't be deleted.
    Rewind/reset leave them alone. API (`api/watchlists.py`): `GET /api/symbols`,
    `GET /api/quote-stats` (`MarketService.quote_stats`: `History.quote_stats` cached per
    (market day, timeline); 52-week high/low from `bars30` before today, average volume over the
    last 30 stored sessions; about 130ms on 5 years), `GET /api/sparklines?symbols=` (the engine's
    `day_closes(5)`: closes every 5 minutes so far plus the latest), `GET|POST /api/watchlists`,
    `PATCH|DELETE /api/watchlists/{id}` (PATCH sets name and/or the whole symbol list). Changes
    are broadcast as a `watchlists` stream message (also sent on connect). Saved lists drop
    symbols the market no longer has on load. `bars30_day` index serves the stats (40ms).
  - **Stream subscriptions (6):** a client sends `{"type": "subscribe", "symbols": [...]}`; its
    ticks then carry only those quotes (filtered per connection in `api/stream.py`), and it gets a
    tick straight away (even while paused; not during a jump). `MarketService.send` makes room in
    a full queue by dropping the oldest tick/progress message, so `orders`/`watchlists` arrive.
    The frontend drops quotes from before a time jump (`mergeQuotes(..., fresh)`). The frontend subscribes to the active list, the
    selected symbol and held symbols once lists arrive; until then it gets all.
  - **Quote sizes (6):** `Quote.bid_size`/`ask_size`, round lots from `_quote_sizes`: geometric
    with mean 1 + daily volume / 4M / spread factor, hashed (`_unit_hash`, splitmix64) from the
    minute's seed, the second and each symbol's key, so they're steady within a second and adding
    a symbol leaves others' unchanged. Cosmetic: fills ignore them.
  - `trading/broker.py`: `Broker`, the cash account. `_build` validates (buying power, shares,
    prices; when replacing, that order's reserved cash or shares count as free). `preview`
    (no side effects: estimated price and total, buying power, cash, position, average cost,
    realized P&L, warnings), `place`, `replace` (cancel/replace: the old order is canceled with
    "Replaced by #N"; the new one has `replaces`), and `cancel`. `_immediate` is the pure "what
    happens right now" check used by both `_activate` and `preview`. `process_iter(t0, t1)` finds each working order's next
    event with `engine.first_hit` (minutes whose high/low can't reach the stop or limit are
    skipped; seconds are generated only for candidate minutes) and handles events in time order.
    Bid/ask use the spread factor at that moment (`bid_ask(price, engine.spread_factor(t))`). It expires day orders at each close. The ledger (cash, positions, average cost,
    realized P&L, and each filled sell's realized P&L in `realized_by_order`) is rebuilt from
    filled orders and cached; `order_dict(o)` is how the API and stream send an order (its
    stored fields plus `realized`). `summary()` gives net liq, buying
    power, day P&L (vs holdings at the previous close) and positions. `rewind(t)` deletes
    orders created after `t` and rolls back later fills, closes and triggers. `reset()`. Fill
    rules are in the module docstring.
    **Options (8a):** symbols are OCC symbols (`options.Contract.parse`, `MULTIPLIER` 100); the
    ledger is signed (shorts) with `apply_fill` handling multipliers and buy-to-close realized
    P&L. `Order.effect` ("open"/"close") is set by `_check_option`; `reserved_cash` covers working
    option buys and puts sold to open; `collateral()` (short puts) and `locked_shares()` (short
    calls and calls being sold) reduce buying power and free shares. `_next_option_event` prices
    each minute at its favorable stock extreme at both ends (with `MATCH_MARGIN`), then candidate
    minutes second by second (a test checks it against brute force). `_settle_options(day)` runs
    at each close passed in `process_iter` (which now also runs with option positions and no
    working orders): `_expire` (assign/exercise/expire/close out), `_maybe_assign_early`.
    `summary` prices held contracts with `contract_quote` (mark, delta, theta per position) and
    `_start_of_day_value` uses each contract's mark at the previous close. `options.price(engine,
    symbol, expiry, strikes, day, sec, spot, cal)` is the vectorized pricer over (moments ×
    strikes) using `engine.option_inputs(day, col)` (open, prev close, vol ratio by regime index,
    spread by minute, 5-minute closes); `today_ratio` now uses finished 5-minute stretches so it's
    steady within one.
  - `trading/orders.py`: the `Order` dataclass. Every status change is time-stamped
    (`created_at`, `triggered_at`, `filled_at`, `closed_at`).
  - `store.py`: SQLite in WAL mode with `synchronous=NORMAL` (a plain commit cost about 60ms on
    disk). A `meta` table holds JSON for `market` (`engine.to_state()` without
    `starts`, saved about every 5s), `market_starts` (written only when
    `engine.starts_version` changes), `clock` and `starting_cash`; an `orders` table holds
    one JSON row per order. Startup
    (`main.load_market`) resumes from it, and `MarketService` rolls back any order newer than
    the saved market time.
  - `api/stream.py`: the WebSocket handler runs its send and receive halves in an **anyio task
    group**. Bare `asyncio.create_task` halves made the tests flaky (about 1 in 5 runs on `main`
    too), because cancellation from the server or test client didn't reach them.
  - `api/`: `GET /api/health`, `GET /api/quotes`, `GET|POST /api/clock` (409 during a jump),
    `GET /api/clock/goto?time=` (preview: forward / rewind / reset / none, plus the snapped
    target), `POST /api/clock/goto {time, future: replay|new}`, WebSocket `/ws`. Times without a
    UTC offset are New York wall-clock time; years 1900–2999; forward jumps are capped at 25
    years of new sessions (`MAX_JUMP_DAYS`; 20 years take about 20 s). `api/deps.py`: `market` (read-only) and `idle_market` (409 during a jump) for
    anything that syncs or changes state. Trading (`api/trading.py`): `GET /api/account`, `POST /api/account/reset`,
    `GET|POST /api/orders`, `POST /api/orders/preview`, `PATCH /api/orders/{id}` (replace),
    `DELETE /api/orders/{id}`, `GET /api/bars?symbol&interval&end&count`; 422 responses carry a user-readable
    `detail`; 409 during a jump. **Endpoints that touch market state must be `async def`** so
    they run on the event loop's thread; a plain `def` runs in a worker thread and races the
    tick loop.
- **frontend/** (React 19, TypeScript, Vite, Vitest)
  - `layout/`: the workspace (dockview-react **8.5.1, pinned with `overrides` for dockview and
    dockview-core**). **User's design (2026-10-28): chart first, uncluttered; panels on any
    edge, no floating windows.** The chart is always docked in the middle; Watchlist, Order
    Entry, Options, Positions and Orders (`SIDE_PANELS`, in their order along an edge) each sit
    on an edge: left/right columns (stacked top to bottom) or top/bottom rows (side by side,
    between the columns). Default (`DEFAULT_EDGE`): Watchlist and Order Entry left, Options,
    Positions and Orders bottom, all collapsed. No tab bars (`group.header.hidden` on every group,
    `disableDnd`, `disableFloatingGroups`), so every panel is a group of its own. Each edge
    has a **strip of tabs** for its panels; a tab toggles open/collapsed (it stays as left).
    The **Panels menu** sets each panel's edge (◀ ▶ ▲ ▼) and On/Off (On opens it; a panel
    turned back on returns to its last edge). No date axis on the chart; range and bar size
    along its bottom, Indicators and Latest at the right of the quote line.
    **The quote line (user's request, after 8a): only symbol, name, price and change** (the name
    hides under 520px); everything else it had is a Watchlist column (Open and Prev Close added).
    **RSI/MACD values sit at the top of their own panes** (user's request): `PriceChart` measures each
    pane's top from `pane.getHTMLElement()` after the chart's next frame (it builds panes then), watching
    each pane element with a ResizeObserver; legends find their pane via `IndicatorLayer.ownPanes()`.
    `AppData.tsx`/`appDataContext.ts` hold the stream data and the selected symbol, shared by
    panels. `PanelViews.tsx` has the panel components; each is wrapped in
    `<div class="dock-panel" data-panel="Title">`. `panels.ts`: the registry,
    `buildDefaultLayout`, `panelEdges` (each docked panel's edge, from `edgeOf`), `dockSpot`
    (beside the nearest panel on the same edge in `SIDE_PANELS` order; the first on an edge
    goes at the workspace's left/right edge, or above/below the chart), `placePanel` (an open
    panel moves with `moveTo`, keeping its state; a collapsed one is closed and re-added, as
    dockview can't move hidden groups reliably), `showGroup` (an edge that was all collapsed comes back at its minimum
    thickness, so it gets `EDGE_SIZE`; panels squeezed along an edge share it evenly),
    `edgeTidier`/`collapseEmptyEdges` (an edge whose last shown panel left keeps its space
    in dockview; showing and hiding a collapsed panel there gives it back), `panelPlaces`.
    `Workspace.tsx`: `LayoutProvider` (dockview API for the Panels menu: `places`, `setOn`,
    `setEdge` reading live visibility, `reset`), `Workspace` (restore from `localStorage`
    `paper-trade.layout.v3` (`loadLayout` drops panels that no longer exist), falling back to
    the default when it's bad; panels added to the app since the layout was saved
    (`panelsAddedSinceSave`, from `paper-trade.layout.panels`; layouts from before it had the
    first five) are added collapsed at their default edge, so Options appears for existing
    users; save 300ms after changes and immediately on `pagehide`. Don't
    clear the API ref in effect cleanup: StrictMode's dev double-mount broke saving that way,
    and `App.test` covers it under StrictMode), and `keepSidePanelSizes` (after a structural
    change (a panel added, removed, moved, opened or collapsed) or a workspace resize, each
    open side panel goes back to its edge's thickness: that of a panel already open there
    (panels on an edge share it), else its own last size there (remembered while collapsed),
    else the edge's default; the chart takes the difference. Tracked by panel,
    since moving replaces the group; restored on the next animation frame, because restoring
    inside dockview's layout change got overridden by the rest of it; a saved layout gets its
    sizes back on load via `savedSizes`).
  - **Two dockview traps (#19):** DockviewReact passes any changed *option* prop to
    `updateOptions`, which lays the grid out again from the sizes saved before a divider
    drag. A new function on every render (the shell re-renders on each market tick) snapped
    panels back mid-drag. Keep option props stable and `Workspace` memoized; `App.test`
    checks ticks and the workspace's own re-renders never call `updateOptions`. And
    `api.clear()` keeps the old grid's root, and `fromJSON` could lay out at 0 width:
    `buildDefaultLayout` starts from `fromJSON` of an empty grid, and `onReady` registers
    (before the first load) a relayout at the host's `clientWidth`/`clientHeight` after every
    `fromJSON`.
  - **Edge strips** (`useEdgeStrips.ts`, `EdgeStrip.tsx`, `stripContext.ts`, `edges.ts`): a
    collapsed panel is a hidden grid group (`group.api.setVisible(false)`; dockview saves
    `visible` in the layout JSON, so it survives reloads). `edgeOf` picks the edge from the
    serialized grid (where the group's path and the chart's split). Strips sit in a CSS grid
    around `.dock-host`; Positions and Orders tabs show counts. dockview's
    `onDidLayoutChange` arrives a moment after `setVisible`, so tests `waitFor` it.
  - **Chart space** (the user wants the chart to be the focus; their OS/browser bars already
    take ~100px): top bar 36px (brand, then the clock's date and the connection wording hide
    on narrower windows), one-line `QuoteDetail` (name and `minor` stats hidden by container
    queries on narrow charts), `FullscreenButton` (F). `PriceChart` takes the quote as
    `header` and goes compact (range and bar size as drop-downs) when the buttons don't fit.
  - **Layering:** `.topbar` has z-index 1 over `.workspace` (`isolation: isolate`). Dialogs go
    through `components/Modal.tsx`, a portal to `<body>` at z-index 100. The `className` prop
    doesn't reach an ancestor of the panels, so theme variables are overridden with
    `.workspace .dockview-theme-dark`.
  - `lib/stream.ts`: one WebSocket with auto-reconnect. `useStream()` returns the latest tick
    (clock, account), `quotes` (a `QuoteMap` of every quote seen, merged by `mergeQuotes`), the
    orders list, the watchlists, any fast-forward progress (cleared by the next tick), and
    `subscribe(symbols)` (re-sent after reconnects). Panels read quotes from the map, not
    `tick.quotes` (which only has subscribed symbols). `lib/storage.ts` `useStoredState` for
    localStorage-backed preferences.
  - `components/`: `TopBar` (plus the Panels menu), `ClockControls` (Go to…, pause, speeds from `clock.speeds`,
    regime badge or progress), `GoToDialog` (picker with ±day/week shortcuts → preview → forward
    immediately / rewind confirmation with replay or new future / new-market warning),
    `Watchlist` (6: `ListBar` picker/+/⋯ menu with New/Rename/Delete and Columns; memoized `Row`s
    with tick flashes, held dot, × remove, drag to reorder when unsorted; optimistic `pending`
    order tied to the list object it was based on; pure logic in `lib/watchlist.ts`: `COLUMNS`,
    `sortRows`, `nextSort`, `moveSymbol`), `Sparkline`/`RangeBar` (`lib/sparklines.ts`
    `useSparklines` refetches on a new 5-minute stretch, at most every 2s), `SymbolSearch`
    (combobox with `matchSymbols` from `lib/symbols.ts`), `SymbolLookup` (global: a letter
    other than F or "/" outside fields and dialogs opens it; `lookup` state in AppData),
    `OptionChain` (7: expiration picker, strikes count, ⋯ columns (both sides, saved), calls
    mirrored left, strike, puts; ITM shading; price line, centered when the chain or count
    changes; pure logic in `lib/options.ts`), `OptionsView` (in `PanelViews.tsx`: expirations
    via REST on symbol/day/timeline; `pickExpiration` keeps the picked date or the next;
    `setChainRequest` only while `usePanelShown` (panel and group visible)), `QuoteDetail` (bid/ask with sizes, day range bar, volume, then minor: spread, 52W range, avg
    vol, open, prev close), `OrderTicket` (form → `OrderReview` from the
    server preview → place; `OrderStatusLine` follows the placed order through the stream,
    including replacements), `AccountViews` (Positions with "Reset account…", Orders with filters, Modify
    (`ModifyOrderDialog`) and Cancel, and a P/L column: each order's `realized` from the server,
    the P&L a filled sell realized against the average cost, totalled under the Filled filter;
    the Activity panel was removed at the user's request as it repeated Orders' Filled list),
    `ResetAccountDialog`, `TifSelect`, `Panel`. `lib/orders.ts`: shared status wording
    (`statusText`, `describeOrder`), time-in-force labels, and the replacement lookup.
    Order ids are never reused (`last_order_id` in the store), even after a rewind. The top bar
    shows net liq, buying power and day P&L.
  - `components/PriceChart.tsx`: lightweight-charts v5 (candles + volume histogram on an overlay
    scale, series markers for fills, ET time labels via formatters). Range presets pick a bar
    size and visible range. Older pages load when the visible logical range nears the start.
    Live ticks fold into the last bar (`lib/bars.ts` `bucketStart`/`applyTick`), and the last 2
    bars resync from the server every 1.5s for exact volume. Data loads again when
    `clock.timeline` changes. When older bars are prepended, the visible logical range is
    shifted by the number of bars added. The library keeps bar positions on `setData`, which
    slid the view back in time and chained extra loads. Older pages also wait until a fresh
    load's visible range has been applied (`settled`, about 80ms, because the chart applies
    ranges on its next frame), then load once if the view needs it. Pure
    logic is in `lib/bars.ts`.
  - Indicators: `lib/indicators.ts` (pure math: SMA, EMA, Wilder RSI, MACD, Bollinger with
    population SD, session-anchored VWAP; settings, labels, sanitizing).
    `components/IndicatorLayer.ts` owns the indicator series. Overlays go on pane 0; RSI and MACD
    go in panes 1+ (stretch factor 4:1), and empty panes are removed. The layer recomputes on
    load and updates the last point on each tick. `components/IndicatorMenu.tsx` has toggles and
    lengths, saved in `localStorage` (`paper-trade.indicators`). The legend shows values at the
    hovered bar, from series computed once per bars and settings. `updateLast` recomputes the
    full series each tick: about 3.6ms for all 9 indicators over 5,000 bars, which is fine at
    2 ticks a second. Make it incremental only if that changes. Tests use a recording stand-in for
    the library (`src/test/lightweight-charts.mock.ts`, set up in `src/test/setup.ts`).
  - Vite proxies `/api` and `/ws` to `localhost:8000`.
- Tests: backend 373 (pytest), frontend 167 (Vitest + Testing Library; jsdom gets a ResizeObserver stand-in for dockview). CI runs them on every PR.

## Decisions already made (don't reopen without a reason)

- **Synthetic market data first**; historical replay later (Phase 12). Real options data is
  too expensive, so option chains will be priced with Black-Scholes using a volatility smile
  (Phase 7).
- **Python backend plus React frontend, run locally.** The user considered **GitHub Pages** and
  chose to keep the local server setup. Pages would require moving the engine into the browser.
- **WebAssembly (WASM):** not for the UI. Rendering and DOM updates are the bottleneck, not
  computation. Later, heavy math can use Web Workers and then WASM, only where measurements
  show a need.
- Single user, local only.
- **Real session hours without waiting:** 9:30–4:00 ET, Monday–Friday, with the close jumping to
  the next open (the user chose this over a 24-hour market). The user can go to any date/time.
- **Going back in time:** later trades are erased and refunded, positions are repriced, and the
  user picks replay or new future each time. Before the history, a fresh market (see above).
- Saved across restarts; cash account only (Phase 2b). No commissions.

## Known limitations / backlog

- Fills are all-or-nothing (no partial fills) and ignore volume and liquidity.
- A stop buy reserves the higher of its stop price and the ask at placement. If the market gaps
  past that, the fill can cost more than reserved. A fill may use only cash not set aside for
  other working buys; otherwise the order is rejected.
- The ledger is rebuilt from all fills when it changes. That's fine for thousands of trades;
  revisit if history grows very large.
- No early closes (half days); one-off closures (e.g. national days of mourning) aren't modeled.
- Options: model spreads and sizes; the American put price is an approximation (about 1% low at the money); the chain streams to the browser in
  full each tick for one expiration (fine at ~150 strikes).
- Reset (going before the history) starts at the real (split-adjusted) closes of that date where Tiingo's
  history is saved (8c, 8d), otherwise today's real price brought back by drift, otherwise the spec
  prices. The path from there is the sim's own, not the real one.
- Five-year samples vary around the spec vol (persistent volatility); the average over many runs
  matches it.
- Within a minute, seconds are a plausible bridge through the bar's open/high/low/close, not an
  exact Brownian path; minute highs and lows are drawn independently (their joint distribution is
  approximated).
- The watchlist panel doesn't widen when columns are added; it scrolls sideways (drag its edge).
- Changing the symbol list (adding one) still continues the market as a new one (`continue_older`)
  and rebuilds history, although other symbols' prices would be unchanged. Remapping saved
  starts by name and backfilling only the new symbol would avoid that; do it if symbols are added.
- Model-version changes (`continue_older`): the old model's prices can't be recomputed, so
  that session restarts from its previous close with new draws and the quote jumps once
  (positions revalue; a working stop could trigger). Fills made earlier keep their prices,
  but the regenerated history no longer shows them at those prices, and going earlier than
  the continue point resets the account. The user's account had no orders at the 5.6 upgrade.
- History meets the origin with a seam in regime and GARCH variance: the origin's session
  starts normal at variance 1 whatever the history ended in (as before 5.6).
- `dev.sh` (foreground) still exists alongside `server.sh` (background). Keep it or remove it.
