"""Tests for update_task fix: partial updates should preserve unmodified fields."""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch

from ticktick_mcp.tools.task_tools import update_task, TaskObject


def run(coro):
    """Helper to run an async function synchronously."""
    return asyncio.run(coro)


# --- Fixtures ---

@pytest.fixture
def mock_client():
    """Create a mock TickTick client with task.update method."""
    client = MagicMock()
    # task.update returns whatever was passed to it (simulates API echo)
    client.task.update = MagicMock(side_effect=lambda d: d)
    return client


@pytest.fixture
def existing_task_with_dates():
    """Simulate a task returned by client.get_by_id with dates, reminders, priority."""
    return {
        "id": "task123",
        "projectId": "proj456",
        "title": "Buy groceries",
        "content": "Milk, eggs, bread",
        "priority": 3,
        "startDate": "2024-08-01T09:00:00+0000",
        "dueDate": "2024-08-01T17:00:00+0000",
        "timeZone": "Europe/London",
        "reminders": [
            {"id": "rem1", "trigger": "TRIGGER:-PT30M"},
            {"id": "rem2", "trigger": "TRIGGER:PT0S"},
        ],
        "status": 0,
        "tags": ["shopping"],
        # Read-only fields that the API returns but should be filtered out
        "creator": "user@example.com",
        "deleted": 0,
        "kind": "TEXT",
        "isFloating": False,
        "etag": "abc123etag",
        "createdTime": "2024-07-01T10:00:00+0000",
        "modifiedTime": "2024-07-15T10:00:00+0000",
    }


# --- Tests: Partial updates preserve existing fields ---

class TestUpdateTaskPartialUpdate:

    def test_update_only_priority_preserves_dates_and_reminders(
        self, mock_client, existing_task_with_dates
    ):
        """Updating only priority should not wipe dates or reminders."""
        mock_client.get_by_id = MagicMock(return_value=existing_task_with_dates.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", priority=5)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]

        assert sent_to_api["priority"] == 5
        assert sent_to_api["startDate"] == "2024-08-01T09:00:00+0000"
        assert sent_to_api["dueDate"] == "2024-08-01T17:00:00+0000"
        assert sent_to_api["title"] == "Buy groceries"
        assert sent_to_api["tags"] == ["shopping"]

    def test_update_only_dates_preserves_priority(
        self, mock_client, existing_task_with_dates
    ):
        """Updating only dates should not reset priority to 0."""
        mock_client.get_by_id = MagicMock(return_value=existing_task_with_dates.copy())

        task_obj = TaskObject(
            id="task123",
            projectId="proj456",
            dueDate="2024-09-01T17:00:00+00:00",
            expectedDayOfWeek="Sunday",
        )

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]

        # Priority should be preserved (not reset to default 0)
        assert sent_to_api["priority"] == 3
        assert sent_to_api["title"] == "Buy groceries"


# --- Tests: Reminder normalization ---

class TestUpdateTaskReminderNormalization:

    def test_reminders_object_format_normalized_to_strings(
        self, mock_client, existing_task_with_dates
    ):
        """Reminders returned as [{id, trigger}] objects should become ["TRIGGER:..."] strings."""
        mock_client.get_by_id = MagicMock(return_value=existing_task_with_dates.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", title="Updated title")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]
        assert sent_to_api["reminders"] == ["TRIGGER:-PT30M", "TRIGGER:PT0S"]

    def test_reminders_already_strings_left_unchanged(self, mock_client):
        """Reminders that are already strings should pass through unchanged."""
        existing = {
            "id": "task123",
            "projectId": "proj456",
            "title": "Task",
            "priority": 1,
            "reminders": ["TRIGGER:-PT15M"],
        }
        mock_client.get_by_id = MagicMock(return_value=existing.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", title="New title")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]
        assert sent_to_api["reminders"] == ["TRIGGER:-PT15M"]

    def test_reminders_mixed_format_normalized(self, mock_client):
        """A mix of object and string reminders should all be normalized."""
        existing = {
            "id": "task123",
            "projectId": "proj456",
            "title": "Task",
            "reminders": [
                {"id": "r1", "trigger": "TRIGGER:-PT1H"},
                "TRIGGER:PT0S",
            ],
        }
        mock_client.get_by_id = MagicMock(return_value=existing.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", title="New title")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]
        assert sent_to_api["reminders"] == ["TRIGGER:-PT1H", "TRIGGER:PT0S"]


# --- Tests: Read-only field filtering ---

class TestUpdateTaskFieldFiltering:

    def test_readonly_fields_filtered_out(
        self, mock_client, existing_task_with_dates
    ):
        """Fields like creator, deleted, kind, isFloating should not be sent to the API."""
        mock_client.get_by_id = MagicMock(return_value=existing_task_with_dates.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", title="Updated")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]

        assert "creator" not in sent_to_api
        assert "deleted" not in sent_to_api
        assert "kind" not in sent_to_api
        assert "isFloating" not in sent_to_api
        assert "etag" not in sent_to_api
        assert "createdTime" not in sent_to_api
        assert "modifiedTime" not in sent_to_api

    def test_updatable_fields_preserved(
        self, mock_client, existing_task_with_dates
    ):
        """All updatable fields from the existing task should be included."""
        mock_client.get_by_id = MagicMock(return_value=existing_task_with_dates.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", title="Updated")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]

        assert "id" in sent_to_api
        assert "projectId" in sent_to_api
        assert "title" in sent_to_api
        assert "content" in sent_to_api
        assert "priority" in sent_to_api
        assert "startDate" in sent_to_api
        assert "dueDate" in sent_to_api
        assert "timeZone" in sent_to_api
        assert "status" in sent_to_api
        assert "tags" in sent_to_api


# --- Tests: Error handling ---

class TestUpdateTaskErrorHandling:

    def test_client_exception_returns_error(self, mock_client):
        """Exceptions from the client should be caught and returned as error responses."""
        mock_client.get_by_id = MagicMock(side_effect=Exception("Connection timeout"))

        task_obj = TaskObject(id="task123", projectId="proj456", priority=5)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object=task_obj))

        parsed = json.loads(result)
        assert "error" in parsed
        assert "task123" in parsed["error"]


# --- Tests: exclude_unset behavior ---

class TestUpdateTaskExcludeUnset:

    def test_default_priority_not_sent_when_unset(self, mock_client):
        """TaskObject has priority=0 as default. If not explicitly set, it should not
        be included in update_fields and should not overwrite existing priority."""
        existing = {
            "id": "task123",
            "projectId": "proj456",
            "title": "Task",
            "priority": 5,
        }
        mock_client.get_by_id = MagicMock(return_value=existing.copy())

        # Only set title - priority is NOT set, so default 0 should not override
        task_obj = TaskObject(id="task123", projectId="proj456", title="New title")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]
        assert sent_to_api["priority"] == 5

    def test_explicit_priority_zero_is_sent(self, mock_client):
        """If the user explicitly sets priority=0, it should overwrite existing priority."""
        existing = {
            "id": "task123",
            "projectId": "proj456",
            "title": "Task",
            "priority": 5,
        }
        mock_client.get_by_id = MagicMock(return_value=existing.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", priority=0)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]
        assert sent_to_api["priority"] == 0
