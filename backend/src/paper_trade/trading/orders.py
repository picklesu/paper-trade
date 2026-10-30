"""Order records.

Every status change is time-stamped in market time. That is all the account
needs: cash and positions are rebuilt from the fills, and going back in time
rolls the time stamps back.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop", "stop_limit", "exercise", "assignment", "expiration"]
# Exercise, assignment and expiration aren't orders anyone placed: they're
# recorded as filled orders so the account, history and time travel treat them
# like trades. An exercise or assignment is two records at the same moment: the
# option closed at its intrinsic value and the shares traded at the stock's price
# (the cash adds up to trading the shares at the strike).
EVENT_TYPES = frozenset({"exercise", "assignment", "expiration"})
Effect = Literal["open", "close"]
TimeInForce = Literal["day", "gtc"]
Status = Literal["working", "filled", "canceled", "expired", "rejected"]


@dataclass
class Order:
    id: int
    symbol: str
    side: Side
    qty: int
    type: OrderType
    tif: TimeInForce
    limit_price: float | None
    stop_price: float | None
    created_at: datetime
    status: Status = "working"
    triggered_at: datetime | None = None  # stop-limit: when the stop price was hit
    filled_at: datetime | None = None
    fill_price: float | None = None
    closed_at: datetime | None = None  # when canceled, expired or rejected
    reason: str | None = None
    replaces: int | None = None  # the order this one replaced (cancel/replace)
    effect: Effect | None = None  # options: opens or closes a position

    @property
    def has_limit(self) -> bool:
        return self.type in ("limit", "stop_limit")

    @property
    def waiting_for_stop(self) -> bool:
        return self.type in ("stop", "stop_limit") and self.triggered_at is None

    def to_dict(self) -> dict:
        def iso(t: datetime | None) -> str | None:
            return t.isoformat() if t else None

        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "type": self.type,
            "tif": self.tif,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "created_at": iso(self.created_at),
            "status": self.status,
            "triggered_at": iso(self.triggered_at),
            "filled_at": iso(self.filled_at),
            "fill_price": self.fill_price,
            "closed_at": iso(self.closed_at),
            "reason": self.reason,
            "replaces": self.replaces,
            "effect": self.effect,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        def dt(v: str | None) -> datetime | None:
            return datetime.fromisoformat(v) if v else None

        return cls(
            id=d["id"],
            symbol=d["symbol"],
            side=d["side"],
            qty=d["qty"],
            type=d["type"],
            tif=d["tif"],
            limit_price=d["limit_price"],
            stop_price=d["stop_price"],
            created_at=datetime.fromisoformat(d["created_at"]),
            status=d["status"],
            triggered_at=dt(d["triggered_at"]),
            filled_at=dt(d["filled_at"]),
            fill_price=d["fill_price"],
            closed_at=dt(d["closed_at"]),
            reason=d["reason"],
            replaces=d.get("replaces"),
            effect=d.get("effect"),
        )
