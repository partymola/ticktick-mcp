"""Pin that the tools actually invoke the freshness sync in the right places.

These guard the Problem-1 fix itself: the freshness module is unit-tested
elsewhere, but these assert the WIRING -- that each active-read tool syncs
before reading, each mutation force-syncs before its pre-read, and the
filter tool syncs on the uncompleted path but NOT on the (already-live)
completed path.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.tools.filter_tools import ticktick_filter_tasks
from ticktick_mcp.tools.task_tools import (
    ticktick_complete_task,
    ticktick_get_by_id,
    ticktick_get_tasks_from_project,
    update_task,
)

TASK_CLIENT = "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client"
FILTER_CLIENT = "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def mock_client():
    return MagicMock()


class TestReadToolsSync:
    def test_get_by_id_syncs_before_read(self, mock_client):
        mock_client.get_by_id = MagicMock(return_value={"id": "t1"})
        with patch(TASK_CLIENT, return_value=mock_client):
            run(ticktick_get_by_id(obj_id="t1"))
        mock_client.sync.assert_called_once()

    def test_get_tasks_from_project_syncs(self, mock_client):
        mock_client.task.get_from_project = MagicMock(return_value=[])
        with patch(TASK_CLIENT, return_value=mock_client):
            run(ticktick_get_tasks_from_project(project_id="p1"))
        mock_client.sync.assert_called_once()


class TestMutationToolsForceSync:
    def test_complete_syncs_before_read(self, mock_client):
        task_obj = {"id": "t1", "projectId": "p1", "status": 0}
        completed = {"id": "t1", "projectId": "p1", "status": 2}
        mock_client.get_by_id = MagicMock(side_effect=[task_obj, completed])
        mock_client.task.complete = MagicMock(return_value=completed)
        with patch(TASK_CLIENT, return_value=mock_client):
            run(ticktick_complete_task(task_id="t1"))
        mock_client.sync.assert_called_once()

    def test_update_syncs_before_read(self, mock_client):
        existing = {"id": "t1", "projectId": "p1", "status": 0, "title": "T", "priority": 0}
        mock_client.get_by_id = MagicMock(return_value=existing)
        mock_client.task.update = MagicMock(side_effect=lambda d: d)
        with patch(TASK_CLIENT, return_value=mock_client):
            run(update_task(task_object={"id": "t1", "projectId": "p1", "priority": 5}))
        mock_client.sync.assert_called_once()


class TestFilterSyncGating:
    def test_uncompleted_path_syncs(self, mock_client):
        with (
            patch(FILTER_CLIENT, return_value=mock_client),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[],
            ),
        ):
            run(ticktick_filter_tasks(filter_criteria={"status": "uncompleted"}))
        mock_client.sync.assert_called_once()

    def test_completed_path_does_not_sync(self, mock_client):
        # Completed queries are fetched live; no local sync should happen.
        with patch(FILTER_CLIENT, return_value=mock_client):
            run(ticktick_filter_tasks(filter_criteria={"status": "completed"}))
        mock_client.sync.assert_not_called()
