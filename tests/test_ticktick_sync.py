"""Tests for the ticktick_sync tool (explicit force-refresh)."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.tools.task_tools import ticktick_sync


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def mock_client():
    return MagicMock()


class TestTickTickSync:
    def test_sync_forces_and_reports_counts(self, mock_client):
        mock_client.state = {
            "tasks": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
            "projects": [{"id": "p1"}, {"id": "p2"}],
        }
        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_sync())

        mock_client.sync.assert_called_once()
        parsed = json.loads(result)
        assert parsed["status"] == "synced"
        assert parsed["task_count"] == 3
        assert parsed["project_count"] == 2

    def test_sync_reports_error_when_sync_fails(self, mock_client):
        mock_client.sync.side_effect = RuntimeError("network down")
        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_sync())

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "detail" in parsed

    def test_sync_handles_missing_state_keys(self, mock_client):
        mock_client.state = {}
        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_sync())

        parsed = json.loads(result)
        assert parsed["status"] == "synced"
        assert parsed["task_count"] == 0
        assert parsed["project_count"] == 0
