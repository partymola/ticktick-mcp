"""Tests for the update_task empty-response ("didn't take") detector.

When the API echoes an empty response, update_task re-reads to determine
whether the change actually applied, and returns an actionable outcome
instead of a {"result": ""} that reads as success.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.tools.task_tools import update_task


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def mock_client():
    return MagicMock()


class TestUpdateEmptyResponse:
    def test_empty_response_and_change_did_not_apply_reports_no_op(self, mock_client):
        """Reopen (status:0) that the API silently ignores -> outcome no_op, actionable."""
        completed = {"id": "t1", "projectId": "p1", "status": 2, "title": "Task"}
        # pre-read returns the completed task; re-read after empty response
        # still shows status 2 -> the reopen did not take.
        mock_client.get_by_id = MagicMock(side_effect=[completed, completed])
        mock_client.task.update = MagicMock(return_value="")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object={"id": "t1", "projectId": "p1", "status": 0}))

        parsed = json.loads(result)
        assert parsed["outcome"] == "no_op"
        assert "did not apply" in parsed["error"]
        assert "ticktick_get_by_id" in parsed["error"]

    def test_empty_response_but_change_applied_reports_updated(self, mock_client):
        """Empty echo but the re-read confirms the change took -> outcome updated."""
        completed = {"id": "t1", "projectId": "p1", "status": 2, "title": "Task"}
        reopened = {"id": "t1", "projectId": "p1", "status": 0, "title": "Task"}
        mock_client.get_by_id = MagicMock(side_effect=[completed, reopened])
        mock_client.task.update = MagicMock(return_value="")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object={"id": "t1", "projectId": "p1", "status": 0}))

        parsed = json.loads(result)
        assert parsed["outcome"] == "updated"
        assert parsed["status"] == 0

    def test_normal_dict_response_unchanged(self, mock_client):
        """A normal (non-empty dict) update response keeps the existing shape."""
        existing = {"id": "t1", "projectId": "p1", "status": 0, "title": "Task", "priority": 0}
        mock_client.get_by_id = MagicMock(return_value=existing)
        mock_client.task.update = MagicMock(side_effect=lambda d: d)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object={"id": "t1", "projectId": "p1", "priority": 5}))

        parsed = json.loads(result)
        # No no_op / didn't-apply path; returns the updated task.
        assert parsed.get("outcome") != "no_op"
        assert parsed["priority"] == 5


class TestEmptyPreReadNeedsProjectId:
    """An id not in local sync state (get_by_id -> {}) with no projectId is a
    dead end: the projectId-less POST always no-ops. Catch it before POSTing
    and name the action that works (supply projectId) instead of retry advice.
    """

    def test_empty_preread_no_projectid_returns_needs_project_id(self, mock_client):
        """Completed-history / unknown id, no projectId -> needs_project_id, no POST."""
        mock_client.get_by_id = MagicMock(return_value={})
        mock_client.task.update = MagicMock(return_value="")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object={"id": "hist1", "status": 0}))

        parsed = json.loads(result)
        assert parsed["outcome"] == "needs_project_id"
        assert "projectId" in parsed["error"]
        # The futile POST is skipped entirely.
        mock_client.task.update.assert_not_called()

    def test_empty_preread_with_projectid_falls_through_and_reopens(self, mock_client):
        """Same empty pre-read but projectId supplied -> routable reopen succeeds."""
        reopened = {"id": "hist1", "projectId": "p1", "status": 0, "title": "Task"}
        mock_client.get_by_id = MagicMock(return_value={})
        mock_client.task.update = MagicMock(return_value=reopened)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object={"id": "hist1", "projectId": "p1", "status": 0}))

        parsed = json.loads(result)
        assert parsed.get("outcome") not in ("needs_project_id", "no_op")
        assert parsed["status"] == 0
        # The routable body was actually POSTed.
        mock_client.task.update.assert_called_once()


class TestNoOpMessageHasNoDeadEndAdvice:
    def test_generic_no_op_message_drops_recurring_retry_parenthetical(self, mock_client):
        """The generic no_op error must not re-introduce the un-retryable
        'common when reopening a completed recurring occurrence' advice."""
        completed = {"id": "t1", "projectId": "p1", "status": 2, "title": "Task"}
        mock_client.get_by_id = MagicMock(side_effect=[completed, completed])
        mock_client.task.update = MagicMock(return_value="")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object={"id": "t1", "projectId": "p1", "status": 0}))

        parsed = json.loads(result)
        assert parsed["outcome"] == "no_op"
        assert "did not apply" in parsed["error"]
        assert "ticktick_get_by_id" in parsed["error"]
        assert "common when reopening" not in parsed["error"]


class TestRecurringReopenNoEffect:
    """Reopening a rolled-forward recurring task by its series id via a bare
    status:0 is a silent false-success: it changes nothing and does not undo
    the completion. Refuse with an explanation, but never block a real edit.
    """

    def _recurring_open(self):
        return {
            "id": "series1",
            "projectId": "p1",
            "title": "Daily standup",
            "status": 0,
            "repeatFlag": "RRULE:FREQ=DAILY;INTERVAL=1",
            "repeatFrom": "2",
            "dueDate": "2026-06-01T11:00:00.000+0000",
        }

    def test_bare_status0_on_recurring_series_id_is_refused(self, mock_client):
        mock_client.get_by_id = MagicMock(return_value=self._recurring_open())

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object={"id": "series1", "status": 0}))

        parsed = json.loads(result)
        assert parsed["outcome"] == "reopen_no_effect"
        assert parsed["status"] == "error"
        assert "does NOT undo" in parsed["error"]
        # No futile / misleading POST is issued.
        mock_client.task.update.assert_not_called()

    def test_status0_with_another_field_change_proceeds(self, mock_client):
        """A real edit that happens to carry status:0 must NOT be refused."""
        mock_client.get_by_id = MagicMock(return_value=self._recurring_open())
        mock_client.task.update = MagicMock(side_effect=lambda d: d)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object={"id": "series1", "status": 0, "priority": 5}))

        parsed = json.loads(result)
        assert parsed.get("outcome") != "reopen_no_effect"
        assert parsed["priority"] == 5
        mock_client.task.update.assert_called_once()

    def test_bare_status0_on_nonrecurring_open_task_proceeds(self, mock_client):
        """The guard is recurring-only; non-recurring tasks are unaffected."""
        existing = {"id": "t9", "projectId": "p1", "title": "One-off", "status": 0}
        mock_client.get_by_id = MagicMock(return_value=existing)
        mock_client.task.update = MagicMock(side_effect=lambda d: d)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object={"id": "t9", "status": 0}))

        parsed = json.loads(result)
        assert parsed.get("outcome") != "reopen_no_effect"
        mock_client.task.update.assert_called_once()
