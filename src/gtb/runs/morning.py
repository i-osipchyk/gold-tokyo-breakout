"""morning: plan the day after the 08:55 bar and place both stop orders (ARCHITECTURE §4.3)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

from ..alerts.messages import alert, morning_summary
from ..broker import CTraderError
from ..core import plan_day
from ..records import DayRecord, Decision, Inputs, OrderRecord
from .deps import LABEL, SYMBOL, Deps, client_order_id

PLAN_AT = time(9, 0, 10)
RETRY_EVERY = timedelta(seconds=15)
CUTOFF = time(9, 5)
M5_DAYS, H1_DAYS = 45, 90


async def morning(deps: Deps) -> DayRecord:
    day = deps.clock.now().date()
    done = deps.store.get(day)
    if done is not None and done.inputs is not None:
        return done                       # a duplicate or retried invocation
    await deps.clock.sleep_until(datetime.combine(day, PLAN_AT, tzinfo=UTC))
    broker = deps.broker
    gold = await broker.symbol(SYMBOL)
    equity = await broker.balance()

    cutoff = datetime.combine(day, CUTOFF, tzinfo=UTC)
    attempts = 0
    while True:
        attempts += 1
        now = deps.clock.now()
        m5 = await broker.trendbars(gold.symbol_id, "M5", now - timedelta(days=M5_DAYS), now)
        h1 = await broker.trendbars(gold.symbol_id, "H1", now - timedelta(days=H1_DAYS), now)
        plan = plan_day(day, m5, h1, equity, early_closes=deps.early_closes, risk=deps.risk)
        if plan.reason != "no_plan_bar" or now + RETRY_EVERY > cutoff:
            break
        await deps.clock.sleep_until(now + RETRY_EVERY)
    if plan.reason == "no_plan_bar" or (plan.trade and now > cutoff):   # e.g. a late retry
        plan = replace(plan, trade=False, reason="cutoff_missed", orders=())

    record = DayRecord(
        date=day, env=deps.env, code_version=deps.code_version,
        inputs=Inputs(tokyo_high=plan.tokyo_high, tokyo_low=plan.tokyo_low, bars=plan.bars,
                      range=plan.range, history_len=plan.history_len, median=plan.median,
                      atr=plan.atr, equity=equity, plan_time=now, attempts=attempts),
        decision=Decision(trade=plan.trade, reason=plan.reason))
    if plan.reason == "cutoff_missed":
        record.alerts.append(f"cutoff missed: no plan by {CUTOFF:%H:%M} (last attempt "
                             f"{now:%H:%M:%S}), no orders today")
    elif plan.reason == "skipped_min_size":
        record.alerts.append(f"skipped_min_size: equity {equity:,.2f} is too small for 0.01 lot "
                             f"at ATR {plan.atr:.3f}, no orders today")
    _, pending = await broker.reconcile()
    resting = {p.client_order_id: p.order_id for p in pending if p.label == LABEL}
    for o in plan.orders:
        assert plan.d is not None and plan.shortened is not None
        coid = client_order_id(day, o.side)
        order_id, status = resting.get(coid), "placed"
        if order_id is None:
            try:
                order_id = await broker.place_stop_order(
                    gold, o.side, lots=o.lots, stop_price=o.entry, stop_loss=o.stop_loss,
                    expiry=o.expiry, label=LABEL, client_order_id=coid)
            except CTraderError as e:
                status = "rejected"
                record.alerts.append(f"{o.side} stop {o.entry} rejected: {e}")
        record.orders.append(OrderRecord(
            side=o.side, entry=o.entry, sl=o.stop_loss, d=plan.d, shortened=plan.shortened,
            lots=o.lots, expiry=o.expiry, client_order_id=coid, status=status,
            order_id=order_id))
    deps.store.put(record)
    for text in record.alerts:
        await deps.notifier.send(alert(record, text))
    await deps.notifier.send(morning_summary(record))
    return record
