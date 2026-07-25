"""Every outcome of ``ticktick_complete_task`` should be readable from one field.

Two of the three success paths tagged themselves with ``outcome`` and the most
common one -- task completed and refetched with status 2 -- tagged nothing, so a
caller could not branch on the field without also parsing warnings. A recurring
completion in particular comes back at status 0, which reads as failure to
anyone who checks status instead.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.tools.task_tools import ticktick_complete_task

TASK = "aaaaaaaaaaaaaaaaaaaaaaaa"
OPEN_TASK = {"id": TASK, "projectId": "p1", "title": "T", "status": 0}
DONE_TASK = {"id": TASK, "projectId": "p1", "title": "T", "status": 2}


@pytest.fixture
def mock_client():
    client = MagicMock()
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
        return_value=client,
    ):
        yield client


def _run(client, before, after):
    client.get_by_id.side_effect = [before, after]
    return json.loads(asyncio.run(ticktick_complete_task(TASK)))


# --- K1: every success path carries an outcome -------------------------------


def test_ordinary_completion_is_tagged(mock_client):
    """The common path. Previously returned the refetched task untagged, so a
    caller reading `outcome` saw nothing on the most frequent success."""
    assert _run(mock_client, OPEN_TASK, DONE_TASK)["outcome"] == "completed"


def test_completion_that_leaves_the_active_list_is_tagged(mock_client):
    assert _run(mock_client, OPEN_TASK, {})["outcome"] == "completed"


def test_recurring_rollforward_is_tagged(mock_client):
    recurring_before = {**OPEN_TASK, "repeatFlag": "RRULE:FREQ=WEEKLY"}
    result = _run(mock_client, recurring_before, {**OPEN_TASK, "status": 0})
    assert result["outcome"] == "completed_recurring"


# --- K2: the recurring payload keeps its follow-on id ------------------------


def test_recurring_rollforward_still_reports_the_next_occurrence(mock_client):
    recurring_before = {**OPEN_TASK, "repeatFlag": "RRULE:FREQ=WEEKLY"}
    result = _run(mock_client, recurring_before, {**OPEN_TASK, "status": 0})
    assert result["next_occurrence_id"] == TASK


# --- an unexpected state is not labelled a success ---------------------------


def test_still_open_after_completing_is_not_called_completed(mock_client):
    """Non-recurring and still status 0: something is wrong, and saying
    'completed' would assert a success we cannot back."""
    result = _run(mock_client, OPEN_TASK, {**OPEN_TASK, "status": 0})
    assert result["outcome"] == "uncertain"
    assert "_verification_warnings" in result


# --- K3: failure paths carry no success outcome ------------------------------


def test_missing_task_is_not_tagged_as_a_success(mock_client):
    mock_client.get_by_id.return_value = {}
    result = json.loads(asyncio.run(ticktick_complete_task(TASK)))
    assert result["status"] == "not_found"
    assert result.get("outcome") not in {"completed", "completed_recurring"}


def test_exception_is_not_tagged_as_a_success(mock_client):
    mock_client.get_by_id.side_effect = RuntimeError("boom")
    result = json.loads(asyncio.run(ticktick_complete_task(TASK)))
    assert result["status"] == "error"
    assert result.get("outcome") not in {"completed", "completed_recurring"}


# --- the warning channel and the outcome field stay independent --------------


def test_the_leaves_active_list_note_survives_alongside_the_outcome(mock_client):
    """The existing note is pinned by tests/test_task_tools_misc.py and stays
    as-is. It reads as a failure on a path the code calls normal success, but
    `outcome` now answers that question on its own, so a caller never has to
    interpret the warning text to know what happened."""
    result = _run(mock_client, OPEN_TASK, {})
    assert result["outcome"] == "completed"
    assert result.get("_verification_warnings")
