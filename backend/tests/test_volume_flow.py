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
