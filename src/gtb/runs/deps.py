"""What a run needs from the outside world, injected so handlers are testable offline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Protocol

from ..broker import Deal, PendingOrder, Position, SymbolSpec
from ..core import Bar
from ..records import RecordStore

LABEL = "gtb"
SYMBOL = "XAUUSD"


class Broker(Protocol):
    """The part of CTraderClient the runs use."""

    async def balance(self) -> float: ...

    async def symbol(self, name: str) -> SymbolSpec: ...

    async def trendbars(self, symbol_id: int, period: str, start: datetime,
                        end: datetime) -> list[Bar]: ...

    async def reconcile(self) -> tuple[list[Position], list[PendingOrder]]: ...

    async def place_stop_order(self, symbol: SymbolSpec, side: str, *, lots: float,
                               stop_price: float, stop_loss: float, expiry: datetime,
                               label: str, client_order_id: str) -> int: ...

    async def cancel_order(self, order_id: int) -> None: ...

    async def close_position(self, position_id: int, *, volume: int) -> None: ...

    async def deals(self, start: datetime, end: datetime) -> list[Deal]: ...

    async def ticks(self, symbol_id: int, quote: str, start: datetime,
                    end: datetime) -> list[tuple[datetime, float]]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep_until(self, t: datetime) -> None: ...


class Notifier(Protocol):
    async def send(self, text: str) -> None: ...


@dataclass
class Deps:
    env: str                              # "demo" | "live"
    risk: float
    broker: Broker
    store: RecordStore
    notifier: Notifier
    clock: Clock
    early_closes: Mapping[date, time]
    code_version: str


def client_order_id(day: date, side: str) -> str:
    return f"gtb-{day:%Y%m%d}-{'L' if side == 'buy' else 'S'}"
