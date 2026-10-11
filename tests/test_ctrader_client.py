"""CTraderClient against an in-process fake Open API server (tests/fake_ctrader.py)."""

import asyncio

import pytest
from ctrader_open_api.messages import OpenApiMessages_pb2 as M

from gtb.broker import CTraderClient, CTraderError

from fake_ctrader import FakeCTrader

ACCOUNT = 4242


def _client(port: int) -> CTraderClient:
    return CTraderClient("127.0.0.1", port, client_id="app-id", client_secret="app-secret",
                         access_token="token-1", account_id=ACCOUNT, use_tls=False, timeout=5)


async def _with_client(fake: FakeCTrader, body):
    port = await fake.start()
    try:
        async with _client(port) as client:
            return await body(client)
    finally:
        await fake.stop()
        if fake.errors:
            raise fake.errors[0]


def run(fake, body):
    return asyncio.run(_with_client(fake, body))


def test_connecting_authenticates_the_application_then_the_account():
    fake = FakeCTrader()

    run(fake, lambda client: asyncio.sleep(0))

    app, account = fake.received[:2]
    assert isinstance(app, M.ProtoOAApplicationAuthReq)
    assert (app.clientId, app.clientSecret) == ("app-id", "app-secret")
    assert isinstance(account, M.ProtoOAAccountAuthReq)
    assert (account.ctidTraderAccountId, account.accessToken) == (ACCOUNT, "token-1")


def test_an_error_reply_raises_with_its_code():
    fake = FakeCTrader()
    fake.on(M.ProtoOAAccountAuthReq, lambda r: [M.ProtoOAErrorRes(
        errorCode="CH_ACCESS_TOKEN_INVALID", description="Invalid access token")])

    with pytest.raises(CTraderError) as e:
        run(fake, lambda client: asyncio.sleep(0))

    assert e.value.code == "CH_ACCESS_TOKEN_INVALID"


def test_reports_whether_the_account_is_live():
    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM
    fake = FakeCTrader()
    fake.on(M.ProtoOAGetAccountListByAccessTokenReq, lambda r: [
        M.ProtoOAGetAccountListByAccessTokenRes(accessToken=r.accessToken, ctidTraderAccount=[
            MM.ProtoOACtidTraderAccount(ctidTraderAccountId=1111, isLive=True),
            MM.ProtoOACtidTraderAccount(ctidTraderAccountId=ACCOUNT, isLive=False),
        ])])

    assert run(fake, lambda client: client.is_live()) is False


def _gold_symbol(fake):
    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM
    fake.on(M.ProtoOASymbolsListReq, lambda r: [M.ProtoOASymbolsListRes(
        ctidTraderAccountId=ACCOUNT, symbol=[
            MM.ProtoOALightSymbol(symbolId=1, symbolName="EURUSD"),
            MM.ProtoOALightSymbol(symbolId=41, symbolName="XAUUSD")])])
    fake.on(M.ProtoOASymbolByIdReq, lambda r: [M.ProtoOASymbolByIdRes(
        ctidTraderAccountId=ACCOUNT, symbol=[MM.ProtoOASymbol(
            symbolId=41, digits=2, pipPosition=2, lotSize=10_000, stepVolume=100,
            minVolume=100)])])


def test_looks_up_a_symbol_by_name_and_converts_lots_to_volume():
    fake = FakeCTrader()
    _gold_symbol(fake)

    gold = run(fake, lambda client: client.symbol("XAUUSD"))

    assert (gold.symbol_id, gold.digits) == (41, 2)
    assert fake.of_type(M.ProtoOASymbolByIdReq)[0].symbolId == [41]
    assert gold.volume(0.27) == 2700          # 0.27 lot x 100 oz x 100 (cents of a unit)
    assert gold.volume(0.01) == 100


def test_trendbars_are_decoded_and_paged_backwards_until_the_start():
    from datetime import UTC, datetime, timedelta

    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM

    from gtb.core import Bar

    t0 = datetime(2018, 1, 24, 9, 0, tzinfo=UTC)
    minute = int(t0.timestamp()) // 60

    def tb(i, low):            # open = low + 0.01, high = low + 0.50, close = low + 0.30
        return MM.ProtoOATrendbar(utcTimestampInMinutes=minute + 5 * i, low=low, deltaOpen=1000,
                                  deltaHigh=50_000, deltaClose=30_000, volume=7)

    def page(r):               # the API fills each page backwards from toTimestamp
        newest = r.toTimestamp // 60_000
        bars = [b for b in (tb(i, 134_800_000 + i * 100_000) for i in range(4))
                if b.utcTimestampInMinutes <= newest][-2:]
        return [M.ProtoOAGetTrendbarsRes(ctidTraderAccountId=ACCOUNT, period=MM.M5, timestamp=0,
                                         trendbar=bars)]

    fake = FakeCTrader()
    fake.on(M.ProtoOAGetTrendbarsReq, page)

    bars = run(fake, lambda client: client.trendbars(
        41, "M5", t0, t0 + timedelta(minutes=15)))

    assert bars == [
        Bar(t0 + timedelta(minutes=5 * i),
            *(round(price + i, 2) for price in (1348.01, 1348.50, 1348.00, 1348.30)))
        for i in range(4)
    ]
    requests = fake.of_type(M.ProtoOAGetTrendbarsReq)
    assert {r.period for r in requests} == {MM.M5} and {r.symbolId for r in requests} == {41}
    assert len(requests) == 2                 # page 2 reaches the start: no third request


def test_balance_is_scaled_by_money_digits():
    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM
    fake = FakeCTrader()
    fake.on(M.ProtoOATraderReq, lambda r: [M.ProtoOATraderRes(
        ctidTraderAccountId=ACCOUNT, trader=MM.ProtoOATrader(
            ctidTraderAccountId=ACCOUNT, balance=10_012_345, moneyDigits=2, depositAssetId=1))])

    assert run(fake, lambda client: client.balance()) == 100_123.45


def _gold():
    from gtb.broker import SymbolSpec
    return SymbolSpec(symbol_id=41, digits=2, lot_size=10_000, step_volume=100, min_volume=100)


def test_places_a_stop_order_with_absolute_stop_loss_expiry_and_our_ids():
    from datetime import UTC, datetime

    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM
    fake = FakeCTrader()
    fake.on(M.ProtoOANewOrderReq, lambda r: [M.ProtoOAExecutionEvent(
        ctidTraderAccountId=ACCOUNT, executionType=MM.ORDER_ACCEPTED,
        order=MM.ProtoOAOrder(orderId=9001, orderType=MM.STOP, orderStatus=MM.ORDER_STATUS_ACCEPTED,
                              tradeData=MM.ProtoOATradeData(symbolId=41, volume=r.volume,
                                                            tradeSide=r.tradeSide)))])
    expiry = datetime(2026, 10, 12, 20, 5, tzinfo=UTC)

    order_id = run(fake, lambda client: client.place_stop_order(
        _gold(), "buy", lots=2.27, stop_price=1348.36, stop_loss=1343.96, expiry=expiry,
        label="gtb", client_order_id="gtb-20261012-L"))

    assert order_id == 9001
    (req,) = fake.of_type(M.ProtoOANewOrderReq)
    assert (req.ctidTraderAccountId, req.symbolId) == (ACCOUNT, 41)
    assert (req.orderType, req.tradeSide, req.volume) == (MM.STOP, MM.BUY, 22_700)
    assert (req.stopPrice, req.stopLoss) == (1348.36, 1343.96)
    assert req.timeInForce == MM.GOOD_TILL_DATE
    assert req.expirationTimestamp == int(expiry.timestamp() * 1000)
    assert (req.label, req.clientOrderId) == ("gtb", "gtb-20261012-L")


def test_a_rejected_order_raises_with_the_broker_error_code():
    from datetime import UTC, datetime

    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM
    fake = FakeCTrader()
    fake.on(M.ProtoOANewOrderReq, lambda r: [M.ProtoOAExecutionEvent(
        ctidTraderAccountId=ACCOUNT, executionType=MM.ORDER_REJECTED,
        errorCode="NOT_ENOUGH_MONEY")])

    with pytest.raises(CTraderError) as e:
        run(fake, lambda client: client.place_stop_order(
            _gold(), "sell", lots=0.01, stop_price=4100.0, stop_loss=4140.0,
            expiry=datetime(2026, 10, 12, 20, 5, tzinfo=UTC), label="gtb",
            client_order_id="gtb-20261012-S"))

    assert e.value.code == "NOT_ENOUGH_MONEY"


def test_reconcile_lists_open_positions_and_pending_orders():
    from datetime import UTC, datetime

    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM

    from gtb.broker import PendingOrder, Position
    opened = datetime(2026, 10, 12, 9, 20, tzinfo=UTC)
    expiry = datetime(2026, 10, 12, 20, 5, tzinfo=UTC)
    fake = FakeCTrader()
    fake.on(M.ProtoOAReconcileReq, lambda r: [M.ProtoOAReconcileRes(
        ctidTraderAccountId=ACCOUNT,
        position=[MM.ProtoOAPosition(
            positionId=77, positionStatus=MM.POSITION_STATUS_OPEN, swap=0, price=4180.27,
            stopLoss=4148.83, tradeData=MM.ProtoOATradeData(
                symbolId=41, volume=8600, tradeSide=MM.BUY, label="gtb",
                openTimestamp=int(opened.timestamp() * 1000)))],
        order=[MM.ProtoOAOrder(
            orderId=9002, orderType=MM.STOP, orderStatus=MM.ORDER_STATUS_ACCEPTED,
            stopPrice=4120.55, stopLoss=4152.0, clientOrderId="gtb-20261012-S",
            expirationTimestamp=int(expiry.timestamp() * 1000),
            tradeData=MM.ProtoOATradeData(symbolId=41, volume=8600, tradeSide=MM.SELL,
                                          label="gtb"))])])

    positions, orders = run(fake, lambda client: client.reconcile())

    assert positions == [Position(position_id=77, symbol_id=41, side="buy", volume=8600,
                                  entry_price=4180.27, stop_loss=4148.83, label="gtb",
                                  opened_at=opened)]
    assert orders == [PendingOrder(order_id=9002, symbol_id=41, side="sell", volume=8600,
                                   stop_price=4120.55, stop_loss=4152.0, label="gtb",
                                   client_order_id="gtb-20261012-S", expiry=expiry)]


def _execution(execution_type):
    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM
    return lambda r: [M.ProtoOAExecutionEvent(ctidTraderAccountId=ACCOUNT,
                                              executionType=getattr(MM, execution_type))]


def test_cancels_a_pending_order_by_id():
    fake = FakeCTrader()
    fake.on(M.ProtoOACancelOrderReq, _execution("ORDER_CANCELLED"))

    run(fake, lambda client: client.cancel_order(9002))

    assert [(r.ctidTraderAccountId, r.orderId) for r in fake.of_type(M.ProtoOACancelOrderReq)] \
        == [(ACCOUNT, 9002)]


def test_closes_a_whole_position_at_market():
    fake = FakeCTrader()
    fake.on(M.ProtoOAClosePositionReq, _execution("ORDER_ACCEPTED"))

    run(fake, lambda client: client.close_position(77, volume=8600))

    assert [(r.positionId, r.volume) for r in fake.of_type(M.ProtoOAClosePositionReq)] \
        == [(77, 8600)]


def test_deals_in_a_window_with_money_scaled_and_closing_details():
    from datetime import UTC, datetime

    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM

    from gtb.broker import Deal
    entry = datetime(2026, 10, 12, 9, 20, tzinfo=UTC)
    exit_ = datetime(2026, 10, 12, 20, 50, tzinfo=UTC)

    def ms(dt):
        return int(dt.timestamp() * 1000)

    def deal(deal_id, side, price, when, close=None):
        return MM.ProtoOADeal(
            dealId=deal_id, orderId=deal_id + 100, positionId=77, volume=8600, filledVolume=8600,
            symbolId=41, createTimestamp=ms(when), executionTimestamp=ms(when),
            executionPrice=price, tradeSide=side, dealStatus=MM.FILLED, commission=-301,
            moneyDigits=2, closePositionDetail=close)

    fake = FakeCTrader()
    fake.on(M.ProtoOADealListReq, lambda r: [M.ProtoOADealListRes(
        ctidTraderAccountId=ACCOUNT, hasMore=False, deal=[
            deal(1, MM.BUY, 4180.27, entry),
            deal(2, MM.SELL, 4244.05, exit_, MM.ProtoOAClosePositionDetail(
                entryPrice=4180.27, grossProfit=548_508, swap=0, commission=-602,
                balance=10_548_508, moneyDigits=2))])])

    deals = run(fake, lambda client: client.deals(entry.replace(hour=0), exit_.replace(hour=23)))

    assert deals == [
        Deal(deal_id=1, order_id=101, position_id=77, side="buy", volume=8600, price=4180.27,
             time=entry, commission=-3.01, closing=False, entry_price=None, gross_profit=None),
        Deal(deal_id=2, order_id=102, position_id=77, side="sell", volume=8600, price=4244.05,
             time=exit_, commission=-3.01, closing=True, entry_price=4180.27,
             gross_profit=5485.08),
    ]
    (req,) = fake.of_type(M.ProtoOADealListReq)
    assert (req.fromTimestamp, req.toTimestamp) == (ms(entry.replace(hour=0)),
                                                    ms(exit_.replace(hour=23)))


def test_ticks_are_rebuilt_from_deltas_and_returned_oldest_first():
    from datetime import UTC, datetime, timedelta

    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM
    newest = datetime(2026, 10, 12, 9, 20, 3, tzinfo=UTC)
    fake = FakeCTrader()
    fake.on(M.ProtoOAGetTickDataReq, lambda r: [M.ProtoOAGetTickDataRes(
        ctidTraderAccountId=ACCOUNT, hasMore=False, tickData=[
            MM.ProtoOATickData(timestamp=int(newest.timestamp() * 1000), tick=418_029_000),
            MM.ProtoOATickData(timestamp=-1_500, tick=-2_000),     # 1.5 s earlier, 0.02 lower
            MM.ProtoOATickData(timestamp=-2_000, tick=1_000)])])   # 2 s before that, +0.01

    ticks = run(fake, lambda client: client.ticks(
        41, "ask", newest - timedelta(seconds=10), newest))

    assert ticks == [(newest - timedelta(seconds=3.5), 4180.28),
                     (newest - timedelta(seconds=1.5), 4180.27),
                     (newest, 4180.29)]
    (req,) = fake.of_type(M.ProtoOAGetTickDataReq)
    assert (req.symbolId, req.type) == (41, MM.ASK)


def test_answers_server_heartbeats_and_still_gets_its_reply():
    from ctrader_open_api.messages import OpenApiCommonMessages_pb2 as C
    fake = FakeCTrader()
    _gold_symbol(fake)
    fake.before_reply = [C.ProtoHeartbeatEvent()]

    gold = run(fake, lambda client: client.symbol("XAUUSD"))

    assert gold.symbol_id == 41
    assert fake.of_type(C.ProtoHeartbeatEvent)


def test_sends_heartbeats_while_idle():
    from ctrader_open_api.messages import OpenApiCommonMessages_pb2 as C
    fake = FakeCTrader()

    async def body():
        port = await fake.start()
        try:
            client = CTraderClient("127.0.0.1", port, client_id="app-id",
                                   client_secret="app-secret", access_token="token-1",
                                   account_id=ACCOUNT, use_tls=False, timeout=5,
                                   heartbeat_every=0.05)
            async with client:
                await asyncio.sleep(0.3)
        finally:
            await fake.stop()

    asyncio.run(body())

    assert len(fake.of_type(C.ProtoHeartbeatEvent)) >= 3


def test_trendbars_before_the_start_are_dropped_because_the_server_ignores_it():
    # Seen live: GetTrendbarsReq returns `count` bars back from toTimestamp, even ones opened
    # before fromTimestamp. Only bars in [start, end] may come back, and paging stops there.
    from datetime import UTC, datetime, timedelta

    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM
    t0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    first = int(t0.timestamp()) // 60 - 5 * 50            # 50 bars before the start exist

    def page(r):
        newest = r.toTimestamp // 60_000
        bars = [MM.ProtoOATrendbar(utcTimestampInMinutes=m, low=400_000_000, deltaOpen=0,
                                   deltaHigh=10, deltaClose=5, volume=1)
                for m in range(first, newest + 1, 5)][-r.count:][-3:]  # pages of 3
        return [M.ProtoOAGetTrendbarsRes(ctidTraderAccountId=ACCOUNT, period=MM.M5, timestamp=0,
                                         trendbar=bars)]

    fake = FakeCTrader()
    fake.on(M.ProtoOAGetTrendbarsReq, page)

    bars = run(fake, lambda client: client.trendbars(41, "M5", t0, t0 + timedelta(minutes=20)))

    assert [b.ts for b in bars] == [t0 + timedelta(minutes=5 * i) for i in range(5)]
    assert len(fake.of_type(M.ProtoOAGetTrendbarsReq)) == 2


def test_a_rate_limited_request_is_retried_after_a_pause():
    # Seen live: paging 45 days of M5 back to back gets "BLOCKED_PAYLOAD_TYPE: You are being
    # rate limited" (historical data is limited to ~5 requests a second).
    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as MM
    fake = FakeCTrader()
    replies = iter([
        [M.ProtoOAErrorRes(errorCode="BLOCKED_PAYLOAD_TYPE", description="You are being rate "
                           "limited")],
        [M.ProtoOATraderRes(ctidTraderAccountId=ACCOUNT, trader=MM.ProtoOATrader(
            ctidTraderAccountId=ACCOUNT, balance=12_345, moneyDigits=2, depositAssetId=1))],
    ])
    fake.on(M.ProtoOATraderReq, lambda r: next(replies))

    async def body():
        port = await fake.start()
        try:
            async with CTraderClient("127.0.0.1", port, client_id="a", client_secret="b",
                                     access_token="t", account_id=ACCOUNT, use_tls=False,
                                     timeout=5, rate_limit_pause=0) as client:
                return await client.balance()
        finally:
            await fake.stop()

    assert asyncio.run(body()) == 123.45
    assert len(fake.of_type(M.ProtoOATraderReq)) == 2
