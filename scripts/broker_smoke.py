"""Manual end-to-end check of CTraderClient against the real Open API (IMPLEMENTATION_PLAN Phase 3).

    uv run python scripts/broker_smoke.py [--bars-out DIR] [--keep MINUTES]

Reads CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET / CTRADER_ACCESS_TOKEN / CTRADER_ACCOUNT_ID from
.env. Read-only checks run on any account; the order round trip (a buy stop far above the market,
then cancelled -- or, with --keep, left to expire on its own after MINUTES) runs ONLY when the
API reports the account as demo.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import dotenv

from gtb.broker import DEMO_HOST, LIVE_HOST, CTraderClient, CTraderError


def _client(host: str) -> CTraderClient:
    return CTraderClient(
        host, client_id=os.environ["CTRADER_CLIENT_ID"],
        client_secret=os.environ["CTRADER_CLIENT_SECRET"],
        access_token=os.environ["CTRADER_ACCESS_TOKEN"],
        account_id=int(os.environ["CTRADER_ACCOUNT_ID"]))


async def _connect() -> tuple[CTraderClient, str]:
    errors = []
    for host in (DEMO_HOST, LIVE_HOST):
        client = _client(host)
        try:
            await client.__aenter__()
            return client, host
        except CTraderError as e:
            errors.append(f"{host}: {e}")
    raise SystemExit("could not authenticate: " + "; ".join(errors))


def _last_weekday_session(now: datetime) -> datetime:
    """Midnight UTC of the most recent fully finished weekday."""
    day = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


async def main(bars_out: Path | None, keep: int | None) -> None:
    client, host = await _connect()
    try:
        live = await client.is_live()
        print(f"connected to {host}; account {client.account_id} is {'LIVE' if live else 'demo'}")
        gold = await client.symbol("XAUUSD")
        print(f"XAUUSD: {gold}; 0.01 lot = volume {gold.volume(0.01)}")
        print(f"balance: {await client.balance():,.2f}")

        day = _last_weekday_session(datetime.now(UTC))
        week = day - timedelta(days=4)
        m5 = await client.trendbars(gold.symbol_id, "M5", week, day + timedelta(days=1))
        h1 = await client.trendbars(gold.symbol_id, "H1", week, day + timedelta(days=1))
        print(f"M5 {len(m5)} bars {m5[0].ts} -> {m5[-1].ts}; H1 {len(h1)} bars")
        if bars_out:
            bars_out.mkdir(parents=True, exist_ok=True)
            for name, bars in (("m5", m5), ("h1", h1)):
                with open(bars_out / f"smoke_{name}.csv", "w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["ts", "open", "high", "low", "close"])
                    w.writerows([b.ts.isoformat(), b.open, b.high, b.low, b.close] for b in bars)
            print(f"bars written to {bars_out}")

        at = day + timedelta(hours=14)
        bid = await client.ticks(gold.symbol_id, "bid", at, at + timedelta(seconds=30))
        ask = await client.ticks(gold.symbol_id, "ask", at, at + timedelta(seconds=30))
        print(f"ticks {at:%Y-%m-%d %H:%M:%S}+30s: {len(bid)} bid, {len(ask)} ask")
        if bid and ask:
            inside = all(at <= t <= at + timedelta(seconds=30) for t, _ in bid + ask)
            print(f"  first bid {bid[0]}, first ask {ask[0]}; all inside window: {inside}; "
                  f"spread ~{ask[0][1] - bid[0][1]:.2f}")

        positions, orders = await client.reconcile()
        print(f"reconcile: {len(positions)} positions, {len(orders)} pending orders")

        if live:
            print("LIVE account: skipping the order round trip")
            return
        price = (m5[-1].close if m5 else 4000.0) * 1.2
        stop, sl = round(price, gold.digits), round(price * 0.99, gold.digits)
        expiry = datetime.now(UTC) + timedelta(minutes=keep or 10)
        try:
            order_id = await client.place_stop_order(
                gold, "buy", lots=0.01, stop_price=stop, stop_loss=sl, expiry=expiry,
                label="gtb-smoke", client_order_id=f"gtb-smoke-{expiry:%H%M%S}")
        except CTraderError as e:
            print(f"order rejected: {e}")
            return
        _, orders = await client.reconcile()
        mine = [o for o in orders if o.order_id == order_id]
        print(f"placed buy stop {order_id} at {stop} / SL {sl}: visible in reconcile: {mine}")
        if keep:
            print(f"left in place; the broker expires it at {expiry:%H:%M:%S} UTC")
            return
        await client.cancel_order(order_id)
        _, orders = await client.reconcile()
        print(f"cancelled; still pending: {[o for o in orders if o.order_id == order_id]}")
    finally:
        await client.close()


if __name__ == "__main__":
    dotenv.load_dotenv(Path(__file__).parents[1] / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars-out", type=Path)
    parser.add_argument("--keep", type=int, metavar="MINUTES",
                        help="leave the test order to expire after MINUTES instead of cancelling")
    args = parser.parse_args()
    asyncio.run(main(args.bars_out, args.keep))
