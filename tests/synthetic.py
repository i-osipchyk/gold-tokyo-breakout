"""Synthetic bars in winter (EST): 4h buckets start at 22/02/06/10/14/18 UTC, so at 09:00 the
bucket 06:00-10:00 is still unsealed. Quiet bars are level +/- 5, so every sealed 4h true range is
10 and ATR(4h, 20) is exactly 10."""

from datetime import UTC, datetime, timedelta

from gtb.core import Bar

DAY = datetime(2025, 2, 10, tzinfo=UTC)          # Monday
HISTORY_START = DAY - timedelta(days=35)         # 25 weekdays before DAY


def bars(start, step, n, level=2000.0, half=5.0):
    return [Bar(start + step * i, level, level + half, level - half, level) for i in range(n)]


def _weekdays(start, end):
    day = start
    while day < end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def history():
    """Tokyo 5m bars and 1h bars (all day) at 2000 +/- 5 for the weekdays before DAY."""
    m5, h1 = [], []
    for day in _weekdays(HISTORY_START, DAY):
        m5 += bars(day, timedelta(minutes=5), 108)
        h1 += bars(day, timedelta(hours=1), 24)
    return m5, h1


def wide_tokyo(day=DAY):
    """00:00-06:00 at 2000 +/- 5, 06:00-09:00 at 2006 +/- 5: range 1995-2011 = 16."""
    m5 = bars(day, timedelta(minutes=5), 72) + bars(
        day + timedelta(hours=6), timedelta(minutes=5), 36, level=2006.0)
    h1 = bars(day, timedelta(hours=1), 6) + bars(
        day + timedelta(hours=6), timedelta(hours=1), 3, level=2006.0)
    return m5, h1


def narrow_tokyo(day=DAY):
    """00:00-09:00 at 2000 +/- 2: range 4, below the median 10."""
    return (bars(day, timedelta(minutes=5), 108, half=2.0),
            bars(day, timedelta(hours=1), 9, half=2.0))
