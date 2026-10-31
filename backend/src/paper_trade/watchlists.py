"""Named watchlists, saved in the store's `watchlists` key.

A list is an id, a name and its symbols in the user's order. Going back in time
or resetting the account leaves watchlists alone: they're preferences, not
market state. There is always at least one list.
"""

from paper_trade.store import Store

MAX_NAME = 40
ALL_SYMBOLS = "All symbols"
INDEXES = frozenset({"VIX"})
DEFAULT_LISTS = (
    ("Main", ("SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "AMD")),
    (ALL_SYMBOLS, None),  # None: every symbol
)


class Watchlists:
    def __init__(self, store: Store, symbols: list[str]) -> None:
        self.store = store
        self.symbols = symbols
        saved = store.get("watchlists")
        if saved is None:
            lists = [
                {"id": i + 1, "name": name, "symbols": [s for s in (members or symbols) if s in symbols]}
                for i, (name, members) in enumerate(DEFAULT_LISTS)
            ]
            saved = {"next_id": len(lists) + 1, "lists": lists, "seen": list(symbols)}
            store.set("watchlists", saved)
        else:
            # Symbols the market no longer has (the symbol list changed) would make every edit fail.
            for wl in saved["lists"]:
                wl["symbols"] = [s for s in wl["symbols"] if s in symbols]
            # Symbols added to the market since the lists were made join "All symbols" once
            # (earlier saves had every symbol but the indexes).
            seen = set(saved.get("seen") or (s for s in symbols if s not in INDEXES))
            fresh = [s for s in symbols if s not in seen]
            everything = next((wl for wl in saved["lists"] if wl["name"] == ALL_SYMBOLS), None)
            if everything and fresh:
                everything["symbols"] += [s for s in fresh if s not in everything["symbols"]]
            saved["seen"] = list(symbols)
            store.set("watchlists", saved)
        self._data = saved

    @property
    def lists(self) -> list[dict]:
        return self._data["lists"]

    def message(self) -> dict:
        return {"type": "watchlists", "watchlists": self.lists}

    def create(self, name: str, symbols: list[str] | None = None) -> dict:
        wl = {"id": self._data["next_id"], "name": self._name(name), "symbols": self._symbols(symbols or [])}
        self._data["next_id"] += 1
        self.lists.append(wl)
        self._save()
        return wl

    def update(self, list_id: int, name: str | None = None, symbols: list[str] | None = None) -> dict:
        """Rename and/or set the symbols (the whole list, in order: adding,
        removing and reordering are all this)."""
        wl = self.get(list_id)
        new_name = self._name(name, list_id) if name is not None else wl["name"]
        new_symbols = self._symbols(symbols) if symbols is not None else wl["symbols"]
        wl["name"], wl["symbols"] = new_name, new_symbols
        self._save()
        return wl

    def delete(self, list_id: int) -> None:
        wl = self.get(list_id)
        if len(self.lists) == 1:
            raise ValueError("the last watchlist can't be deleted")
        self.lists.remove(wl)
        self._save()

    def get(self, list_id: int) -> dict:
        for wl in self.lists:
            if wl["id"] == list_id:
                return wl
        raise KeyError(list_id)

    def _name(self, name: str, list_id: int | None = None) -> str:
        name = " ".join(name.split())
        if not name:
            raise ValueError("a watchlist needs a name")
        if len(name) > MAX_NAME:
            raise ValueError(f"names are at most {MAX_NAME} characters")
        if any(wl["name"].lower() == name.lower() and wl["id"] != list_id for wl in self.lists):
            raise ValueError(f'there is already a watchlist named "{name}"')
        return name

    def _symbols(self, symbols: list[str]) -> list[str]:
        out = []
        for raw in symbols:
            s = raw.strip().upper()
            if s not in self.symbols:
                raise ValueError(f"unknown symbol: {raw}")
            if s not in out:
                out.append(s)
        return out

    def _save(self) -> None:
        self.store.set("watchlists", self._data)
