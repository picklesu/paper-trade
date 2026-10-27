from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from paper_trade import __version__

router = APIRouter()


class Health(BaseModel):
    status: str
    version: str
    server_time: datetime


@router.get("/health")
def get_health() -> Health:
    return Health(status="ok", version=__version__, server_time=datetime.now(UTC))
