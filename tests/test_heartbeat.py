"""The heartbeat (ARCHITECTURE §4.3): at 09:10, alert if the morning run left no record; keep the
cTrader token fresh (refreshed once it is 7 days old)."""

import asyncio
from datetime import timedelta

from gtb.records import DayRecord
from gtb.runs import heartbeat

from fake_broker import make_deps
from synthetic import DAY

AT = DAY + timedelta(hours=9, minutes=10)


class FakeTokens:
    def __init__(self, refreshed_at, fail=False):
        self._at, self.fail, self.refreshes = refreshed_at, fail, []

    def refreshed_at(self):
        return self._at

    async def refresh(self, now):
        if self.fail:
            raise ConnectionError("cTrader unreachable")
        self.refreshes.append(now)
        self._at = now


def _with_morning_record(tmp_path):
    deps = make_deps(tmp_path, AT)
    deps.store.put(DayRecord(date=DAY.date(), env="dev", code_version="abc123"))
    return deps


def test_a_missing_morning_record_is_alerted(tmp_path):
    deps = make_deps(tmp_path, AT)

    asyncio.run(heartbeat(deps, FakeTokens(AT - timedelta(days=1))))

    (message,) = deps.notifier.sent
    assert message.startswith("ALERT") and "morning" in message


def test_with_a_morning_record_and_a_fresh_token_the_heartbeat_is_quiet(tmp_path):
    deps = _with_morning_record(tmp_path)
    tokens = FakeTokens(AT - timedelta(days=6, hours=23))

    asyncio.run(heartbeat(deps, tokens))

    assert (deps.notifier.sent, tokens.refreshes) == ([], [])


def test_a_week_old_token_is_refreshed(tmp_path):
    deps = _with_morning_record(tmp_path)
    tokens = FakeTokens(AT - timedelta(days=7))

    asyncio.run(heartbeat(deps, tokens))

    assert tokens.refreshes == [AT] and deps.notifier.sent == []


def test_a_failed_token_refresh_is_alerted(tmp_path):
    deps = _with_morning_record(tmp_path)

    asyncio.run(heartbeat(deps, FakeTokens(None, fail=True)))       # never refreshed: due

    (message,) = deps.notifier.sent
    assert message.startswith("ALERT") and "token" in message and "unreachable" in message
