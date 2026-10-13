"""Reconciliation (ARCHITECTURE §4.5): live DayRecords against the Hermes static-variant replay,
in three layers."""

from datetime import UTC, date, datetime, timedelta

from gtb.records import DayRecord, Decision, Inputs, OrderRecord, TradeRecord
from reconcile.compare import compare
from reconcile.replay import Replay, ReplayDay, ReplayTrade

DAY = date(2026, 10, 12)


def _at(h, m, s=0):
    return datetime(2026, 10, 12, h, m, s, tzinfo=UTC)


def _replay_day(**kw):
    day = dict(date=DAY, tokyo_high=4012.50, tokyo_low=3990.10, bars=108, range=22.40,
               history_len=20, median=18.00, atr=37.25, equity=100_000.0, trade=True,
               reason=None, long_entry=4013.245, long_sl=3975.995, short_entry=3989.355,
               short_sl=4026.605, d=37.25, shortened=False, lots=0.26)
    day.update(kw)
    return ReplayDay(**day)


def _record(trades=(), **inputs):
    i = dict(tokyo_high=4012.50, tokyo_low=3990.10, bars=108, range=22.40, history_len=20,
             median=18.00, atr=37.25, equity=100_000.0, plan_time=_at(9, 0, 12), attempts=1)
    i.update(inputs)
    return DayRecord(
        date=DAY, env="dev", code_version="abc", inputs=Inputs(**i),
        decision=Decision(trade=True, reason=None),
        orders=[OrderRecord("buy", 4013.25, 3976.00, 37.25, False, 0.26, _at(20, 5),
                            "gtb-20261012-L", "placed", 1),
                OrderRecord("sell", 3989.36, 4026.61, 37.25, False, 0.26, _at(20, 5),
                            "gtb-20261012-S", "placed", 2)],
        trades=list(trades))


def _replay(day=None, trades=(), bars=()):
    day = day or _replay_day()
    return Replay(days={day.date: day}, trades=list(trades), bars=list(bars), risk=0.01)


def _problems(report, layer):
    return [(f.field, f.status) for f in report.findings
            if f.layer == layer and f.status != "ok"]


def test_identical_inputs_and_an_atr_within_a_tenth_of_a_percent_pass_layer_1():
    report = compare([_record(atr=37.25 * 1.0005)], _replay(), risk=0.01)

    assert _problems(report, 1) == []


def test_a_range_one_tick_off_is_a_layer_1_mismatch():
    report = compare([_record(tokyo_high=4012.51, range=22.41)], _replay(), risk=0.01)

    assert ("range", "mismatch") in _problems(report, 1)
    assert ("tokyo_high", "mismatch") in _problems(report, 1)


def test_a_different_gate_decision_is_a_layer_1_mismatch():
    record = _record()
    record.decision = Decision(trade=False, reason="narrow_range")

    report = compare([record], _replay(), risk=0.01)

    assert ("decision", "mismatch") in _problems(report, 1)


def test_days_on_one_side_only_are_reported_missing_and_a_missed_cutoff_is_not_a_bug():
    tuesday = _replay_day(date=DAY + timedelta(days=1))
    replay = Replay(days={DAY: _replay_day(), tuesday.date: tuesday}, trades=[], bars=[],
                    risk=0.01)
    missed = _record()
    missed.decision = Decision(trade=False, reason="cutoff_missed")
    missed.orders = []

    report = compare([missed], replay, risk=0.01)

    assert [(f.day, f.field, f.status) for f in report.findings if f.status != "ok"] == [
        (DAY, "decision", "missing"), (tuesday.date, "record", "missing")]


def test_levels_within_half_a_tick_pass_layer_2_and_a_stop_five_ticks_off_does_not():
    assert _problems(compare([_record()], _replay(), risk=0.01), 2) == []

    report = compare([_record()], _replay(_replay_day(long_sl=3975.95)), risk=0.01)

    assert _problems(report, 2) == [("buy.sl", "mismatch")]


def test_a_different_shortened_flag_is_a_layer_2_mismatch():
    report = compare([_record()], _replay(_replay_day(shortened=True)), risk=0.01)

    assert ("shortened", "mismatch") in _problems(report, 2)


def _with_lots(record, lots):
    for o in record.orders:
        o.lots = lots
    return record


def test_risk_is_compared_as_a_share_of_each_sides_risk_budget():
    # Replay: 0.26 lot x 37.25 x 100 oz on 100,000 = 0.97% of a 1% budget.
    assert _problems(compare([_record()], _replay(), risk=0.01), 2) == []
    # Live at 0.25% on 30,000: 0.02 lot risks 74.50 = 0.99 of the budget -- the same sizing.
    live = _with_lots(_record(equity=30_000.0), 0.02)
    assert _problems(compare([live], _replay(), risk=0.0025), 2) == []
    # 0.20 lot on 100,000 is 0.75 of the budget: more than a lot step away.
    undersized = _with_lots(_record(), 0.20)
    assert _problems(compare([undersized], _replay(), risk=0.01), 2) == [("risk", "mismatch")]


def _bar(h, m, high, low):
    from gtb.core import Bar
    return Bar(_at(h, m), low, high, low, low)


def _live_buy(at, price=4013.31, spread=0.15, **kw):
    t = dict(side="buy", entry_time=at, entry_price=price, spread_at_entry=spread,
             exit_time=_at(20, 50, 5), exit_price=4040.0, spread_at_exit=0.2, exit_reason="eod",
             lots=0.26, commission=-1.82, pnl=692.12, risk_usd=1000.0, r=0.69212,
             position_id=9)
    t.update(kw)
    return TradeRecord(**t)


def _replay_buy(at, **kw):
    t = dict(entry_time=at, side="buy", entry_price=4013.245, stop_loss=3975.995, lots=0.26,
             exit_time=_at(20, 50), exit_price=4040.0, exit_reason="signal", net_pnl=694.0)
    t.update(kw)
    return ReplayTrade(**t)


def _trigger(report):
    return [(f.field, f.live, f.replay, f.status) for f in report.findings
            if f.field.endswith(".trigger")]


def test_the_same_side_filling_in_the_same_5m_bar_matches():
    report = compare([_record([_live_buy(_at(10, 3, 12))])],
                     _replay(trades=[_replay_buy(_at(10, 0))]), risk=0.01)

    assert _trigger(report) == [("buy.trigger", _at(10, 0), _at(10, 0), "ok")]


def test_a_live_only_buy_is_explained_when_the_bid_came_within_a_spread_of_the_entry():
    # The bid topped at 4013.20, 0.045 short of the entry; with a 0.15 spread the ask crossed.
    marginal = _replay(bars=[_bar(10, 0, 4013.20, 4005.0)])
    report = compare([_record([_live_buy(_at(10, 3, 12))])], marginal, risk=0.01)

    assert _trigger(report) == [("buy.trigger", _at(10, 0), None, "explained")]
    assert "ask" in [f for f in report.findings if f.field == "buy.trigger"][0].note


def test_a_live_only_buy_far_from_the_bid_is_a_mismatch():
    far = _replay(bars=[_bar(10, 0, 4012.00, 4005.0)])
    report = compare([_record([_live_buy(_at(10, 3, 12))])], far, risk=0.01)

    assert _trigger(report) == [("buy.trigger", _at(10, 0), None, "mismatch")]


def test_a_live_buy_one_bar_early_by_a_marginal_touch_is_explained_and_replay_only_is_not():
    early = _replay(trades=[_replay_buy(_at(10, 5))], bars=[_bar(10, 0, 4013.20, 4005.0)])
    report = compare([_record([_live_buy(_at(10, 3, 12), spread=None)])], early, risk=0.01)
    assert _trigger(report) == [("buy.trigger", _at(10, 0), _at(10, 5), "explained")]

    replay_only = _replay(trades=[_replay_buy(_at(10, 5))])
    report = compare([_record()], replay_only, risk=0.01)
    assert _trigger(report) == [("buy.trigger", None, _at(10, 5), "mismatch")]


def _live_sell(**kw):
    t = dict(side="sell", entry_time=_at(11, 1, 40), entry_price=3989.30, spread_at_entry=0.15,
             exit_time=_at(20, 50, 5), exit_price=3980.0, spread_at_exit=0.2, exit_reason="eod",
             lots=0.26, commission=-1.82, pnl=240.0, risk_usd=1000.0, r=0.24, position_id=10)
    t.update(kw)
    return TradeRecord(**t)


def _replay_sell(**kw):
    t = dict(entry_time=_at(11, 0), side="sell", entry_price=3989.355, stop_loss=4026.605,
             lots=0.26, exit_time=_at(20, 50), exit_price=3980.0, exit_reason="signal",
             net_pnl=242.0)
    t.update(kw)
    return ReplayTrade(**t)


def _exits(report):
    return [(f.field, f.live, f.replay, f.status) for f in report.findings
            if f.field.endswith(".exit")]


def test_a_flatten_in_both_matches():
    report = compare([_record([_live_sell()])], _replay(trades=[_replay_sell()]), risk=0.01)

    assert _exits(report) == [("sell.exit", ("eod", _at(20, 50)), ("eod", _at(20, 50)), "ok")]


def test_a_short_stopped_live_by_the_ask_while_the_replay_held_to_the_flatten_is_explained():
    # Bid high 4026.50 at 15:00, 0.105 below the 4026.61 stop: the ask (spread 0.20) touched it.
    stopped = _live_sell(exit_time=_at(15, 2, 7), exit_price=4026.61, exit_reason="sl",
                         spread_at_exit=0.20)
    replay = _replay(trades=[_replay_sell()], bars=[_bar(15, 0, 4026.50, 4020.0)])

    report = compare([_record([stopped])], replay, risk=0.01)

    assert _exits(report) == [
        ("sell.exit", ("sl", _at(15, 0)), ("eod", _at(20, 50)), "explained")]


def test_layer_3_reports_execution_and_live_minus_replay_r_per_matched_trade():
    live = _live_buy(_at(9, 41, 2), price=4013.31, r=0.69212, commission=-1.82)
    rep = _replay_buy(_at(9, 40), entry_price=4013.245, net_pnl=694.0)

    report = compare([_record([live])], _replay(trades=[rep]), risk=0.01)

    (row,) = report.execution
    assert (row.day, row.side, row.live_entry, row.replay_entry) == (DAY, "buy", 4013.31, 4013.245)
    assert (row.live_exit, row.replay_exit, row.spread_at_entry, row.commission) == (
        4040.0, 4040.0, 0.15, -1.82)
    # Replay: +694 on 100,000 at 1% = +0.694 R; live +0.69212 R.
    assert (row.live_r, row.replay_r, round(row.r_diff, 5)) == (0.69212, 0.694, -0.00188)
