"""SQLite persistence: small JSON settings plus one row per order."""

import json
import sqlite3
from pathlib import Path

from paper_trade.trading.orders import Order

# backend/paper_trade.db unless PAPER_TRADE_DB says otherwise.
DEFAULT_DB = Path(__file__).resolve().parents[2] / "paper_trade.db"


class Store:
    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(path, isolation_level=None)  # autocommit
        # Write-ahead logging with normal syncing: each commit no longer waits for a full
        # disk flush (still safe against app crashes; a power cut may lose the last moments).
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")

    @property
    def connection(self) -> sqlite3.Connection:
        return self._db

    def get(self, key: str, default=None):
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM meta WHERE key = ?", (key,))

    def load_orders(self) -> list[Order]:
        rows = self._db.execute("SELECT data FROM orders ORDER BY id").fetchall()
        return [Order.from_dict(json.loads(data)) for (data,) in rows]

    def save_order(self, order: Order) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO orders (id, data) VALUES (?, ?)", (order.id, json.dumps(order.to_dict()))
        )

    def delete_order(self, order_id: int) -> None:
        self._db.execute("DELETE FROM orders WHERE id = ?", (order_id,))

    def delete_all_orders(self) -> None:
        self._db.execute("DELETE FROM orders")

    def close(self) -> None:
        self._db.close()
