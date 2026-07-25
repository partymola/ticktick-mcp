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
    ticktick_delete_tasks,
    ticktick_get_by_id,
    ticktick_get_tasks_from_project,
    ticktick_make_subtask,
    ticktick_move_task,
    update_task,
)

TASK_CLIENT = "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client"
FILTER_CLIENT = "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def mock_client():
    client = MagicMock()
    # "p1" must be a KNOWN project id here. Without a state the resolver reads
    # it as an unrecognised name and syncs on its own, which satisfies
    # sync.assert_called_once() no matter what the tool does - and then these
    # tests pass with the tool's own sync deleted, which is the one thing they
    # exist to detect.
    client.state = {"projects": [{"id": "p1", "name": "Project One"}], "tasks": []}
    client.inbox_id = "inbox1"
    return client


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


# --- pre-read freshness: the sync serves the tool's OWN read ------------------
#
# These are separate from project resolution. Removing them was possible because
# nothing observed them: the resolver's own sync satisfied the assertions above.


def _client_where_the_task_only_appears_after_sync():
    """A task created on another device: absent from the snapshot until a sync."""
    client = MagicMock()
    client.state = {"projects": [{"id": "p1", "name": "Project One"}], "tasks": []}
    client.inbox_id = "inbox1"
    seen = {"task": {}}

    def _sync(*a, **k):
        seen["task"] = {"id": "t1", "projectId": "pREAL", "title": "T", "status": 0}
        return {}

    client.sync.side_effect = _sync
    client.get_by_id.side_effect = lambda i, *a, **k: seen["task"] if i == "t1" else {}
    return client


def test_move_task_syncs_before_its_own_pre_read():
    """task.move() sends the fetched body back, so a task missing from a stale
    snapshot cannot be moved at all."""
    client = _client_where_the_task_only_appears_after_sync()
    with patch(TASK_CLIENT, return_value=client):
        run(ticktick_move_task("t1", "p1"))
    assert client.task.move.called, "move never happened - the pre-read saw a stale snapshot"


def test_delete_syncs_before_its_own_pre_read():
    """Without the sync the task is missing, control falls to the project_id
    branch, and the delete goes out against the CALLER's project rather than
    the task's own - reporting success either way."""
    client = _client_where_the_task_only_appears_after_sync()
    with patch(TASK_CLIENT, return_value=client):
        run(ticktick_delete_tasks(["t1"], project_id="p1"))
    sent = client.task.delete.call_args[0][0]
    payload = sent[0] if isinstance(sent, list) else sent
    assert payload["projectId"] == "pREAL", "deleted against the caller's project, not the task's"


def test_make_subtask_syncs_before_its_own_pre_reads():
    """Both ends are read from local state and sent back. This tool had no sync
    of its own and only got one when protection happened to be configured."""
    client = MagicMock()
    client.state = {"projects": [{"id": "p1", "name": "Project One"}], "tasks": []}
    client.inbox_id = "inbox1"
    seen = {"tasks": {}}

    def _sync(*a, **k):
        seen["tasks"] = {
            "parent1": {"id": "parent1", "projectId": "p1", "title": "P"},
            "child1": {"id": "child1", "projectId": "p1", "title": "C"},
        }
        return {}

    client.sync.side_effect = _sync
    client.get_by_id.side_effect = lambda i, *a, **k: seen["tasks"].get(i, {})
    with patch(TASK_CLIENT, return_value=client):
        run(ticktick_make_subtask("parent1", "child1"))
    assert client.task.make_subtask.called, "neither end was visible in the stale snapshot"


def test_delete_forces_its_pre_read_sync_rather_than_honouring_the_throttle():
    """A throttled sync is not enough: something else syncing seconds earlier
    leaves the task invisible, delete falls back to the caller's projectId, and
    it deletes from the wrong project while reporting success."""
    from ticktick_mcp.freshness import ensure_fresh as real_ensure_fresh

    client = MagicMock()
    client.state = {"projects": [{"id": "p1", "name": "Project One"}], "tasks": []}
    client.inbox_id = "inbox1"
    client.sync.side_effect = lambda *a, **k: {}
    real_ensure_fresh(client)  # opens the throttle window

    seen = {"task": {}}

    def _sync(*a, **k):
        seen["task"] = {"id": "t1", "projectId": "pREAL", "title": "T", "status": 0}
        return {}

    client.sync.side_effect = _sync
    client.get_by_id.side_effect = lambda i, *a, **k: seen["task"] if i == "t1" else {}
    with patch(TASK_CLIENT, return_value=client):
        run(ticktick_delete_tasks(["t1"], project_id="p1"))
    sent = client.task.delete.call_args[0][0]
    payload = sent[0] if isinstance(sent, list) else sent
    assert payload["projectId"] == "pREAL", "throttle served a stale snapshot to the pre-read"


def test_make_subtask_forces_its_pre_read_sync_rather_than_honouring_the_throttle():
    """Both ends are read from local state and posted back, so a snapshot
    served by a warm throttle makes one or both ends look absent."""
    from ticktick_mcp.freshness import ensure_fresh as real_ensure_fresh

    client = MagicMock()
    client.state = {"projects": [{"id": "p1", "name": "Project One"}], "tasks": []}
    client.inbox_id = "inbox1"
    client.sync.side_effect = lambda *a, **k: {}
    real_ensure_fresh(client)  # opens the throttle window

    seen = {"tasks": {}}

    def _sync(*a, **k):
        seen["tasks"] = {
            "parent1": {"id": "parent1", "projectId": "p1", "title": "P"},
            "child1": {"id": "child1", "projectId": "p1", "title": "C"},
        }
        return {}

    client.sync.side_effect = _sync
    client.get_by_id.side_effect = lambda i, *a, **k: seen["tasks"].get(i, {})
    with patch(TASK_CLIENT, return_value=client):
        run(ticktick_make_subtask("parent1", "child1"))
    assert client.task.make_subtask.called, "throttle served a stale snapshot to the pre-read"


def test_move_forces_its_pre_read_sync_rather_than_honouring_the_throttle():
    """task.move() takes fromProjectId from the fetched body, so a snapshot
    served by a warm throttle moves the task out of the wrong project."""
    from ticktick_mcp.freshness import ensure_fresh as real_ensure_fresh

    client = MagicMock()
    client.state = {"projects": [{"id": "p1", "name": "Project One"}], "tasks": []}
    client.inbox_id = "inbox1"
    client.sync.side_effect = lambda *a, **k: {}
    real_ensure_fresh(client)  # opens the throttle window

    seen = {"task": {}}

    def _sync(*a, **k):
        seen["task"] = {"id": "t1", "projectId": "pREAL", "title": "T", "status": 0}
        return {}

    client.sync.side_effect = _sync
    client.get_by_id.side_effect = lambda i, *a, **k: seen["task"] if i == "t1" else {}
    with patch(TASK_CLIENT, return_value=client):
        run(ticktick_move_task("t1", "p1"))
    assert client.task.move.called, "throttle served a stale snapshot; the task looked absent"
    assert client.task.move.call_args[0][0]["projectId"] == "pREAL"
