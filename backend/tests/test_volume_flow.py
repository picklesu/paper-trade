from datetime import date

from paper_trade.market.calendar import session_open


def test_funds_have_no_company_news_and_news_trades_in_proportion() -> None:
    from paper_trade.market import engine as engine_mod
    from paper_trade.market.engine import MarketEngine

    e = MarketEngine(session_open(date(2027, 1, 4)), seed=1)
    for i, spec in enumerate(e.specs):
        assert (e._news_sd[i] == 0) == (spec.earnings_move == 0)  # funds (SPY, QQQ) never jump on company news
    assert e._news_sd[e.symbol_index("SPY")] == 0 and e._news_sd[e.symbol_index("AAPL")] > 0
    assert engine_mod.JUMP_VOLUME > 0 and engine_mod.NEWS_VOLUME_BURST * engine_mod.NEWS_VOLUME_DECAY > 100  # a big jump trades several times a normal day


def test_generated_days_keep_volume_finite_and_funds_free_of_news_jumps() -> None:
    import numpy as np

    from paper_trade.market.calendar import next_trading_day
    from paper_trade.market.engine import MarketEngine

    e = MarketEngine(session_open(date(2027, 1, 4)), seed=9)
    spy = e.symbol_index("SPY")
    e.advance(session_open(date(2027, 12, 31)))
    day, worst, days = date(2027, 1, 4), 0.0, 0
    while day in e._starts:
        sk = e._skeleton(day)
        assert np.isfinite(sk.volume).all() and (sk.volume >= 0).all()  # also for negative-beta symbols (TLT) on selloffs
        worst = max(worst, float(np.abs(np.diff(sk.close[:, spy])).max()))
        day, days = next_trading_day(day), days + 1
    assert days > 200
    assert worst < 0.04  # no single-minute jump like the company news SPY once got (9.7%)


def test_persistence_file_gives_each_symbol_its_own_volume_state() -> None:
    import json

    from paper_trade.market import engine as engine_mod
    from paper_trade.market.symbols import SYMBOLS

    fits = json.loads(engine_mod.PERSISTENCE_FILE.read_text())["symbols"]
    assert set(fits) == {s.symbol for s in SYMBOLS}  # a refit covers every symbol
    p = engine_mod._Persistence.load(SYMBOLS)
    tsla, ko = [s.symbol for s in SYMBOLS].index("TSLA"), [s.symbol for s in SYMBOLS].index("KO")
    assert p.slow_share[tsla] > 0.4 > p.slow_share[ko]  # TSLA's volume stays high or low for months, KO's does not
    assert (p.fast_share + p.slow_share <= 1 + 1e-9).all() and (p.flow_size > 0).all()
    d = engine_mod._Persistence.default(len(SYMBOLS))  # without a fit the old global values apply
    assert d.own_sd[0] == engine_mod.VOLUME_OWN_SD and d.flow_size[0] == engine_mod.VOLUME_FLOW_SIZE


def test_fitted_persistence_runs_through_the_engine_without_nan_and_rounded_shares_are_scaled(tmp_path) -> None:
    import json

    import numpy as np

    from paper_trade.market import engine as engine_mod
    from paper_trade.market.engine import MarketEngine
    from paper_trade.market.symbols import SYMBOLS

    # shares that add to a hair over 1 (rounded in the file) are scaled, so the white rest stays real
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"symbols": {"JNJ": {"fast_share": 0.5, "slow_share": 0.50001, "slow_rho": 0.96, "market_sd": 0.05, "own_sd": 0.2, "flow_size": 0.1}}}))
    p = engine_mod._Persistence.load(SYMBOLS, path)
    j = [s.symbol for s in SYMBOLS].index("JNJ")
    assert p.fast_share[j] + p.slow_share[j] <= 1.0 and p.flow_size[j] == 0.1 and p.flow_size[0] == engine_mod.VOLUME_FLOW_SIZE

    e = MarketEngine(session_open(date(2027, 1, 4)), seed=3)  # the shipped fit, a month of days
    e.advance(session_open(date(2027, 2, 12)))
    day = date(2027, 1, 5)
    for _ in range(25):
        assert np.isfinite(e._skeleton(day).volume).all() and np.isfinite(e._starts[day].volume_state).all()
        day = __import__("paper_trade.market.calendar", fromlist=["x"]).next_trading_day(day)
