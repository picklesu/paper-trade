"""Order records.

Every status change is time-stamped in market time. That is all the account
needs: cash and positions are rebuilt from the fills, and going back in time
rolls the time stamps back.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop", "stop_limit"]
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
        )
