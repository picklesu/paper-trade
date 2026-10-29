import pytest

from paper_trade import realprices


@pytest.fixture(autouse=True)
def no_real_prices(monkeypatch):
    """Tests never call the real price service (a local backend/.env may hold keys)."""
    monkeypatch.setattr(realprices, "credentials", lambda *args: None)

    def refuse(*args):
        raise AssertionError("tests must not fetch real prices")

    monkeypatch.setattr(realprices, "fetch_snapshots", refuse)
