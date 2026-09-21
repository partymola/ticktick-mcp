"""Reopening a task drops the record of its earlier completion.

A completion recorded by ``ticktick_complete_task`` keeps that task id out of
``ticktick_get_unprocessed_completions`` permanently. Reopening the task makes a
later completion news again, and that one may well be made in the app with a
note attached, so the old record must not go on filtering it out.
"""

import asyncio
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.completion_db import init_db, is_processed, mark_processed
from ticktick_mcp.tools.task_tools import update_task

TASK = "aaaaaaaaaaaaaaaaaaaaaaaa"
PROJECT = "pppppppppppppppppppppppp"
SIBLING_TASK = "bbbbbbbbbbbbbbbbbbbbbbbb"
OTHER_TASK = "cccccccccccccccccccccccc"
OTHER_PROJECT = "oooooooooooooooooooooooo"
COMPLETED_TASK = {"id": TASK, "projectId": PROJECT, "title": "Fix the tap", "status": 2}
RECURRING_TASK = {**COMPLETED_TASK, "status": 0, "repeatFlag": "RRULE:FREQ=WEEKLY"}


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.state = {"tasks": [], "projects": [{"id": PROJECT, "name": "Home"}]}
    client.inbox_id = "inbox1"
    client.get_by_id.return_value = COMPLETED_TASK
    client.task.update.return_value = {"id": TASK, "projectId": PROJECT, "status": 0}
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
        return_value=client,
    ):
        yield client


@pytest.fixture
def recorded():
    init_db()
    mark_processed(TASK, PROJECT, "Fix the tap", None, notes="completed via ticktick_complete_task")
    assert is_processed(TASK) is True


def _update(fields):
    return json.loads(asyncio.run(update_task({"id": TASK, "projectId": PROJECT, **fields})))


def _clear_warnings(result):
    return [w for w in result.get("_verification_warnings") or [] if "could not be cleared" in w]


def test_reopening_a_task_clears_its_completion_record(mock_client, recorded):
    result = _update({"status": 0})

    assert is_processed(TASK) is False
    # A clear that worked says nothing. Warning on every reopen would have a
    # caller acting on a problem that is not there.
    assert _clear_warnings(result) == []


def test_an_update_that_does_not_reopen_leaves_the_record(mock_client, recorded):
    _update({"priority": 5})

    assert is_processed(TASK) is True


def test_completing_through_an_update_leaves_the_record(mock_client, recorded):
    """``status: 2`` is the opposite move, and the record still describes it."""
    _update({"status": 2})

    assert is_processed(TASK) is True


def test_a_refused_reopen_leaves_the_record(mock_client, recorded):
    """A recurring task already rolled forward is refused before anything is
    changed, so the clear must sit behind that refusal rather than in front."""
    mock_client.get_by_id.return_value = RECURRING_TASK

    result = _update({"status": 0})

    assert result["outcome"] == "reopen_no_effect"
    assert is_processed(TASK) is True


def test_the_record_is_cleared_even_when_the_update_then_fails(mock_client, recorded):
    """The clear sits before the POST. An update that fails afterwards costs one
    completion reappearing in the queue, where clearing after a successful POST
    would lose a note on every update that did not come back."""
    mock_client.task.update.side_effect = RuntimeError("boom")

    result = _update({"status": 0})

    assert result["status"] == "error"
    assert is_processed(TASK) is False


def test_clearing_one_record_leaves_every_other_alone(mock_client, recorded):
    """The delete is keyed on this task alone. The sibling row sits in the SAME
    project, because that is the predicate a maintainer is most likely to reach
    for while wiring a delete against a queue that filters per project, and a
    row in another project would survive it."""
    mark_processed(SIBLING_TASK, PROJECT, "Same project", None)
    mark_processed(OTHER_TASK, OTHER_PROJECT, "Another project", None)

    _update({"status": 0})

    assert is_processed(TASK) is False
    assert is_processed(SIBLING_TASK) is True
    assert is_processed(OTHER_TASK) is True


def test_a_clear_that_quietly_removes_nothing_is_reported(mock_client, recorded):
    """``clear_processed`` raising is not the only way it fails to clear: a
    predicate that matches nothing returns cleanly. The read-back is what tells
    the caller, and without it the reopen reads as clean."""
    with patch("ticktick_mcp.tools.task_tools.clear_processed"):
        result = _update({"status": 0})

    assert is_processed(TASK) is True
    assert _clear_warnings(result)


def test_a_store_that_has_never_been_created_clears_without_complaint(
    mock_client, tmp_path, monkeypatch
):
    """A reopen can be the first thing on an install that touches the store.
    Without ``init_db`` the DELETE hits no table and reports a failure to clear
    a record that never existed."""
    import ticktick_mcp.completion_db as db_module

    monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "never-created.db")

    result = _update({"status": 0})

    assert _clear_warnings(result) == []


def test_a_store_that_cannot_be_opened_does_not_fail_the_update(
    mock_client, recorded, tmp_path, monkeypatch, caplog
):
    """The same requirement as the write side, at the second call site: the
    realistic failure is a missing or read-only config directory, where
    ``init_db`` raises ``sqlite3.OperationalError``, which is not an
    ``OSError``. A narrower except here, or an ``init_db`` hoisted out of the
    try, fails a reopen over local housekeeping that TickTick knows nothing
    about."""
    import ticktick_mcp.completion_db as db_module

    monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "no-such-dir" / "completions.db")

    with caplog.at_level(logging.ERROR):
        result = _update({"status": 0})

    assert mock_client.task.update.called
    assert result.get("status") != "error"
    assert "Could not clear the completion record" in caplog.text
    assert any("could not be cleared" in w for w in result["_verification_warnings"])


def test_a_clear_that_raises_something_else_does_not_fail_the_update(mock_client, recorded):
    """The same property one layer up, for a failure type the store itself does
    not produce."""
    with patch(
        "ticktick_mcp.tools.task_tools.clear_processed",
        side_effect=OSError("disk is full"),
    ):
        result = _update({"status": 0})

    assert mock_client.task.update.called
    assert result.get("status") != "error"
    assert _clear_warnings(result)


@pytest.mark.parametrize(
    ("recheck_status", "outcome"),
    [(0, "updated"), (2, "no_op")],
)
def test_the_warning_survives_an_update_that_echoed_nothing(
    mock_client, recorded, recheck_status, outcome
):
    """The empty-echo branches build their own result rather than adding to the
    ordinary one, so each has to attach the warning itself. They matter more
    than the ordinary path, not less: a status-0 update that cannot be routed
    is exactly where the reopen silently no-ops, which leaves the caller
    holding the stale record this warning is about."""
    mock_client.task.update.return_value = {}
    mock_client.get_by_id.side_effect = [
        COMPLETED_TASK,
        {**COMPLETED_TASK, "status": recheck_status},
    ]

    with patch(
        "ticktick_mcp.tools.task_tools.clear_processed",
        side_effect=OSError("disk is full"),
    ):
        result = _update({"status": 0})

    assert result["outcome"] == outcome
    assert _clear_warnings(result)
