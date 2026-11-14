"""A completed-task window holding more than one v2 answer is read in parts.

``ticktick-py`` asks v2 for completed tasks with a fixed limit of 100, and
TickTick answers with the newest that many across the account (measured
14 Nov 2026). The unprocessed queue filters by project only afterwards, so
without splitting, a completion older than the newest 100 in the window never
reaches it, whatever ``days`` says.

These tests run the real ``TaskManager.get_completed`` and fake only its HTTP
call, so the library's own checks and its day and zone conversion are in play.
"""

import asyncio
import datetime
import json
import logging
import types
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from ticktick.managers.tasks import TaskManager

from ticktick_mcp.tools import filter_tools
from ticktick_mcp.tools.filter_tools import PeriodFilter, TaskFilterer

PROJECT = "pppppppppppppppppppppppp"
OTHER = "oooooooooooooooooooooooo"
PAGE = 100
WIRE = "%Y-%m-%d %H:%M:%S"


class FakeV2:
    """v2's completed list: the UTC window asked for, newest first, at most 100."""

    def __init__(self, completions, ignore_dates=False):
        self.completions = completions
        self.ignore_dates = ignore_dates
        self.requests = []

    def http_get(self, url, params=None, **_):
        self.requests.append(params)
        low = datetime.datetime.strptime(params["from"], WIRE)
        high = datetime.datetime.strptime(params["to"], WIRE)
        hits = [t for t in self.completions if self.ignore_dates or low <= _utc(t) <= high]
        hits.sort(key=_utc, reverse=True)
        return hits[:PAGE]


def _utc(task):
    return datetime.datetime.strptime(task["completedTime"][:19], "%Y-%m-%dT%H:%M:%S")


def _completions(days, per_day, project=OTHER, prefix="c", hour=12):
    return [
        {
            "id": f"{prefix}{d.isoformat()}-{n}",
            "projectId": project,
            "status": 2,
            "completedTime": (
                datetime.datetime.combine(d, datetime.time(hour)) + datetime.timedelta(minutes=n)
            ).strftime("%Y-%m-%dT%H:%M:%S.000+0000"),
        }
        for d in days
        for n in range(per_day)
    ]


def _days(first, count):
    return [first + datetime.timedelta(days=i) for i in range(count)]


def _client(fake, account_zone="Europe/London"):
    manager = TaskManager.__new__(TaskManager)
    manager._client = types.SimpleNamespace(
        BASE_URL="https://api.example/",
        time_zone=account_zone,
        cookies=None,
        http_get=fake.http_get,
    )
    manager.headers = {}
    client = MagicMock()
    client.task = manager
    return client


def _fetch(fake, start, end, tz_info=None):
    with patch(
        "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
        return_value=_client(fake),
    ):
        return asyncio.run(
            TaskFilterer()._fetch_tasks_by_status(
                "completed", PeriodFilter(start_date=start, end_date=end), tz_info
            )
        )


AUGUST = _days(datetime.date(2026, 8, 1), 30)


class TestAFullAnswerIsSplit:
    def test_every_completion_comes_back_however_many_splits_it_takes(self):
        # 15 a day is full at 30, 15 and 8 days, so this needs three levels.
        fake = FakeV2(_completions(AUGUST, 15))

        result = _fetch(fake, "2026-08-01", "2026-08-30")

        assert len(result) == 450
        assert len({t["id"] for t in result}) == 450

    def test_the_window_is_halved_rather_than_walked(self):
        """1 + 2 + 4 + 8 requests for the same data; a split that peels one
        day at a time sends dozens."""
        fake = FakeV2(_completions(AUGUST, 15))

        _fetch(fake, "2026-08-01", "2026-08-30")

        assert len(fake.requests) == 15

    def test_the_newest_come_first(self):
        fake = FakeV2(_completions(AUGUST, 15))

        result = _fetch(fake, "2026-08-01", "2026-08-30")

        assert [_utc(t) for t in result] == sorted((_utc(t) for t in result), reverse=True)

    def test_every_request_is_built_for_the_zone_asked_for(self):
        """The account is in London; asked in New York, every part's day
        starts at 04:00 UTC in August, not London's 23:00."""
        fake = FakeV2(_completions(AUGUST, 15))

        _fetch(fake, "2026-08-01", "2026-08-30", ZoneInfo("America/New_York"))

        assert len(fake.requests) > 1
        assert {r["from"][-8:] for r in fake.requests} == {"04:00:00"}

    def test_a_start_with_a_time_of_day_is_read_as_its_whole_day(self):
        """The queue's window starts at the current time of day, and the
        library refuses an end before its start before it widens either."""
        fake = FakeV2(_completions(AUGUST[:2], 60, hour=16))

        result = _fetch(fake, "2026-08-01T15:00:00", "2026-08-02T10:00:00")

        assert len(result) == 120

    def test_a_server_ignoring_the_dates_returns_no_duplicates(self):
        fake = FakeV2(_completions(AUGUST, 15), ignore_dates=True)

        result = _fetch(fake, "2026-08-01", "2026-08-30")

        assert len(result) == len({t["id"] for t in result})


class TestASameDayWindow:
    def test_a_start_later_in_the_day_than_the_end_reads_that_day(self):
        """Bounds are whole days, so 15:00 to 09:00 on one day is that day.
        The library itself refuses those two times as an end before a start."""
        fake = FakeV2(_completions([datetime.date(2026, 8, 10)], 3))

        result = _fetch(fake, "2026-08-10T15:00:00", "2026-08-10T09:00:00")

        assert len(result) == 3


class TestAnAnswerUnderOnePageIsOneRequest:
    def test_a_window_under_one_answer_makes_one_request(self):
        fake = FakeV2(_completions(AUGUST, 3))

        result = _fetch(fake, "2026-08-01", "2026-08-30")

        assert len(result) == 90
        assert len(fake.requests) == 1


class TestASingleFullDay:
    def test_a_full_day_is_not_split_and_is_logged(self, caplog):
        fake = FakeV2(_completions([datetime.date(2026, 8, 10)], 120))

        with caplog.at_level(logging.WARNING, logger="ticktick_mcp.tools.filter_tools"):
            result = _fetch(fake, "2026-08-10", "2026-08-10")

        assert len(result) == PAGE
        assert len(fake.requests) == 1
        assert "2026-08-10" in caplog.text

    def test_a_start_with_no_end_is_one_request_even_when_full(self):
        """``get_completed`` reads a lone start as that one day."""
        fake = FakeV2(_completions([datetime.date(2026, 8, 10)], 120))

        result = _fetch(fake, "2026-08-10", None)

        assert len(result) == PAGE
        assert len(fake.requests) == 1


class TestTheAnswerShapes:
    def test_no_answer_is_an_empty_list(self):
        client = MagicMock()
        client.task.get_completed.return_value = None
        day = datetime.datetime(2026, 8, 1)
        assert filter_tools._get_completed(client, day, None, None) == []

    def test_a_single_object_is_a_list_of_one(self):
        client = MagicMock()
        client.task.get_completed.return_value = {"id": "x"}
        day = datetime.datetime(2026, 8, 1)
        assert filter_tools._get_completed(client, day, None, None) == [{"id": "x"}]


class TestTheQueueSeesPastTheFirstAnswer:
    def test_an_older_completion_reaches_the_unprocessed_queue(self):
        from ticktick_mcp.tools.completion_tools import ticktick_get_unprocessed_completions

        today = datetime.datetime.now(datetime.timezone.utc).date()
        recent = _days(today - datetime.timedelta(days=24), 25)
        older = today - datetime.timedelta(days=27)
        mine = _completions([older], 1, project=PROJECT, prefix="mine")
        fake = FakeV2(_completions(recent, 5) + mine)

        client = _client(fake)
        client.state = {"tasks": [], "projects": [{"id": PROJECT, "name": "Home"}]}
        client.inbox_id = "inbox1"
        with (
            patch(
                "ticktick_mcp.tools.completion_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
        ):
            queue = json.loads(asyncio.run(ticktick_get_unprocessed_completions(PROJECT, days=30)))

        assert [t["id"] for t in queue] == [mine[0]["id"]]
