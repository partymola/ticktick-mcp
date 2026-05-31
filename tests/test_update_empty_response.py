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
