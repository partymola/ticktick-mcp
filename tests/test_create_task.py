"""Tests for create_task fix: fields not handled by builder should be included."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.tools.task_tools import ticktick_create_task


def run(coro):
    """Helper to run an async function synchronously."""
    return asyncio.run(coro)


# --- Fixtures ---


@pytest.fixture
def mock_client():
    """Create a mock TickTick client with task.builder and task.create methods."""
    client = MagicMock()

    def fake_create(task_dict):
        result = dict(task_dict)
        result["id"] = "new_task_id_123"
        return result

    client.task.create = MagicMock(side_effect=fake_create)
    return client


# --- Tests: Fields missing from builder output ---


class TestCreateTaskBuilderMissingFields:
    def test_dates_included_when_builder_omits_them(self, mock_client):
        """If builder output lacks startDate/dueDate, the fix should add them."""
        mock_client.task.builder = MagicMock(
            return_value={
                "title": "Test task",
                "projectId": "proj123",
            }
        )

        with (
            patch(
                "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
                return_value=mock_client,
            ),
            patch(
                "ticktick_mcp.tools.task_tools.convert_date_to_tick_tick_format",
                side_effect=lambda dt, tz: dt.isoformat(),
            ),
        ):
            run(
                ticktick_create_task(
                    title="Test task",
                    projectId="proj123",
                    startDate="2024-08-01T09:00:00+00:00",
                    dueDate="2024-08-01T17:00:00+00:00",
                    expectedDayOfWeek="Thursday",
                    timeZone="Europe/London",
                )
            )

        created_dict = mock_client.task.create.call_args[0][0]
        assert "startDate" in created_dict
        assert "dueDate" in created_dict
        assert created_dict["startDate"] is not None
        assert created_dict["dueDate"] is not None

    def test_reminders_included_when_builder_omits_them(self, mock_client):
        """If builder output lacks reminders, the fix should add them."""
        mock_client.task.builder = MagicMock(
            return_value={
                "title": "Test task",
            }
        )

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(
                ticktick_create_task(
                    title="Test task",
                    reminders=["TRIGGER:-PT30M", "TRIGGER:PT0S"],
                )
            )

        created_dict = mock_client.task.create.call_args[0][0]
        assert created_dict["reminders"] == ["TRIGGER:-PT30M", "TRIGGER:PT0S"]

    def test_priority_included_when_builder_omits_it(self, mock_client):
        """If builder output lacks priority, the fix should add it."""
        mock_client.task.builder = MagicMock(
            return_value={
                "title": "Test task",
            }
        )

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(
                ticktick_create_task(
                    title="Test task",
                    priority=5,
                )
            )

        created_dict = mock_client.task.create.call_args[0][0]
        assert created_dict["priority"] == 5

    def test_timezone_included_when_builder_omits_it(self, mock_client):
        """If builder output lacks timeZone, the fix should add it."""
        mock_client.task.builder = MagicMock(
            return_value={
                "title": "Test task",
            }
        )

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(
                ticktick_create_task(
                    title="Test task",
                    timeZone="Asia/Seoul",
                )
            )

        created_dict = mock_client.task.create.call_args[0][0]
        assert created_dict["timeZone"] == "Asia/Seoul"

    def test_priority_zero_included_when_builder_omits_it(self, mock_client):
        """priority=0 (None priority) should still be added if builder omits it,
        since the caller explicitly provided it."""
        mock_client.task.builder = MagicMock(
            return_value={
                "title": "Test task",
            }
        )

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(
                ticktick_create_task(
                    title="Test task",
                    priority=0,
                )
            )

        created_dict = mock_client.task.create.call_args[0][0]
        assert "priority" in created_dict
        assert created_dict["priority"] == 0


# --- Tests: Fields already in builder output ---


class TestCreateTaskBuilderFieldsPreserved:
    def test_builder_dates_not_overwritten(self, mock_client):
        """If builder already includes dates, the fix should not overwrite them."""
        builder_start = "2024-08-01T09:00:00+0000"
        builder_due = "2024-08-01T17:00:00+0000"
        mock_client.task.builder = MagicMock(
            return_value={
                "title": "Test task",
                "startDate": builder_start,
                "dueDate": builder_due,
            }
        )

        with (
            patch(
                "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
                return_value=mock_client,
            ),
            patch(
                "ticktick_mcp.tools.task_tools.convert_date_to_tick_tick_format",
                return_value="SHOULD_NOT_APPEAR",
            ),
        ):
            run(
                ticktick_create_task(
                    title="Test task",
                    startDate="2024-08-01T09:00:00+00:00",
                    dueDate="2024-08-01T17:00:00+00:00",
                    expectedDayOfWeek="Thursday",
                )
            )

        created_dict = mock_client.task.create.call_args[0][0]
        assert created_dict["startDate"] == builder_start
        assert created_dict["dueDate"] == builder_due

    def test_builder_reminders_not_overwritten(self, mock_client):
        """If builder already includes reminders, the fix should not overwrite them."""
        builder_reminders = ["TRIGGER:-PT15M"]
        mock_client.task.builder = MagicMock(
            return_value={
                "title": "Test task",
                "reminders": builder_reminders,
            }
        )

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(
                ticktick_create_task(
                    title="Test task",
                    reminders=["TRIGGER:-PT30M"],
                )
            )

        created_dict = mock_client.task.create.call_args[0][0]
        assert created_dict["reminders"] == builder_reminders

    def test_builder_priority_not_overwritten(self, mock_client):
        """If builder already includes priority, the fix should not overwrite it."""
        mock_client.task.builder = MagicMock(
            return_value={
                "title": "Test task",
                "priority": 3,
            }
        )

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(
                ticktick_create_task(
                    title="Test task",
                    priority=5,
                )
            )

        created_dict = mock_client.task.create.call_args[0][0]
        assert created_dict["priority"] == 3

    def test_builder_timezone_not_overwritten(self, mock_client):
        """If builder already includes timeZone, the fix should not overwrite it."""
        mock_client.task.builder = MagicMock(
            return_value={
                "title": "Test task",
                "timeZone": "Asia/Tokyo",
            }
        )

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(
                ticktick_create_task(
                    title="Test task",
                    timeZone="Europe/London",
                )
            )

        created_dict = mock_client.task.create.call_args[0][0]
        assert created_dict["timeZone"] == "Asia/Tokyo"


# --- Tests: No fields to add ---


class TestCreateTaskNoExtraFields:
    def test_no_dates_no_extras_added(self, mock_client):
        """If no dates/reminders/priority were requested, nothing extra should be added."""
        mock_client.task.builder = MagicMock(
            return_value={
                "title": "Simple task",
            }
        )

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(ticktick_create_task(title="Simple task"))

        created_dict = mock_client.task.create.call_args[0][0]
        # Should only have what builder returned (create adds id, but we check the input)
        assert "startDate" not in created_dict
        assert "dueDate" not in created_dict
        assert "reminders" not in created_dict
        assert "priority" not in created_dict
        assert "timeZone" not in created_dict

    def test_none_priority_not_added(self, mock_client):
        """priority=None (default) should NOT trigger the fallback."""
        mock_client.task.builder = MagicMock(
            return_value={
                "title": "Task",
            }
        )

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(ticktick_create_task(title="Task"))

        created_dict = mock_client.task.create.call_args[0][0]
        assert "priority" not in created_dict


# --- Tests: Default timezone fallback ---


class TestCreateTaskTimezoneDefault:
    def test_local_timezone_used_for_date_conversion(self, mock_client):
        """When timeZone is not provided, get_localzone() should be used."""
        mock_client.task.builder = MagicMock(
            return_value={
                "title": "Task",
            }
        )

        with (
            patch(
                "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
                return_value=mock_client,
            ),
            patch(
                "ticktick_mcp.tools.task_tools.get_localzone",
            ) as mock_localzone,
            patch(
                "ticktick_mcp.tools.task_tools.convert_date_to_tick_tick_format",
                return_value="2024-08-01T17:00:00+0100",
            ) as mock_convert,
        ):
            mock_localzone.return_value.key = "Europe/London"

            run(
                ticktick_create_task(
                    title="Task",
                    dueDate="2024-08-01T17:00:00+01:00",
                    expectedDayOfWeek="Thursday",
                )
            )

        mock_convert.assert_called_once()
        args = mock_convert.call_args[0]
        assert args[1] == "Europe/London"


# --- Tests: Error handling ---


class TestCreateTaskErrorHandling:
    def test_invalid_date_format_returns_error(self, mock_client):
        """Invalid ISO date format should return an error, not crash."""
        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(
                ticktick_create_task(
                    title="Test",
                    dueDate="not-a-date",
                )
            )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "Invalid date format" in parsed["error"]

    def test_client_exception_returns_error(self, mock_client):
        """Exceptions from the client should be caught and returned as errors."""
        mock_client.task.builder = MagicMock(side_effect=Exception("API error"))

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_create_task(title="Test"))

        parsed = json.loads(result)
        assert "error" in parsed
