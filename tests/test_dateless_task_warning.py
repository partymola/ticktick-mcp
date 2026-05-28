"""Tests for dateless task warning in create_task."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.tools.task_tools import ticktick_create_task


@pytest.fixture
def mock_client():
    """Mock TickTickClientSingleton to return a fake client."""
    client = MagicMock()
    client.task.builder.return_value = {"title": "Test task"}
    client.task.create.return_value = {"id": "abc123", "title": "Test task"}
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
        return_value=client,
    ):
        yield client


def test_warning_when_no_due_date(mock_client):
    result = asyncio.run(ticktick_create_task(title="Test task without date"))
    data = json.loads(result)
    assert "_verification_warnings" in data
    assert any("No due_date set" in w for w in data["_verification_warnings"])


def test_no_warning_when_due_date_present(mock_client):
    # Echo the sent fields back from builder/create so verify_mutation
    # does not flag missing or mismatched values.
    mock_client.task.builder.return_value = {
        "title": "Test task with date",
        "dueDate": "2026-04-13T19:45:00+0000",
    }
    mock_client.task.create.return_value = {
        "id": "abc123",
        "title": "Test task with date",
        "dueDate": "2026-04-13T19:45:00+0000",
    }
    result = asyncio.run(
        ticktick_create_task(
            title="Test task with date",
            due_date="2026-04-13T20:45:00+01:00",
            expected_day_of_week="Monday",
        )
    )
    data = json.loads(result)
    assert "_verification_warnings" not in data
