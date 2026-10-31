import pytest

from paper_trade import optiondata, pricehistory, realprices
from paper_trade.market import service


@pytest.fixture(autouse=True)
def no_real_prices(monkeypatch):
    """Tests never call the real price or option services (a local backend/.env may hold keys)."""
    monkeypatch.setattr(realprices, "credentials", lambda *args: None)
    monkeypatch.setattr(optiondata, "av_key", lambda *args: None)
    monkeypatch.setattr(pricehistory, "tiingo_key", lambda *args: None)

    def refuse(*args, **kwargs):
        raise AssertionError("tests must not fetch real prices or option data")

    monkeypatch.setattr(realprices, "fetch_snapshots", refuse)
    monkeypatch.setattr(optiondata, "request_json", refuse)
    monkeypatch.setattr(pricehistory, "fetch_closes", refuse)
    monkeypatch.setattr(optiondata, "fetch_earnings", refuse)
    monkeypatch.setattr(optiondata, "fetch_treasury_rate", refuse)
    for name in ("fetch_bars", "fetch_earnings", "fetch_treasury_rate", "summarize"):  # imported by name there
        monkeypatch.setattr(service, name, refuse)
