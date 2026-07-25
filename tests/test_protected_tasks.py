"""Tests for the protected-task-ID guard.

Some tasks must never be mutated by an agent. TICKTICK_MCP_PROTECTED_TASK_IDS
names them; every mutating tool refuses before issuing any network call, while
reads are untouched. Unset means no protection, so the default behaviour of the
server is unchanged.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.tools import task_tools
from ticktick_mcp.tools.task_tools import (
    TaskObject,
    ticktick_complete_task,
    ticktick_delete_tasks,
    ticktick_get_by_id,
    ticktick_make_subtask,
    ticktick_move_task,
    update_task,
)

PROTECTED = "aaaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "bbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.fixture
def mock_client():
    """A client that records whether any network-facing call was made."""
    client = MagicMock()
    client.get_by_id.return_value = {"id": PROTECTED, "title": "Protected", "projectId": "p1"}
    client.task.update.return_value = {"id": PROTECTED}
    client.task.complete.return_value = {"id": PROTECTED}
    client.task.delete.return_value = [{"id": PROTECTED}]
    client.task.move.return_value = {"id": PROTECTED}
    client.task.make_subtask.return_value = {"id": PROTECTED}
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
        return_value=client,
    ):
        yield client


@pytest.fixture
def protect(monkeypatch):
    """Enable protection for PROTECTED only."""
    monkeypatch.setattr(task_tools, "PROTECTED_TASK_IDS", frozenset({PROTECTED}))


@pytest.fixture
def no_protection(monkeypatch):
    monkeypatch.setattr(task_tools, "PROTECTED_TASK_IDS", frozenset())


def _refused(result):
    data = json.loads(result)
    return data.get("outcome") == "protected_task"


# --- I1: every single-target mutation refuses, with no network call -----------


def test_update_refuses_protected(mock_client, protect):
    result = asyncio.run(update_task(TaskObject(id=PROTECTED, projectId="p1", title="x")))
    assert _refused(result)
    mock_client.task.update.assert_not_called()


def test_update_refuses_protected_when_passed_a_raw_dict(mock_client, protect):
    """The guard runs before dict->TaskObject normalisation, so it must read
    the id from either shape; otherwise passing a dict bypasses protection."""
    result = asyncio.run(update_task({"id": PROTECTED, "projectId": "p1", "title": "x"}))
    assert _refused(result)
    mock_client.task.update.assert_not_called()


def test_complete_refuses_protected(mock_client, protect):
    result = asyncio.run(ticktick_complete_task(PROTECTED))
    assert _refused(result)
    mock_client.task.complete.assert_not_called()


def test_move_refuses_protected(mock_client, protect):
    result = asyncio.run(ticktick_move_task(PROTECTED, "p2"))
    assert _refused(result)
    mock_client.task.move.assert_not_called()


# --- I5: make_subtask protects BOTH ends -------------------------------------


def test_make_subtask_refuses_protected_child(mock_client, protect):
    result = asyncio.run(ticktick_make_subtask(OTHER, PROTECTED))
    assert _refused(result)
    mock_client.task.make_subtask.assert_not_called()


def test_make_subtask_refuses_protected_parent(mock_client, protect):
    result = asyncio.run(ticktick_make_subtask(PROTECTED, OTHER))
    assert _refused(result)
    mock_client.task.make_subtask.assert_not_called()


# --- I2: a batch delete containing a protected id refuses ENTIRELY -----------


def test_delete_refuses_whole_batch_when_one_is_protected(mock_client, protect):
    result = asyncio.run(ticktick_delete_tasks([OTHER, PROTECTED]))
    assert _refused(result)
    mock_client.task.delete.assert_not_called()


def test_delete_allows_batch_with_no_protected_ids(mock_client, protect):
    asyncio.run(ticktick_delete_tasks([OTHER]))
    assert mock_client.task.delete.called


# --- I3: reads are unaffected -------------------------------------------------


def test_read_of_protected_task_still_works(mock_client, protect):
    result = asyncio.run(ticktick_get_by_id(PROTECTED))
    assert not _refused(result)


# --- I4: unset config is a no-op (the public default) ------------------------


def test_no_protection_configured_allows_mutation(mock_client, no_protection):
    asyncio.run(ticktick_complete_task(PROTECTED))
    assert mock_client.task.complete.called


# --- the refusal has to be actionable ----------------------------------------


def test_refusal_names_the_id_and_the_env_var(mock_client, protect):
    data = json.loads(asyncio.run(ticktick_complete_task(PROTECTED)))
    blob = json.dumps(data)
    assert PROTECTED in blob
    assert "TICKTICK_MCP_PROTECTED_TASK_IDS" in blob
