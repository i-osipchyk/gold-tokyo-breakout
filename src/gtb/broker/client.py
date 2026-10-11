"""A small asyncio client for the cTrader Open API (ARCHITECTURE §4.2).

One connection per Lambda invocation: connect -> app auth -> account auth -> requests -> close.
Frames are a 4-byte big-endian length followed by a ProtoMessage (the protocol the
``ctrader-open-api`` package speaks through Twisted's Int32StringReceiver); only its generated
message classes are used here, not its Twisted reactor, which can't be restarted in a warm Lambda.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import ssl
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ctrader_open_api.messages import OpenApiCommonMessages_pb2 as C
from ctrader_open_api.messages import OpenApiMessages_pb2 as M
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM

from ..core import Bar

LIVE_HOST = "live.ctraderapi.com"
DEMO_HOST = "demo.ctraderapi.com"
PORT = 5035
PRICE_SCALE = 100_000             # trendbar and tick prices are integers in 1e-5 units
_SIDES = {"buy": MM.BUY, "sell": MM.SELL}
PAGE = 1000                       # trendbars per request (Open API cap)
RATE_LIMITED = "BLOCKED_PAYLOAD_TYPE"   # "You are being rate limited": the request was not run
_PERIOD_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}


class CTraderError(Exception):
    def __init__(self, code: str, description: str = "") -> None:
        super().__init__(f"{code}: {description}" if description else code)
        self.code = code
        self.description = description


@dataclass(frozen=True)
class SymbolSpec:
    symbol_id: int
    digits: int
    lot_size: int                 # cents of a unit per lot (XAUUSD: 100 oz -> 10_000)
    step_volume: int
    min_volume: int

    def volume(self, lots: float) -> int:
        """Lots -> the Open API's volume (cents of a unit)."""
        return round(lots * self.lot_size)


@dataclass(frozen=True)
class Position:
    position_id: int
    symbol_id: int
    side: str                     # "buy" | "sell"
    volume: int                   # cents of a unit
    entry_price: float
    stop_loss: float | None
    label: str
    opened_at: datetime


@dataclass(frozen=True)
class PendingOrder:
    order_id: int
    symbol_id: int
    side: str
    volume: int
    stop_price: float
    stop_loss: float | None
    label: str
    client_order_id: str
    expiry: datetime | None


@dataclass(frozen=True)
class Deal:
    """One fill, as the broker recorded it. Money in the deposit currency; commission keeps the
    broker's sign (negative = charged)."""

    deal_id: int
    order_id: int
    position_id: int
    side: str
    volume: int                   # filled, cents of a unit
    price: float
    time: datetime
    commission: float
    closing: bool
    entry_price: float | None     # closing deals: the position's entry price
    gross_profit: float | None    # closing deals: P&L before commission and swap


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _side(trade_side: int) -> str:
    return "buy" if trade_side == MM.BUY else "sell"


def _message_types() -> dict[int, type]:
    out = {}
    for mod in (C, M):
        for name in dir(mod):
            cls = getattr(mod, name)
            if name.startswith("Proto") and hasattr(cls, "DESCRIPTOR") and \
                    "payloadType" in cls.DESCRIPTOR.fields_by_name:
                out[cls().payloadType] = cls
    return out


_TYPES = _message_types()


class CTraderClient:
    def __init__(self, host: str, port: int = PORT, *, client_id: str, client_secret: str,
                 access_token: str, account_id: int, use_tls: bool = True,
                 timeout: float = 30, heartbeat_every: float = 10,
                 rate_limit_pause: float = 1.0, rate_limit_retries: int = 10) -> None:
        self.host, self.port = host, port
        self.client_id, self.client_secret = client_id, client_secret
        self.access_token, self.account_id = access_token, account_id
        self.use_tls, self.timeout = use_tls, timeout
        self.heartbeat_every = heartbeat_every
        self.rate_limit_pause, self.rate_limit_retries = rate_limit_pause, rate_limit_retries
        self._beating: asyncio.Task[None] | None = None
        self._ids = itertools.count(1)
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reading: asyncio.Task[None] | None = None

    async def __aenter__(self) -> CTraderClient:
        await self.connect()
        try:
            await self._request(M.ProtoOAApplicationAuthReq(
                clientId=self.client_id, clientSecret=self.client_secret))
            await self._request(M.ProtoOAAccountAuthReq(
                ctidTraderAccountId=self.account_id, accessToken=self.access_token))
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        context = ssl.create_default_context() if self.use_tls else None
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, ssl=context), self.timeout)
        self._reading = asyncio.create_task(self._read_loop())
        self._beating = asyncio.create_task(self._heartbeat_loop())

    async def close(self) -> None:
        for task in (self._reading, self._beating):
            if task is not None:
                task.cancel()
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(ConnectionError, ssl.SSLError):
                await self._writer.wait_closed()

    # --- account -----------------------------------------------------------------------

    async def is_live(self) -> bool:
        """Whether this client's account is a live (real-money) one."""
        res = await self._request(
            M.ProtoOAGetAccountListByAccessTokenReq(accessToken=self.access_token))
        for account in res.ctidTraderAccount:
            if account.ctidTraderAccountId == self.account_id:
                return bool(account.isLive)
        raise CTraderError("ACCOUNT_NOT_FOUND", f"{self.account_id} is not on this access token")

    async def balance(self) -> float:
        """Account balance in the deposit currency (equity, when flat)."""
        res = await self._request(M.ProtoOATraderReq(ctidTraderAccountId=self.account_id))
        return float(res.trader.balance / 10 ** res.trader.moneyDigits)

    async def symbol(self, name: str) -> SymbolSpec:
        listing = await self._request(M.ProtoOASymbolsListReq(ctidTraderAccountId=self.account_id))
        ids = [s.symbolId for s in listing.symbol if s.symbolName == name]
        if not ids:
            raise CTraderError("SYMBOL_NOT_FOUND", name)
        res = await self._request(
            M.ProtoOASymbolByIdReq(ctidTraderAccountId=self.account_id, symbolId=ids))
        s = res.symbol[0]
        return SymbolSpec(s.symbolId, s.digits, s.lotSize, s.stepVolume, s.minVolume)

    # --- trading -----------------------------------------------------------------------

    async def reconcile(self) -> tuple[list[Position], list[PendingOrder]]:
        """Open positions and pending orders on the account (all of them, any label)."""
        res = await self._request(M.ProtoOAReconcileReq(ctidTraderAccountId=self.account_id))
        positions = [Position(
            position_id=p.positionId, symbol_id=p.tradeData.symbolId,
            side=_side(p.tradeData.tradeSide), volume=p.tradeData.volume, entry_price=p.price,
            stop_loss=p.stopLoss if p.HasField("stopLoss") else None, label=p.tradeData.label,
            opened_at=_from_ms(p.tradeData.openTimestamp)) for p in res.position]
        orders = [PendingOrder(
            order_id=o.orderId, symbol_id=o.tradeData.symbolId, side=_side(o.tradeData.tradeSide),
            volume=o.tradeData.volume, stop_price=o.stopPrice,
            stop_loss=o.stopLoss if o.HasField("stopLoss") else None, label=o.tradeData.label,
            client_order_id=o.clientOrderId,
            expiry=_from_ms(o.expirationTimestamp) if o.HasField("expirationTimestamp") else None,
        ) for o in res.order]
        return positions, orders

    async def place_stop_order(self, symbol: SymbolSpec, side: str, *, lots: float,
                               stop_price: float, stop_loss: float, expiry: datetime,
                               label: str, client_order_id: str) -> int:
        """A resting stop entry with an absolute stop-loss, good till ``expiry``. Returns the
        broker's order id."""
        res = await self._request(M.ProtoOANewOrderReq(
            ctidTraderAccountId=self.account_id, symbolId=symbol.symbol_id,
            orderType=MM.STOP, tradeSide=_SIDES[side], volume=symbol.volume(lots),
            stopPrice=stop_price, stopLoss=stop_loss, timeInForce=MM.GOOD_TILL_DATE,
            expirationTimestamp=_ms(expiry), label=label, clientOrderId=client_order_id))
        return int(res.order.orderId)

    async def cancel_order(self, order_id: int) -> None:
        await self._request(
            M.ProtoOACancelOrderReq(ctidTraderAccountId=self.account_id, orderId=order_id))

    async def close_position(self, position_id: int, *, volume: int) -> None:
        """Market-close ``volume`` (cents of a unit; the whole position in practice)."""
        await self._request(M.ProtoOAClosePositionReq(
            ctidTraderAccountId=self.account_id, positionId=position_id, volume=volume))

    async def deals(self, start: datetime, end: datetime) -> list[Deal]:
        """Fills executed in [start, end], oldest first."""
        res = await self._request(M.ProtoOADealListReq(
            ctidTraderAccountId=self.account_id, fromTimestamp=_ms(start), toTimestamp=_ms(end),
            maxRows=1000))
        if res.hasMore:
            raise CTraderError("TOO_MANY_DEALS", f"more than 1000 deals in {start} -> {end}")
        out = []
        for d in res.deal:
            scale = 10 ** d.moneyDigits
            close = d.closePositionDetail if d.HasField("closePositionDetail") else None
            out.append(Deal(
                deal_id=d.dealId, order_id=d.orderId, position_id=d.positionId,
                side=_side(d.tradeSide), volume=d.filledVolume, price=d.executionPrice,
                time=_from_ms(d.executionTimestamp), commission=d.commission / scale,
                closing=close is not None,
                entry_price=close.entryPrice if close is not None else None,
                gross_profit=close.grossProfit / 10 ** close.moneyDigits
                if close is not None else None))
        return sorted(out, key=lambda d: (d.time, d.deal_id))

    # --- market data -------------------------------------------------------------------

    async def trendbars(self, symbol_id: int, period: str, start: datetime,
                        end: datetime) -> list[Bar]:
        """Bid trendbars opened in [start, end], ascending. The API returns up to ``count`` bars
        back from ``toTimestamp`` -- including bars before ``fromTimestamp`` (seen live) -- so
        bars outside the window are dropped and paging stops once a page reaches ``start``. An
        empty page also ends it; a short one doesn't (calendar gaps shorten pages too)."""
        from_ms, to_ms = _ms(start), _ms(end)
        step_ms = _PERIOD_SECONDS[period] * 1000
        bars: dict[int, Bar] = {}
        while True:
            res = await self._request(M.ProtoOAGetTrendbarsReq(
                ctidTraderAccountId=self.account_id, symbolId=symbol_id,
                period=MM.ProtoOATrendbarPeriod.Value(period), fromTimestamp=from_ms,
                toTimestamp=to_ms, count=PAGE))
            if not res.trendbar:
                break
            for tb in res.trendbar:
                if not from_ms <= tb.utcTimestampInMinutes * 60_000 <= _ms(end):
                    continue
                bars[tb.utcTimestampInMinutes] = Bar(
                    datetime.fromtimestamp(tb.utcTimestampInMinutes * 60, tz=UTC),
                    (tb.low + tb.deltaOpen) / PRICE_SCALE, (tb.low + tb.deltaHigh) / PRICE_SCALE,
                    tb.low / PRICE_SCALE, (tb.low + tb.deltaClose) / PRICE_SCALE)
            to_ms = min(tb.utcTimestampInMinutes for tb in res.trendbar) * 60_000 - step_ms
            if to_ms < from_ms:
                break
        return [bars[k] for k in sorted(bars)]

    async def ticks(self, symbol_id: int, quote: str, start: datetime,
                    end: datetime) -> list[tuple[datetime, float]]:
        """Bid or ask ticks in [start, end], oldest first. On the wire the first tick is absolute
        and each later one a delta from the previous, newest first."""
        res = await self._request(M.ProtoOAGetTickDataReq(
            ctidTraderAccountId=self.account_id, symbolId=symbol_id,
            type=MM.BID if quote == "bid" else MM.ASK, fromTimestamp=_ms(start),
            toTimestamp=_ms(end)))
        if res.hasMore:
            raise CTraderError("TOO_MANY_TICKS", f"narrow the window {start} -> {end}")
        out, ts, price = [], 0, 0
        for td in res.tickData:
            ts, price = ts + td.timestamp, price + td.tick
            out.append((_from_ms(ts), round(price / PRICE_SCALE, 5)))
        return sorted(out)

    # --- transport ---------------------------------------------------------------------

    def _send(self, msg: Any, client_msg_id: str | None = None) -> None:
        assert self._writer is not None
        envelope = C.ProtoMessage(payloadType=msg.payloadType, payload=msg.SerializeToString())
        if client_msg_id:
            envelope.clientMsgId = client_msg_id
        data = envelope.SerializeToString()
        self._writer.write(struct.pack(">I", len(data)) + data)

    async def _request(self, msg: Any) -> Any:
        """Send ``msg`` and wait for its reply. A rate-limited request was refused unexecuted,
        so it is re-sent after a pause."""
        for _ in range(self.rate_limit_retries):
            try:
                return await self._request_once(msg)
            except CTraderError as e:
                if e.code != RATE_LIMITED:
                    raise
            await asyncio.sleep(self.rate_limit_pause)
        return await self._request_once(msg)

    async def _request_once(self, msg: Any) -> Any:
        msg_id = str(next(self._ids))
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        self._send(msg, msg_id)
        try:
            reply = await asyncio.wait_for(future, self.timeout)
        finally:
            self._pending.pop(msg_id, None)
        if isinstance(reply, (M.ProtoOAErrorRes, M.ProtoOAOrderErrorEvent, C.ProtoErrorRes)):
            raise CTraderError(reply.errorCode, reply.description)
        if isinstance(reply, M.ProtoOAExecutionEvent) and reply.executionType in (
                MM.ORDER_REJECTED, MM.ORDER_CANCEL_REJECTED):
            raise CTraderError(reply.errorCode or MM.ProtoOAExecutionType.Name(reply.executionType))
        return reply

    async def _heartbeat_loop(self) -> None:
        """The server drops a connection that is silent for ~30 s (the morning run idles while it
        waits for the 08:55 bar)."""
        while True:
            await asyncio.sleep(self.heartbeat_every)
            self._send(C.ProtoHeartbeatEvent())

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                (size,) = struct.unpack(">I", await self._reader.readexactly(4))
                envelope = C.ProtoMessage()
                envelope.ParseFromString(await self._reader.readexactly(size))
                cls = _TYPES.get(envelope.payloadType)
                if cls is None:
                    continue
                msg = cls()
                msg.ParseFromString(envelope.payload)
                if isinstance(msg, C.ProtoHeartbeatEvent):
                    self._send(C.ProtoHeartbeatEvent())
                    continue
                future = self._pending.get(envelope.clientMsgId)
                if future is not None and not future.done():
                    future.set_result(msg)
        except (asyncio.IncompleteReadError, ConnectionError) as e:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError(f"cTrader connection lost: {e}"))
