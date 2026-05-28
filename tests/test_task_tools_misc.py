"""Tests for task tools not covered by test_create_task.py /
test_update_task.py / test_verify_mutation.py.

Covers: ticktick_delete_tasks, ticktick_complete_task, ticktick_move_task,
ticktick_make_subtask, ticktick_get_tasks_from_project, ticktick_get_by_id,
ticktick_get_all.
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch

from ticktick_mcp.tools.task_tools import (
    ticktick_delete_tasks,
    ticktick_complete_task,
    ticktick_move_task,
    ticktick_make_subtask,
    ticktick_get_tasks_from_project,
    ticktick_get_by_id,
    ticktick_get_all,
)


def run(coro):
    """Helper to run an async function synchronously."""
    return asyncio.run(coro)


@pytest.fixture
def mock_client():
    """A fresh MagicMock TickTick client per test."""
    return MagicMock()


# ============================================================ #
# ticktick_delete_tasks                                        #
# ============================================================ #

class TestDeleteTasks:

    def test_delete_single_task_by_id_string(self, mock_client):
        """A single task_id string should fetch one object and call delete with a single dict."""
        task_obj = {"id": "t1", "projectId": "p1", "title": "Task"}
        mock_client.get_by_id = MagicMock(return_value=task_obj)
        mock_client.task.delete = MagicMock(return_value={"ok": True})

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_delete_tasks(task_ids="t1"))

        # When input is a single string, delete should be called with a single dict
        mock_client.task.delete.assert_called_once_with(task_obj)
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["deleted_count"] == 1
        assert parsed["tasks_deleted_ids"] == ["t1"]

    def test_delete_multiple_tasks_by_id_list(self, mock_client):
        """A list of task_ids should fetch each and pass a list to delete."""
        objs = {
            "t1": {"id": "t1", "projectId": "p1", "title": "A"},
            "t2": {"id": "t2", "projectId": "p1", "title": "B"},
        }
        mock_client.get_by_id = MagicMock(side_effect=lambda tid: objs.get(tid))
        mock_client.task.delete = MagicMock(return_value={"ok": True})

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_delete_tasks(task_ids=["t1", "t2"]))

        call_arg = mock_client.task.delete.call_args[0][0]
        assert isinstance(call_arg, list)
        assert len(call_arg) == 2
        parsed = json.loads(result)
        assert parsed["deleted_count"] == 2
        assert set(parsed["tasks_deleted_ids"]) == {"t1", "t2"}

    def test_delete_completed_task_with_project_id(self, mock_client):
        """Tasks not in local state should still be deleted when project_id is supplied."""
        # get_by_id returns None (task is completed and not in local state)
        mock_client.get_by_id = MagicMock(return_value=None)
        mock_client.task.delete = MagicMock(return_value={"ok": True})

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_delete_tasks(task_ids="t_completed", project_id="pX"))

        # delete should be called with a minimal dict {id, projectId}
        called = mock_client.task.delete.call_args[0][0]
        assert called == {"id": "t_completed", "projectId": "pX"}
        parsed = json.loads(result)
        assert parsed["status"] == "success"

    def test_missing_ids_returns_not_found(self, mock_client):
        """All-missing IDs (no project_id fallback) return not_found with missing_ids."""
        mock_client.get_by_id = MagicMock(return_value=None)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_delete_tasks(task_ids=["missing1", "missing2"]))

        parsed = json.loads(result)
        assert parsed["status"] == "not_found"
        assert parsed["missing_ids"] == ["missing1", "missing2"]

    def test_invalid_object_returns_warning(self, mock_client):
        """An object found but not looking like a task (no projectId/title) goes to invalid_ids."""
        # The returned object is a dict but lacks the required fields
        mock_client.get_by_id = MagicMock(return_value={"id": "x", "kind": "project"})

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_delete_tasks(task_ids=["x"]))

        parsed = json.loads(result)
        assert parsed["status"] == "not_found"
        assert parsed["invalid_ids"] == ["x"]

    def test_partial_success_includes_warning_field(self, mock_client):
        """A mix of valid + missing IDs deletes the valid ones and lists the missing ones in warnings."""
        objs = {"t1": {"id": "t1", "projectId": "p1", "title": "A"}}
        mock_client.get_by_id = MagicMock(side_effect=lambda tid: objs.get(tid))
        mock_client.task.delete = MagicMock(return_value={"ok": True})

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_delete_tasks(task_ids=["t1", "missing"]))

        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["deleted_count"] == 1
        assert "warnings" in parsed
        assert "missing" in parsed["warnings"]

    def test_empty_list_returns_error(self, mock_client):
        """Empty list of IDs yields a 'no task IDs provided' error message."""
        mock_client.get_by_id = MagicMock(return_value=None)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_delete_tasks(task_ids=[]))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "No task IDs" in parsed["message"]

    def test_connection_error_returns_error_status(self, mock_client):
        """A ConnectionError from the client is caught and returned as error status."""
        mock_client.get_by_id = MagicMock(side_effect=ConnectionError("offline"))

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_delete_tasks(task_ids="t1"))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "offline" in parsed["error"]

    def test_generic_exception_returns_error(self, mock_client):
        """Any other exception is caught and returned as an error."""
        mock_client.get_by_id = MagicMock(side_effect=RuntimeError("boom"))

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_delete_tasks(task_ids="t1"))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "boom" in parsed["error"]


# ============================================================ #
# ticktick_complete_task                                       #
# ============================================================ #

class TestCompleteTask:

    def test_success_returns_refetched_task(self, mock_client):
        """Happy path: complete succeeds, refetch shows status != 0, returns refetched obj."""
        task_obj = {"id": "t1", "projectId": "p1", "status": 0}
        completed_obj = {"id": "t1", "projectId": "p1", "status": 2}
        mock_client.get_by_id = MagicMock(side_effect=[task_obj, completed_obj])
        mock_client.task.complete = MagicMock(return_value=completed_obj)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_complete_task(task_id="t1"))

        parsed = json.loads(result)
        assert parsed["status"] == 2
        assert parsed["id"] == "t1"
        # complete was called with the originally fetched object
        mock_client.task.complete.assert_called_once_with(task_obj)

    def test_task_not_found_returns_not_found(self, mock_client):
        """If initial get_by_id returns None, return not_found error."""
        mock_client.get_by_id = MagicMock(return_value=None)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_complete_task(task_id="missing"))

        parsed = json.loads(result)
        assert parsed["status"] == "not_found"
        assert "not found" in parsed["error"]
        # complete should never have been called
        mock_client.task.complete.assert_not_called()

    def test_task_with_no_project_id_returns_not_found(self, mock_client):
        """An object without projectId is treated as not_found."""
        mock_client.get_by_id = MagicMock(return_value={"id": "t1"})

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_complete_task(task_id="t1"))

        parsed = json.loads(result)
        assert parsed["status"] == "not_found"

    def test_refetch_returns_none_warns(self, mock_client):
        """If refetch returns None (status verification failed), return warning."""
        task_obj = {"id": "t1", "projectId": "p1", "status": 0}
        mock_client.get_by_id = MagicMock(side_effect=[task_obj, None])
        mock_client.task.complete = MagicMock(return_value={"some": "api_resp"})

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_complete_task(task_id="t1"))

        parsed = json.loads(result)
        assert "_verification_warnings" in parsed
        assert any("verification failed" in w for w in parsed["_verification_warnings"])

    def test_refetch_status_still_zero_warns(self, mock_client):
        """If refetched task still has status=0, return warning."""
        task_obj = {"id": "t1", "projectId": "p1", "status": 0}
        mock_client.get_by_id = MagicMock(side_effect=[task_obj, task_obj])
        mock_client.task.complete = MagicMock(return_value={"some": "api_resp"})

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_complete_task(task_id="t1"))

        parsed = json.loads(result)
        assert "_verification_warnings" in parsed

    def test_complete_raises_returns_error(self, mock_client):
        """If client.task.complete raises, return formatted error."""
        task_obj = {"id": "t1", "projectId": "p1", "status": 0}
        mock_client.get_by_id = MagicMock(return_value=task_obj)
        mock_client.task.complete = MagicMock(side_effect=Exception("API down"))

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_complete_task(task_id="t1"))

        parsed = json.loads(result)
        assert "error" in parsed
        assert "API down" in parsed["error"]


# ============================================================ #
# ticktick_move_task                                           #
# ============================================================ #

class TestMoveTask:

    def test_move_success_returns_moved_task(self, mock_client):
        """Happy path: source task + target project both found, move returns updated obj."""
        task_obj = {"id": "t1", "projectId": "p1", "title": "Hi"}
        target_proj = {"id": "p2", "name": "Target"}
        moved = {"id": "t1", "projectId": "p2", "title": "Hi"}
        mock_client.get_by_id = MagicMock(side_effect=[task_obj, target_proj])
        mock_client.task.move = MagicMock(return_value=moved)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_move_task(task_id="t1", new_project_id="p2"))

        parsed = json.loads(result)
        assert parsed["projectId"] == "p2"
        mock_client.task.move.assert_called_once_with(task_obj, "p2")

    def test_task_not_found_returns_error(self, mock_client):
        """If task lookup yields a dict without projectId, return not_found error.

        NOTE: Known quirk - if get_by_id returns None the next line raises
        AttributeError, which is caught by the outer try/except and reported
        as a generic error. The "not_found" path only triggers for an object
        that has no projectId key.
        """
        mock_client.get_by_id = MagicMock(return_value={"id": "t1"})  # no projectId

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_move_task(task_id="t1", new_project_id="p2"))

        parsed = json.loads(result)
        assert parsed["status"] == "not_found"

    def test_task_none_falls_through_to_generic_error(self, mock_client):
        """Characterisation: get_by_id returning None for the task causes AttributeError,
        caught by the outer except and reported as a generic 'Failed to move task' error."""
        mock_client.get_by_id = MagicMock(return_value=None)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_move_task(task_id="t1", new_project_id="p2"))

        parsed = json.loads(result)
        assert "error" in parsed
        # No status='not_found' here - it falls through to the generic handler
        assert parsed.get("status") != "not_found"

    def test_target_project_missing_proceeds_anyway(self, mock_client):
        """Even when target project is not found, the move is attempted (documented behaviour)."""
        task_obj = {"id": "t1", "projectId": "p1", "title": "Hi"}
        # Source task found, target project not found
        mock_client.get_by_id = MagicMock(side_effect=[task_obj, None])
        moved = {"id": "t1", "projectId": "p2"}
        mock_client.task.move = MagicMock(return_value=moved)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_move_task(task_id="t1", new_project_id="p2"))

        mock_client.task.move.assert_called_once()
        parsed = json.loads(result)
        assert parsed["projectId"] == "p2"

    def test_move_raises_returns_error(self, mock_client):
        """An exception in task.move is caught and returned as error."""
        task_obj = {"id": "t1", "projectId": "p1"}
        target_proj = {"id": "p2"}
        mock_client.get_by_id = MagicMock(side_effect=[task_obj, target_proj])
        mock_client.task.move = MagicMock(side_effect=Exception("nope"))

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_move_task(task_id="t1", new_project_id="p2"))

        parsed = json.loads(result)
        assert "error" in parsed
        assert "nope" in parsed["error"]


# ============================================================ #
# ticktick_make_subtask                                        #
# ============================================================ #

class TestMakeSubtask:

    def test_success_returns_success_payload(self, mock_client):
        """Happy path: both tasks in same project, make_subtask succeeds."""
        child = {"id": "c1", "projectId": "p1", "title": "child"}
        parent = {"id": "pt1", "projectId": "p1", "title": "parent"}
        updated_parent = {"id": "pt1", "projectId": "p1", "items": [child]}
        # Order: child fetch, parent fetch, then refetch parent
        mock_client.get_by_id = MagicMock(side_effect=[child, parent, updated_parent])
        mock_client.task.make_subtask = MagicMock(return_value={"raw": "ok"})

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_make_subtask(parent_task_id="pt1", child_task_id="c1"))

        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["updated_parent_task"] == updated_parent
        mock_client.task.make_subtask.assert_called_once_with(child, "pt1")

    def test_same_parent_and_child_rejected(self, mock_client):
        """Same IDs short-circuit before any client calls."""
        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_make_subtask(parent_task_id="same", child_task_id="same"))

        parsed = json.loads(result)
        assert "error" in parsed
        assert "cannot be the same" in parsed["error"]
        # The client should not have been touched
        mock_client.get_by_id.assert_not_called()

    def test_child_not_found(self, mock_client):
        """If child task lookup returns None, return not_found."""
        mock_client.get_by_id = MagicMock(return_value=None)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_make_subtask(parent_task_id="pt1", child_task_id="missing"))

        parsed = json.loads(result)
        assert parsed["status"] == "not_found"
        assert "Child task" in parsed["error"]

    def test_parent_not_found(self, mock_client):
        """Child found but parent missing → not_found error."""
        child = {"id": "c1", "projectId": "p1", "title": "child"}
        mock_client.get_by_id = MagicMock(side_effect=[child, None])

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_make_subtask(parent_task_id="missing", child_task_id="c1"))

        parsed = json.loads(result)
        assert parsed["status"] == "not_found"
        assert "Parent task" in parsed["error"]

    def test_cross_project_rejected(self, mock_client):
        """If child and parent are in different projects, reject with explicit project info."""
        child = {"id": "c1", "projectId": "pA", "title": "child"}
        parent = {"id": "pt1", "projectId": "pB", "title": "parent"}
        mock_client.get_by_id = MagicMock(side_effect=[child, parent])

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_make_subtask(parent_task_id="pt1", child_task_id="c1"))

        parsed = json.loads(result)
        assert "same project" in parsed["error"]
        assert parsed["child_project"] == "pA"
        assert parsed["parent_project"] == "pB"

    def test_make_subtask_raises_returns_error(self, mock_client):
        """Exception during make_subtask is caught and returned."""
        child = {"id": "c1", "projectId": "p1", "title": "child"}
        parent = {"id": "pt1", "projectId": "p1", "title": "parent"}
        mock_client.get_by_id = MagicMock(side_effect=[child, parent])
        mock_client.task.make_subtask = MagicMock(side_effect=Exception("api error"))

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_make_subtask(parent_task_id="pt1", child_task_id="c1"))

        parsed = json.loads(result)
        assert "error" in parsed
        assert "api error" in parsed["error"]


# ============================================================ #
# ticktick_get_tasks_from_project                              #
# ============================================================ #

class TestGetTasksFromProject:

    def test_returns_list_unchanged(self, mock_client):
        """A list response is returned as a list."""
        tasks = [{"id": "t1"}, {"id": "t2"}]
        mock_client.task.get_from_project = MagicMock(return_value=tasks)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_get_tasks_from_project(project_id="p1"))

        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2

    def test_none_response_becomes_empty_list(self, mock_client):
        """If API returns None, the tool converts it to []."""
        mock_client.task.get_from_project = MagicMock(return_value=None)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_get_tasks_from_project(project_id="p1"))

        parsed = json.loads(result)
        assert parsed == []

    def test_single_dict_response_wrapped_in_list(self, mock_client):
        """If API returns a single dict, the tool wraps it in a list."""
        mock_client.task.get_from_project = MagicMock(return_value={"id": "t1"})

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_get_tasks_from_project(project_id="p1"))

        parsed = json.loads(result)
        assert parsed == [{"id": "t1"}]

    def test_exception_returns_error(self, mock_client):
        """Exception from get_from_project is caught and returned."""
        mock_client.task.get_from_project = MagicMock(side_effect=Exception("bad"))

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_get_tasks_from_project(project_id="p1"))

        parsed = json.loads(result)
        assert "error" in parsed
        assert "bad" in parsed["error"]


# ============================================================ #
# ticktick_get_by_id                                           #
# ============================================================ #

class TestGetById:

    def test_success_returns_object(self, mock_client):
        obj = {"id": "x1", "title": "Task"}
        mock_client.get_by_id = MagicMock(return_value=obj)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_get_by_id(obj_id="x1"))

        parsed = json.loads(result)
        assert parsed["id"] == "x1"

    def test_exception_returns_error(self, mock_client):
        mock_client.get_by_id = MagicMock(side_effect=Exception("fail"))

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_get_by_id(obj_id="x1"))

        parsed = json.loads(result)
        assert "error" in parsed
        assert "fail" in parsed["error"]


# ============================================================ #
# ticktick_get_all                                             #
# ============================================================ #

class TestGetAll:

    def test_tasks_search_calls_get_all_tasks(self, mock_client):
        """search='tasks' should invoke _get_all_tasks_from_ticktick."""
        mock_client.sync = MagicMock()
        mock_client.state = {"projects": [], "tags": []}
        mock_client.inbox_id = "inbox123"

        fake_tasks = [{"id": "t1"}, {"id": "t2"}]
        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ), patch(
            "ticktick_mcp.tools.task_tools._get_all_tasks_from_ticktick",
            return_value=fake_tasks,
        ) as mock_getter:
            result = run(ticktick_get_all(search="tasks"))

        mock_getter.assert_called_once()
        # NOTE: known BUG - the "tasks" branch computes `all_items` but never
        # returns it. The function falls off the end of the try block, and
        # because it isn't wrapped in format_response, the tool returns
        # Python None (not the string "null"). Characterised as-is.
        assert result is None

    def test_projects_search_returns_inbox_plus_state(self, mock_client):
        """search='projects' returns inbox + state['projects']."""
        mock_client.sync = MagicMock()
        mock_client.inbox_id = "inbox123"
        mock_client.state = {"projects": [{"id": "p1", "name": "Work"}], "tags": []}

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_get_all(search="projects"))

        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert parsed[0] == {"id": "inbox123", "name": "Inbox"}
        assert parsed[1] == {"id": "p1", "name": "Work"}

    def test_tags_search_returns_state_tags(self, mock_client):
        """search='tags' returns state['tags']."""
        mock_client.sync = MagicMock()
        mock_client.state = {"projects": [], "tags": [{"name": "foo"}]}
        mock_client.inbox_id = "inbox123"

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_get_all(search="tags"))

        parsed = json.loads(result)
        assert parsed == [{"name": "foo"}]

    def test_invalid_search_returns_error(self, mock_client):
        """An unknown search type returns an error dict."""
        mock_client.sync = MagicMock()
        mock_client.state = {"projects": [], "tags": []}
        mock_client.inbox_id = "inbox123"

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_get_all(search="habits"))

        parsed = json.loads(result)
        assert "error" in parsed
        assert "Invalid search type" in parsed["error"]

    def test_search_is_case_insensitive(self, mock_client):
        """Mixed-case 'PROJECTS' should hit the projects branch."""
        mock_client.sync = MagicMock()
        mock_client.inbox_id = "inbox123"
        mock_client.state = {"projects": [{"id": "p1", "name": "Work"}], "tags": []}

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_get_all(search="PROJECTS"))

        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert parsed[0]["name"] == "Inbox"

    def test_exception_returns_error(self, mock_client):
        """An exception (e.g. sync raises) is caught and returned as an error."""
        mock_client.sync = MagicMock(side_effect=Exception("sync failed"))

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(ticktick_get_all(search="projects"))

        parsed = json.loads(result)
        assert "error" in parsed
        assert "sync failed" in parsed["error"]
