"""Tests for recurring-aware completion in ticktick_complete_task.

A recurring task rolls forward on completion (same id reappears as the next
occurrence with status 0), which the old code misreported as
"status still indicates open". These tests pin the recurring-aware handling
and confirm the non-recurring path is unchanged.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.tools.task_tools import _is_recurring, ticktick_complete_task


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def mock_client():
    return MagicMock()


class TestIsRecurring:
    def test_repeat_flag_rrule(self):
        assert _is_recurring({"repeatFlag": "RRULE:FREQ=DAILY;INTERVAL=1"}) is True

    def test_repeat_from_completion_mode_empty_flag(self):
        # The incident shape: repeatFrom set, repeatFlag empty.
        assert _is_recurring({"repeatFrom": "2", "repeatFlag": ""}) is True

    def test_repeat_task_id(self):
        assert _is_recurring({"repeatTaskId": "abc"}) is True

    def test_repeat_first_date(self):
        assert _is_recurring({"repeatFirstDate": "2026-07-10T23:00:00.000+0000"}) is True

    def test_repeat_from_zero_is_not_recurring(self):
        assert _is_recurring({"repeatFrom": "0"}) is False

    def test_plain_task_not_recurring(self):
        assert _is_recurring({"id": "t1", "projectId": "p1", "status": 0}) is False

    def test_non_dict_returns_false(self):
        assert _is_recurring(None) is False
        assert _is_recurring("not a dict") is False


class TestCompleteRecurring:
    def test_rolled_forward_reports_success_not_warning(self, mock_client):
        """Recurring task that rolls forward -> success with next-occurrence info."""
        task_obj = {
            "id": "r1",
            "projectId": "p1",
            "status": 0,
            "content": "Feedback: done",
            "repeatFlag": "RRULE:FREQ=DAILY;INTERVAL=1",
            "repeatFrom": "2",
            "dueDate": "2026-05-31T08:00:00.000+0000",
        }
        next_occurrence = dict(task_obj, dueDate="2026-06-01T08:00:00.000+0000")
        mock_client.get_by_id = MagicMock(side_effect=[task_obj, next_occurrence])
        mock_client.task.complete = MagicMock(return_value=task_obj)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_complete_task(task_id="r1"))

        parsed = json.loads(result)
        assert parsed["outcome"] == "completed_recurring"
        assert parsed["next_occurrence_id"] == "r1"
        # No false "status still indicates open" warning.
        assert "_verification_warnings" not in parsed

    def test_completed_in_place_is_success(self, mock_client):
        """Recurring task with no live rule completes in place (refetch empty) -> success."""
        task_obj = {
            "id": "r2",
            "projectId": "p1",
            "status": 0,
            "content": "Feedback: done",
            "repeatFrom": "2",
            "repeatFlag": "",
        }
        mock_client.get_by_id = MagicMock(side_effect=[task_obj, {}])
        mock_client.task.complete = MagicMock(return_value=task_obj)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_complete_task(task_id="r2"))

        parsed = json.loads(result)
        assert parsed["outcome"] == "completed"
        # Documented success signal preserved for the left-the-active-list case.
        assert "_verification_warnings" in parsed

    def test_non_recurring_status_zero_still_warns(self, mock_client):
        """A NON-recurring task left at status 0 keeps the open-status warning."""
        task_obj = {"id": "t1", "projectId": "p1", "status": 0}
        mock_client.get_by_id = MagicMock(side_effect=[task_obj, task_obj])
        mock_client.task.complete = MagicMock(return_value={"some": "resp"})

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_complete_task(task_id="t1"))

        parsed = json.loads(result)
        assert parsed.get("outcome") != "completed_recurring"
        assert "_verification_warnings" in parsed
