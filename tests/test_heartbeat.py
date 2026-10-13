"""The heartbeat (ARCHITECTURE §4.3): at 09:10, alert if the morning run left no record. It no
longer touches the cTrader token: the platform refreshes the shared pair."""

import asyncio
from datetime import timedelta

from gtb.records import DayRecord
from gtb.runs import heartbeat

from fake_broker import make_deps
from synthetic import DAY

AT = DAY + timedelta(hours=9, minutes=10)


def _with_morning_record(tmp_path):
    deps = make_deps(tmp_path, AT)
    deps.store.put(DayRecord(date=DAY.date(), env="dev", code_version="abc123"))
    return deps


def test_a_missing_morning_record_is_alerted(tmp_path):
    deps = make_deps(tmp_path, AT)

    asyncio.run(heartbeat(deps))

    (message,) = deps.notifier.sent
    assert message.startswith("ALERT") and "morning" in message


def test_with_a_morning_record_the_heartbeat_is_quiet(tmp_path):
    deps = _with_morning_record(tmp_path)

    asyncio.run(heartbeat(deps))

    assert deps.notifier.sent == []
