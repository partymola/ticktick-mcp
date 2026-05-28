"""Tests for src/ticktick_mcp/tools/filter_tools.py."""

import asyncio
import json
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from ticktick_mcp.tools.filter_tools import (
    PeriodFilter,
    PropertyFilter,
    TaskFilterer,
    _build_property_filter,
    ticktick_filter_tasks,
)


def run(coro):
    """Helper to run an async function synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# PeriodFilter validator (format_time)
# ---------------------------------------------------------------------------


class TestPeriodFilterValidator:
    """Behaviour of the start_date/end_date validator."""

    def test_date_only_string_parses_to_start_of_day(self):
        pf = PeriodFilter(start_date="2024-08-01")
        assert pf.start_date.year == 2024
        assert pf.start_date.month == 8
        assert pf.start_date.day == 1
        assert pf.start_date.hour == 0
        assert pf.start_date.minute == 0
        assert pf.start_date.second == 0
        # No tz applied (no tz param, no tz in the string)
        assert pf.start_date.tzinfo is None

    def test_iso_datetime_with_tz_no_tz_param_converts_to_naive_local(self):
        """Validator's 'tzinfo present, no tz param' path:

        astimezone(None) converts to system local time, then tzinfo is stripped.
        Result is naive datetime in local time.
        """
        pf = PeriodFilter(start_date="2024-08-01T09:00:00+00:00")
        # Whatever the local zone is, the result must be naive.
        assert pf.start_date is not None
        assert pf.start_date.tzinfo is None

    def test_iso_datetime_with_tz_and_tz_param_still_strips_to_local(self):
        """Validator can't see ``tz`` (pydantic field-order issue), so a
        ``tz`` param does NOT change the no-tz-param result."""
        without = PeriodFilter(start_date="2024-08-01T09:00:00+00:00")
        with_tz = PeriodFilter(start_date="2024-08-01T09:00:00+00:00", tz=ZoneInfo("Europe/London"))
        assert with_tz.start_date == without.start_date
        assert with_tz.start_date.tzinfo is None

    def test_naive_string_with_tz_param_returns_naive_due_to_known_bug(self):
        """Validator can't see ``tz`` so the localize branch never runs;
        the parsed naive datetime is returned verbatim. Also ``ZoneInfo``
        has no ``.localize`` method, so even if the branch did run it
        would AttributeError and fall through to ``return None``."""
        pf = PeriodFilter(start_date="2024-08-01T09:00:00", tz=ZoneInfo("Europe/London"))
        assert pf.start_date is not None
        assert pf.start_date.tzinfo is None
        assert pf.start_date.hour == 9

    def test_date_only_with_tz_param_returns_naive_due_to_known_bug(self):
        """Same root cause as test_naive_string_with_tz_param_returns_naive..."""
        pf = PeriodFilter(start_date="2024-08-01", tz=ZoneInfo("Europe/London"))
        assert pf.start_date is not None
        assert pf.start_date.tzinfo is None
        assert pf.start_date.hour == 0

    def test_invalid_iso_string_returns_none(self):
        pf = PeriodFilter(start_date="not a date at all")
        assert pf.start_date is None

    def test_empty_string_returns_none(self):
        pf = PeriodFilter(start_date="", end_date="")
        assert pf.start_date is None
        assert pf.end_date is None

    def test_no_input_returns_none(self):
        pf = PeriodFilter()
        assert pf.start_date is None
        assert pf.end_date is None

    def test_validator_handles_both_start_and_end(self):
        pf = PeriodFilter(start_date="2024-08-01", end_date="2024-08-31")
        assert pf.start_date is not None
        assert pf.end_date is not None
        assert pf.start_date.day == 1
        assert pf.end_date.day == 31


# ---------------------------------------------------------------------------
# PeriodFilter.contains()
# ---------------------------------------------------------------------------


class TestPeriodFilterContains:
    """Behaviour of PeriodFilter.contains(date_str)."""

    def test_empty_date_str_with_no_filter_dates_returns_true(self):
        pf = PeriodFilter()
        assert pf.contains(None) is True
        assert pf.contains("") is True

    def test_empty_date_str_with_filter_start_returns_false(self):
        pf = PeriodFilter(start_date="2024-08-01")
        assert pf.contains(None) is False
        assert pf.contains("") is False

    def test_empty_date_str_with_filter_end_returns_false(self):
        pf = PeriodFilter(end_date="2024-08-31")
        assert pf.contains(None) is False

    def test_task_date_within_range_returns_true(self):
        pf = PeriodFilter(start_date="2024-08-01", end_date="2024-08-31")
        assert pf.contains("2024-08-15") is True

    def test_task_date_before_start_returns_false(self):
        pf = PeriodFilter(start_date="2024-08-01", end_date="2024-08-31")
        assert pf.contains("2024-07-15") is False

    def test_task_date_after_end_returns_false(self):
        pf = PeriodFilter(start_date="2024-08-01", end_date="2024-08-31")
        assert pf.contains("2024-09-15") is False

    def test_task_date_on_exact_start_returns_true(self):
        """Range comparison is inclusive on both ends (date granularity)."""
        pf = PeriodFilter(start_date="2024-08-01", end_date="2024-08-31")
        assert pf.contains("2024-08-01") is True

    def test_task_date_on_exact_end_returns_true(self):
        pf = PeriodFilter(start_date="2024-08-01", end_date="2024-08-31")
        assert pf.contains("2024-08-31") is True

    def test_only_start_set_checks_lower_bound_only(self):
        pf = PeriodFilter(start_date="2024-08-01")
        assert pf.contains("2024-08-15") is True
        assert pf.contains("2099-12-31") is True  # no upper bound
        assert pf.contains("2024-07-15") is False

    def test_only_end_set_checks_upper_bound_only(self):
        pf = PeriodFilter(end_date="2024-08-31")
        assert pf.contains("2024-08-15") is True
        assert pf.contains("1999-01-01") is True  # no lower bound
        assert pf.contains("2024-09-15") is False

    def test_unparseable_task_date_with_filter_set_returns_false(self):
        """When the task date is junk, _parse_task_date returns None and
        contains() falls back to 'not (start or end)' - which is False here.
        """
        pf = PeriodFilter(start_date="2024-08-01", end_date="2024-08-31")
        assert pf.contains("garbage-string") is False

    def test_unparseable_task_date_with_no_filter_returns_true(self):
        pf = PeriodFilter()
        assert pf.contains("garbage-string") is True


# ---------------------------------------------------------------------------
# PeriodFilter._parse_task_date()
# ---------------------------------------------------------------------------


class TestPeriodFilterParseTaskDate:
    """Behaviour of the internal _parse_task_date helper."""

    def test_iso_with_T_and_Z_suffix_parses_as_utc(self):
        pf = PeriodFilter()
        # No filter tz - dt is converted to local naive
        dt = pf._parse_task_date("2024-08-01T09:00:00Z")
        assert dt is not None
        # tzinfo stripped on the "no filter tz, task has tz" path
        assert dt.tzinfo is None

    def test_iso_with_offset_parses(self):
        pf = PeriodFilter()
        dt = pf._parse_task_date("2024-08-01T09:00:00+01:00")
        assert dt is not None
        assert dt.tzinfo is None  # stripped to local

    def test_iso_with_compact_offset_parses(self):
        pf = PeriodFilter()
        dt = pf._parse_task_date("2024-08-01T09:00:00+0100")
        assert dt is not None

    def test_iso_with_milliseconds_handled(self):
        """TickTick emits '.000' millisecond suffix; the helper strips it."""
        pf = PeriodFilter()
        dt = pf._parse_task_date("2024-08-01T09:00:00.000+0000")
        assert dt is not None

    def test_date_only_string_parses_to_start_of_day(self):
        pf = PeriodFilter()
        dt = pf._parse_task_date("2024-08-01")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 8
        assert dt.day == 1
        assert dt.hour == 0
        assert dt.tzinfo is None

    def test_unparseable_returns_none(self):
        pf = PeriodFilter()
        assert pf._parse_task_date("not-a-date") is None

    def test_filter_tz_applied_to_naive_task_triggers_localize_bug(self):
        """When the task date is naive and the filter has a tz, the code
        calls ``self.tz.localize(dt)``. ``ZoneInfo`` has no ``.localize``
        method so AttributeError fires, the bare ``except`` swallows it,
        and the helper returns ``None``."""
        pf = PeriodFilter(tz=ZoneInfo("Europe/London"))
        # Date-only also produces a naive datetime, so same bug path:
        assert pf._parse_task_date("2024-08-01") is None
        # And naive datetime string:
        assert pf._parse_task_date("2024-08-01T09:00:00") is None

    def test_task_tz_converted_to_filter_tz_when_both_set(self):
        """The astimezone(self.tz) path works (no .localize call)."""
        pf = PeriodFilter(tz=ZoneInfo("Europe/London"))
        dt = pf._parse_task_date("2024-08-01T09:00:00+00:00")
        assert dt is not None
        assert dt.tzinfo is not None
        # 09:00 UTC -> 10:00 BST (August)
        assert dt.hour == 10

    def test_no_filter_tz_task_has_tz_strips_to_local_naive(self):
        pf = PeriodFilter()
        dt = pf._parse_task_date("2024-08-01T09:00:00+00:00")
        assert dt is not None
        assert dt.tzinfo is None  # stripped


# ---------------------------------------------------------------------------
# PropertyFilter.matches()
# ---------------------------------------------------------------------------


class TestPropertyFilterMatches:
    """Behaviour of PropertyFilter.matches(task)."""

    def test_tag_label_match(self):
        pf = PropertyFilter(tag_label="work", status="uncompleted")
        assert pf.matches({"status": 0, "tags": ["work", "urgent"]}) is True

    def test_tag_label_mismatch(self):
        pf = PropertyFilter(tag_label="work", status="uncompleted")
        assert pf.matches({"status": 0, "tags": ["personal"]}) is False

    def test_tag_label_missing_field(self):
        """Task with no 'tags' key: tag filter treats it as empty list."""
        pf = PropertyFilter(tag_label="work", status="uncompleted")
        assert pf.matches({"status": 0}) is False

    def test_tag_label_none_matches_anything(self):
        pf = PropertyFilter(status="uncompleted")  # tag_label defaults to None
        assert pf.matches({"status": 0, "tags": ["anything"]}) is True
        assert pf.matches({"status": 0}) is True

    def test_project_id_match(self):
        pf = PropertyFilter(project_id="p1", status="uncompleted")
        assert pf.matches({"status": 0, "projectId": "p1"}) is True

    def test_project_id_mismatch(self):
        pf = PropertyFilter(project_id="p1", status="uncompleted")
        assert pf.matches({"status": 0, "projectId": "p2"}) is False

    def test_project_id_missing_field(self):
        pf = PropertyFilter(project_id="p1", status="uncompleted")
        assert pf.matches({"status": 0}) is False

    def test_priority_match(self):
        pf = PropertyFilter(priority=5, status="uncompleted")
        assert pf.matches({"status": 0, "priority": 5}) is True

    def test_priority_mismatch(self):
        pf = PropertyFilter(priority=5, status="uncompleted")
        assert pf.matches({"status": 0, "priority": 3}) is False

    def test_priority_zero_match(self):
        """priority=0 is a real value (None priority in TickTick)."""
        pf = PropertyFilter(priority=0, status="uncompleted")
        assert pf.matches({"status": 0, "priority": 0}) is True

    def test_priority_none_filter_matches_anything(self):
        pf = PropertyFilter(status="uncompleted")  # priority None
        assert pf.matches({"status": 0, "priority": 5}) is True
        assert pf.matches({"status": 0}) is True

    def test_status_filter_uncompleted_task_uncompleted(self):
        pf = PropertyFilter(status="uncompleted")
        assert pf.matches({"status": 0}) is True

    def test_status_filter_uncompleted_task_completed(self):
        pf = PropertyFilter(status="uncompleted")
        assert pf.matches({"status": 2}) is False

    def test_status_filter_completed_task_completed(self):
        pf = PropertyFilter(status="completed")
        assert pf.matches({"status": 2}) is True

    def test_status_filter_completed_task_uncompleted(self):
        pf = PropertyFilter(status="completed")
        assert pf.matches({"status": 0}) is False

    def test_task_missing_status_defaults_to_uncompleted(self):
        pf = PropertyFilter(status="uncompleted")
        assert pf.matches({"id": "no_status_field"}) is True

    def test_due_date_filter_applied_to_uncompleted(self):
        pf = PropertyFilter(
            status="uncompleted",
            due_date_filter=PeriodFilter(start_date="2024-08-01", end_date="2024-08-31"),
        )
        assert pf.matches({"status": 0, "dueDate": "2024-08-15T10:00:00+0000"}) is True
        assert pf.matches({"status": 0, "dueDate": "2024-09-15T10:00:00+0000"}) is False

    def test_due_date_filter_excludes_uncompleted_with_no_due_date(self):
        """When the filter has bounds and the task has no dueDate, contains()
        returns False (it's an exclusion, not a match)."""
        pf = PropertyFilter(
            status="uncompleted",
            due_date_filter=PeriodFilter(start_date="2024-08-01"),
        )
        assert pf.matches({"status": 0}) is False

    def test_due_date_filter_ignored_for_completed(self):
        pf = PropertyFilter(
            status="completed",
            due_date_filter=PeriodFilter(start_date="2024-08-01", end_date="2024-08-31"),
        )
        # A completed task with dueDate well outside the range still matches
        assert pf.matches({"status": 2, "dueDate": "2099-12-31T00:00:00+0000"}) is True

    def test_completion_date_filter_applied_to_completed(self):
        pf = PropertyFilter(
            status="completed",
            completion_date_filter=PeriodFilter(start_date="2024-08-01", end_date="2024-08-31"),
        )
        assert pf.matches({"status": 2, "completedTime": "2024-08-15T10:00:00+0000"}) is True
        assert pf.matches({"status": 2, "completedTime": "2024-09-15T10:00:00+0000"}) is False

    def test_completion_date_filter_ignored_for_uncompleted(self):
        pf = PropertyFilter(
            status="uncompleted",
            completion_date_filter=PeriodFilter(start_date="2024-08-01", end_date="2024-08-31"),
        )
        # Uncompleted task with bogus completedTime still matches
        assert pf.matches({"status": 0, "completedTime": "2099-12-31T00:00:00+0000"}) is True

    def test_all_filters_pass(self):
        pf = PropertyFilter(status="uncompleted", project_id="p1", tag_label="work", priority=5)
        assert (
            pf.matches(
                {
                    "status": 0,
                    "projectId": "p1",
                    "tags": ["work"],
                    "priority": 5,
                }
            )
            is True
        )

    def test_one_filter_fails_whole_returns_false(self):
        pf = PropertyFilter(status="uncompleted", project_id="p1", tag_label="work", priority=5)
        # Same task as above but priority differs:
        assert (
            pf.matches(
                {
                    "status": 0,
                    "projectId": "p1",
                    "tags": ["work"],
                    "priority": 3,
                }
            )
            is False
        )


# ---------------------------------------------------------------------------
# TaskFilterer._fetch_tasks_by_status()
# ---------------------------------------------------------------------------


class TestFetchTasksByStatus:
    """Behaviour of TaskFilterer._fetch_tasks_by_status."""

    def test_completed_no_filter_returns_empty(self):
        filterer = TaskFilterer()
        result = run(filterer._fetch_tasks_by_status("completed", None, None))
        assert result == []

    def test_completed_empty_filter_returns_empty(self):
        """An empty PeriodFilter (no dates) is treated like no filter."""
        filterer = TaskFilterer()
        result = run(filterer._fetch_tasks_by_status("completed", PeriodFilter(), None))
        assert result == []

    def test_completed_with_start_calls_api_with_datetimes(self):
        mock_client = MagicMock()
        mock_client.task.get_completed = MagicMock(return_value=[])

        filterer = TaskFilterer()
        pf = PeriodFilter(start_date="2024-08-01")
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(filterer._fetch_tasks_by_status("completed", pf, None))

        call_kwargs = mock_client.task.get_completed.call_args.kwargs
        assert call_kwargs["start"] is not None
        assert call_kwargs["end"] is None
        # start passed as a datetime
        assert call_kwargs["start"].year == 2024
        assert call_kwargs["start"].month == 8

    def test_completed_with_end_calls_api_with_datetimes(self):
        mock_client = MagicMock()
        mock_client.task.get_completed = MagicMock(return_value=[])

        filterer = TaskFilterer()
        pf = PeriodFilter(end_date="2024-08-31")
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(filterer._fetch_tasks_by_status("completed", pf, None))

        call_kwargs = mock_client.task.get_completed.call_args.kwargs
        assert call_kwargs["start"] is None
        assert call_kwargs["end"] is not None
        assert call_kwargs["end"].day == 31

    def test_completed_reapplies_period_filter_precision(self):
        """API may filter only by day; the code re-applies the filter to
        trim out anything outside the precise window."""
        mock_client = MagicMock()
        mock_client.task.get_completed = MagicMock(
            return_value=[
                {"id": "in_window", "status": 2, "completedTime": "2024-08-15T10:00:00+0000"},
                {"id": "outside", "status": 2, "completedTime": "2024-09-15T10:00:00+0000"},
            ]
        )

        filterer = TaskFilterer()
        pf = PeriodFilter(start_date="2024-08-01", end_date="2024-08-31")
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(filterer._fetch_tasks_by_status("completed", pf, None))

        assert len(result) == 1
        assert result[0]["id"] == "in_window"

    def test_completed_api_exception_raises_connection_error(self):
        mock_client = MagicMock()
        mock_client.task.get_completed = MagicMock(side_effect=Exception("API kaboom"))

        filterer = TaskFilterer()
        pf = PeriodFilter(start_date="2024-08-01")
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            with pytest.raises(ConnectionError) as exc_info:
                run(filterer._fetch_tasks_by_status("completed", pf, None))
        assert "Failed to fetch completed tasks" in str(exc_info.value)

    def test_completed_no_client_raises_connection_error(self):
        """No client inside the try: raises ConnectionError, which gets
        re-wrapped by the broad except into another ConnectionError."""
        filterer = TaskFilterer()
        pf = PeriodFilter(start_date="2024-08-01")
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=None,
        ):
            with pytest.raises(ConnectionError):
                run(filterer._fetch_tasks_by_status("completed", pf, None))

    def test_uncompleted_calls_get_all_tasks_from_ticktick(self):
        filterer = TaskFilterer()
        fake_tasks = [{"id": "u1", "status": 0}, {"id": "u2", "status": 0}]
        with patch(
            "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
            return_value=fake_tasks,
        ) as mock_helper:
            result = run(filterer._fetch_tasks_by_status("uncompleted", None, None))

        mock_helper.assert_called_once()
        assert result == fake_tasks


# ---------------------------------------------------------------------------
# TaskFilterer.filter()
# ---------------------------------------------------------------------------


class TestTaskFiltererFilter:
    """Behaviour of TaskFilterer.filter (the top-level orchestrator)."""

    def test_orchestrates_fetch_and_property_filter(self):
        filterer = TaskFilterer()
        fake_tasks = [
            {"id": "match", "status": 0, "tags": ["work"]},
            {"id": "tag_miss", "status": 0, "tags": ["home"]},
        ]
        pf = PropertyFilter(status="uncompleted", tag_label="work")
        with patch(
            "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
            return_value=fake_tasks,
        ):
            result = run(filterer.filter(property_filter=pf, sort_by_priority=False, tz_info=None))

        assert len(result) == 1
        assert result[0]["id"] == "match"

    def test_sort_by_priority_descending(self):
        filterer = TaskFilterer()
        fake_tasks = [
            {"id": "low", "status": 0, "priority": 1},
            {"id": "high", "status": 0, "priority": 5},
            {"id": "med", "status": 0, "priority": 3},
        ]
        pf = PropertyFilter(status="uncompleted")
        with patch(
            "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
            return_value=fake_tasks,
        ):
            result = run(filterer.filter(property_filter=pf, sort_by_priority=True, tz_info=None))

        assert [t["id"] for t in result] == ["high", "med", "low"]

    def test_sort_by_priority_false_preserves_order(self):
        filterer = TaskFilterer()
        fake_tasks = [
            {"id": "low", "status": 0, "priority": 1},
            {"id": "high", "status": 0, "priority": 5},
            {"id": "med", "status": 0, "priority": 3},
        ]
        pf = PropertyFilter(status="uncompleted")
        with patch(
            "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
            return_value=fake_tasks,
        ):
            result = run(filterer.filter(property_filter=pf, sort_by_priority=False, tz_info=None))

        assert [t["id"] for t in result] == ["low", "high", "med"]

    def test_sort_treats_missing_priority_as_zero(self):
        filterer = TaskFilterer()
        fake_tasks = [
            {"id": "high", "status": 0, "priority": 5},
            {"id": "no_priority", "status": 0},
            {"id": "med", "status": 0, "priority": 3},
        ]
        pf = PropertyFilter(status="uncompleted")
        with patch(
            "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
            return_value=fake_tasks,
        ):
            result = run(filterer.filter(property_filter=pf, sort_by_priority=True, tz_info=None))

        assert result[0]["id"] == "high"
        assert result[-1]["id"] == "no_priority"

    def test_completed_path_passes_completion_filter_only(self):
        """For status=completed, the filter passes completion_date_filter
        (not due_date_filter) to the fetcher."""
        filterer = TaskFilterer()
        mock_client = MagicMock()
        mock_client.task.get_completed = MagicMock(
            return_value=[
                {"id": "c1", "status": 2, "completedTime": "2024-08-15T10:00:00+0000"},
            ]
        )
        pf = PropertyFilter(
            status="completed",
            completion_date_filter=PeriodFilter(start_date="2024-08-01", end_date="2024-08-31"),
        )
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(filterer.filter(property_filter=pf, sort_by_priority=False, tz_info=None))

        assert len(result) == 1
        assert result[0]["id"] == "c1"


# ---------------------------------------------------------------------------
# _build_property_filter()
# ---------------------------------------------------------------------------


class TestBuildPropertyFilter:
    """Behaviour of _build_property_filter (criteria -> filter objects)."""

    def test_accepts_dict_input(self):
        pf, tz, sort = _build_property_filter(
            {
                "status": "uncompleted",
                "tag_label": "work",
                "priority": 5,
            }
        )
        assert pf.status == "uncompleted"
        assert pf.tag_label == "work"
        assert pf.priority == 5
        assert sort is False

    def test_accepts_json_string_input(self):
        pf, tz, sort = _build_property_filter('{"status": "completed", "project_id": "p1"}')
        assert pf.status == "completed"
        assert pf.project_id == "p1"

    def test_invalid_json_string_raises_value_error(self):
        with pytest.raises(ValueError) as exc_info:
            _build_property_filter("not json{")
        assert "Invalid JSON string" in str(exc_info.value)

    def test_invalid_status_raises_value_error(self):
        with pytest.raises(ValueError) as exc_info:
            _build_property_filter({"status": "bogus"})
        assert "Invalid status" in str(exc_info.value)

    def test_non_dict_non_str_raises_value_error(self):
        with pytest.raises(ValueError):
            _build_property_filter(123)
        with pytest.raises(ValueError):
            _build_property_filter(None)

    def test_status_defaults_to_uncompleted(self):
        pf, tz, sort = _build_property_filter({})
        assert pf.status == "uncompleted"

    def test_sort_by_priority_defaults_to_false(self):
        _, _, sort = _build_property_filter({})
        assert sort is False

    def test_sort_by_priority_passed_through(self):
        _, _, sort = _build_property_filter({"sort_by_priority": True})
        assert sort is True

    def test_valid_tz_string_yields_zoneinfo(self):
        pf, tz, _ = _build_property_filter({"tz": "Europe/London"})
        assert isinstance(tz, ZoneInfo)
        assert str(tz) == "Europe/London"
        # The PeriodFilters also receive the same tz
        assert pf.due_date_filter.tz is not None
        assert str(pf.due_date_filter.tz) == "Europe/London"
        assert pf.completion_date_filter.tz is not None

    def test_invalid_tz_string_logs_warning_returns_none(self):
        """Invalid tz does not raise; tz_info stays None."""
        pf, tz, _ = _build_property_filter({"tz": "Not/A/Real/Zone"})
        assert tz is None
        assert pf.due_date_filter.tz is None
        assert pf.completion_date_filter.tz is None

    def test_due_dates_built_into_period_filter(self):
        pf, _, _ = _build_property_filter(
            {
                "due_start_date": "2024-08-01",
                "due_end_date": "2024-08-31",
            }
        )
        assert pf.due_date_filter.start_date is not None
        assert pf.due_date_filter.end_date is not None
        assert pf.due_date_filter.start_date.day == 1
        assert pf.due_date_filter.end_date.day == 31

    def test_completion_dates_built_into_period_filter(self):
        pf, _, _ = _build_property_filter(
            {
                "status": "completed",
                "completion_start_date": "2024-08-01",
                "completion_end_date": "2024-08-31",
            }
        )
        assert pf.completion_date_filter.start_date is not None
        assert pf.completion_date_filter.end_date is not None

    def test_all_fields_passed_through(self):
        pf, tz, sort = _build_property_filter(
            {
                "status": "uncompleted",
                "project_id": "p1",
                "tag_label": "work",
                "priority": 3,
                "due_start_date": "2024-08-01",
                "due_end_date": "2024-08-31",
                "tz": "America/New_York",
                "sort_by_priority": True,
            }
        )
        assert pf.status == "uncompleted"
        assert pf.project_id == "p1"
        assert pf.tag_label == "work"
        assert pf.priority == 3
        assert isinstance(tz, ZoneInfo)
        assert sort is True


# ---------------------------------------------------------------------------
# ticktick_filter_tasks() - the MCP entry point
# ---------------------------------------------------------------------------


class TestTickTickFilterTasksEntryPoint:
    """Behaviour of the @mcp.tool decorated entry point."""

    def test_uncompleted_success_returns_json_list(self):
        mock_client = MagicMock()
        fake_tasks = [
            {"id": "t1", "title": "T1", "status": 0, "priority": 5},
            {"id": "t2", "title": "T2", "status": 0, "priority": 1},
        ]
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=mock_client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=fake_tasks,
            ),
        ):
            result = run(ticktick_filter_tasks({"status": "uncompleted"}))

        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert {t["id"] for t in parsed} == {"t1", "t2"}

    def test_invalid_status_returns_json_error_dict(self):
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=MagicMock(),
        ):
            result = run(ticktick_filter_tasks({"status": "bogus"}))

        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert parsed["status"] == "error"
        assert "Invalid status" in parsed["error"]

    def test_invalid_json_string_input_returns_json_error_dict(self):
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=MagicMock(),
        ):
            result = run(ticktick_filter_tasks("not json{"))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Invalid JSON" in parsed["error"]

    def test_connection_error_returns_json_error_dict(self):
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=MagicMock(),
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                side_effect=ConnectionError("client gone"),
            ),
        ):
            result = run(ticktick_filter_tasks({"status": "uncompleted"}))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "client gone" in parsed["error"]

    def test_unexpected_exception_returns_json_error_dict(self):
        """A non-ValueError, non-ConnectionError exception is caught by the
        outermost except and wrapped with 'unexpected error' prefix."""
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=MagicMock(),
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                side_effect=RuntimeError("something weird"),
            ),
        ):
            result = run(ticktick_filter_tasks({"status": "uncompleted"}))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "unexpected error" in parsed["error"].lower()

    def test_sort_by_priority_flag_honoured(self):
        mock_client = MagicMock()
        fake_tasks = [
            {"id": "low", "status": 0, "priority": 1},
            {"id": "high", "status": 0, "priority": 5},
            {"id": "med", "status": 0, "priority": 3},
        ]
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=mock_client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=fake_tasks,
            ),
        ):
            result = run(
                ticktick_filter_tasks(
                    {
                        "status": "uncompleted",
                        "sort_by_priority": True,
                    }
                )
            )

        parsed = json.loads(result)
        assert [t["id"] for t in parsed] == ["high", "med", "low"]

    def test_completed_no_dates_returns_empty_list(self):
        """Completed + no date filter -> _fetch returns [], result is []."""
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=MagicMock(),
        ):
            result = run(ticktick_filter_tasks({"status": "completed"}))

        parsed = json.loads(result)
        assert parsed == []

    def test_completed_with_dates_calls_get_completed(self):
        mock_client = MagicMock()
        mock_client.task.get_completed = MagicMock(
            return_value=[
                {"id": "c1", "status": 2, "completedTime": "2024-08-15T10:00:00+0000"},
            ]
        )
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(
                ticktick_filter_tasks(
                    {
                        "status": "completed",
                        "completion_start_date": "2024-08-01",
                        "completion_end_date": "2024-08-31",
                    }
                )
            )

        mock_client.task.get_completed.assert_called_once()
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["id"] == "c1"

    def test_json_string_filter_criteria_accepted(self):
        mock_client = MagicMock()
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=mock_client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[{"id": "t1", "status": 0}],
            ),
        ):
            result = run(ticktick_filter_tasks('{"status": "uncompleted"}'))

        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
