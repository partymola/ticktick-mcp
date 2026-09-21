"""A completion made through this server is recorded as processed by it.

The unprocessed-completions queue exists to surface completions made
elsewhere, in the app or on a phone widget, where a note may be waiting to be
read. A completion made through ``ticktick_complete_task`` carries no such
note, so leaving it in the queue costs a later caller a fetch and a judgement
about content it wrote itself.

Recording it is deliberately narrower than "the call succeeded": only a
completion whose own id is the one the queue will show is recorded.
"""

import asyncio
import datetime
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.completion_db import (
    _connect,
    get_processed_ids_for_project,
    init_db,
    is_processed,
    mark_processed,
)
from ticktick_mcp.tools import task_tools
from ticktick_mcp.tools.task_tools import ticktick_complete_task

TASK = "aaaaaaaaaaaaaaaaaaaaaaaa"
PROJECT = "pppppppppppppppppppppppp"
OTHER_PROJECT = "oooooooooooooooooooooooo"
OPEN_TASK = {"id": TASK, "projectId": PROJECT, "title": "Fix the tap", "status": 0}
DONE_TASK = {**OPEN_TASK, "status": 2, "completedTime": "2026-09-21T19:00:00.000+0000"}
RECURRING_TASK = {**OPEN_TASK, "repeatFlag": "RRULE:FREQ=WEEKLY"}


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.state = {"tasks": [], "projects": [{"id": PROJECT, "name": "Home"}]}
    client.inbox_id = "inbox1"
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
        return_value=client,
    ):
        yield client


def _complete(client, before, after):
    client.get_by_id.side_effect = [before, after]
    return json.loads(asyncio.run(ticktick_complete_task(TASK)))


# --- the two terminal paths record, under the task's own project -------------


def test_a_completed_task_is_recorded(mock_client):
    result = _complete(mock_client, OPEN_TASK, DONE_TASK)

    assert result["outcome"] == "completed"
    assert result["completion_recorded"] is True
    assert is_processed(TASK) is True


def test_a_completion_that_leaves_the_active_list_is_recorded(mock_client):
    """The task cannot be refetched, so the row has to be built from the object
    read before completing rather than from the response."""
    result = _complete(mock_client, OPEN_TASK, {})

    assert result["outcome"] == "completed"
    assert result["completion_recorded"] is True
    assert is_processed(TASK) is True


def test_the_row_is_keyed_on_the_project_the_task_is_in(mock_client):
    """``ticktick_get_unprocessed_completions`` filters the store by exact
    project id, so a row written under any other value leaves the task in the
    queue while looking recorded."""
    _complete(mock_client, OPEN_TASK, DONE_TASK)

    assert get_processed_ids_for_project(PROJECT) == {TASK}
    assert get_processed_ids_for_project(OTHER_PROJECT) == set()


def test_the_row_carries_the_title_and_the_completion_time(mock_client):
    """The store is read by a person as well as by the queue filter."""
    _complete(mock_client, OPEN_TASK, DONE_TASK)

    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM completion_tracking WHERE task_id = ?", (TASK,)
        ).fetchone()

    assert row["title"] == "Fix the tap"
    assert row["completed_time"] == DONE_TASK["completedTime"]
    # The provenance the reader needs: this row was not written by an agent
    # working through the queue.
    assert row["notes"] == "completed via ticktick_complete_task"


def test_a_store_that_has_never_been_created_is_created(mock_client, tmp_path, monkeypatch):
    """A completion can be the first thing that ever touches the store: on a
    fresh install neither completion-tracking tool need have run."""
    import ticktick_mcp.completion_db as db_module

    monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "never-created.db")

    result = _complete(mock_client, OPEN_TASK, DONE_TASK)

    assert result["completion_recorded"] is True
    assert is_processed(TASK) is True


# --- the queue is the property; the rows above are proxies for it ------------


def test_the_completed_task_no_longer_reaches_the_unprocessed_queue(mock_client):
    from ticktick_mcp.tools.completion_tools import ticktick_get_unprocessed_completions

    # TickTick stamps completions in UTC. Backdated a few hours so the window
    # holds it whatever the host's offset from UTC is.
    stamped = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=6)
    completed_record = {
        **DONE_TASK,
        "completedTime": stamped.strftime("%Y-%m-%dT%H:%M:%S.000+0000"),
    }
    mock_client.task.get_completed.return_value = [completed_record]

    with (
        patch(
            "ticktick_mcp.tools.completion_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ),
        patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ),
    ):
        before = json.loads(asyncio.run(ticktick_get_unprocessed_completions(PROJECT, days=30)))
        _complete(mock_client, OPEN_TASK, DONE_TASK)
        after = json.loads(asyncio.run(ticktick_get_unprocessed_completions(PROJECT, days=30)))

    assert [t["id"] for t in before] == [TASK]
    assert after == []


# --- nothing else records ----------------------------------------------------


def test_a_recurring_rollforward_does_not_record_the_series_id(mock_client):
    """Completing a recurring task rolls the same id forward and files the
    completed instance under a new id, so the series id is not the one the
    queue shows and a row under it is keyed on the wrong one."""
    result = _complete(mock_client, RECURRING_TASK, {**OPEN_TASK, "status": 0})

    assert result["outcome"] == "completed_recurring"
    assert "completion_recorded" not in result
    assert is_processed(TASK) is False


@pytest.mark.parametrize("refetched", [DONE_TASK, {}], ids=["status-2", "left-the-list"])
def test_a_recurring_task_records_nothing_however_it_comes_back(mock_client, refetched):
    """The roll-forward is not the only shape a recurring completion takes: one
    can also refetch at status 2, or leave the active list. Recurrence is the
    discriminator, not the outcome, so none of them records."""
    result = _complete(mock_client, RECURRING_TASK, refetched)

    assert result["outcome"] == "completed"
    assert "completion_recorded" not in result
    assert is_processed(TASK) is False


def test_a_task_still_open_afterwards_is_not_recorded(mock_client):
    result = _complete(mock_client, OPEN_TASK, {**OPEN_TASK, "status": 0})

    assert result["outcome"] == "uncertain"
    assert "completion_recorded" not in result
    assert is_processed(TASK) is False


def test_a_task_that_was_not_found_is_not_recorded(mock_client):
    mock_client.get_by_id.return_value = {}

    result = json.loads(asyncio.run(ticktick_complete_task(TASK)))

    assert result["status"] == "not_found"
    assert is_processed(TASK) is False


def test_a_failed_completion_is_not_recorded(mock_client):
    mock_client.get_by_id.side_effect = RuntimeError("boom")

    result = json.loads(asyncio.run(ticktick_complete_task(TASK)))

    assert result["status"] == "error"
    assert is_processed(TASK) is False


def test_a_protected_task_is_not_recorded(mock_client, monkeypatch):
    monkeypatch.setattr(task_tools, "PROTECTED_TASK_IDS", frozenset({TASK}))

    result = json.loads(asyncio.run(ticktick_complete_task(TASK)))

    assert result["outcome"] == "protected_task"
    assert is_processed(TASK) is False


def test_a_row_under_another_project_is_not_reported_as_recorded(mock_client):
    """The queue asks per project, so a row left under a project the task has
    since moved out of suppresses nothing. ``mark_processed`` sees the id as a
    duplicate and writes no new row, and reading the store back by id alone
    would report that as recorded."""
    mark_processed(TASK, OTHER_PROJECT, "Fix the tap", None)

    result = _complete(mock_client, OPEN_TASK, DONE_TASK)

    assert result["outcome"] == "completed"
    assert result["completion_recorded"] is False
    assert get_processed_ids_for_project(PROJECT) == set()


# --- recording is not allowed to cost the completion -------------------------


def test_a_store_that_cannot_be_opened_still_reports_the_completion(
    mock_client, tmp_path, monkeypatch, caplog
):
    """The realistic failure, and the one the store raises first: a config
    directory that is missing or read-only. ``init_db`` raises
    ``sqlite3.OperationalError`` there, which is not an ``OSError``, so a
    narrower except or an ``init_db`` hoisted out of the try turns a task that
    is already complete into an error and sends the caller back to redo it."""
    import ticktick_mcp.completion_db as db_module

    monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "no-such-dir" / "completions.db")

    with caplog.at_level(logging.ERROR):
        result = _complete(mock_client, OPEN_TASK, DONE_TASK)

    assert result["outcome"] == "completed"
    assert result["completion_recorded"] is False
    assert "Could not record completion" in caplog.text


def test_a_write_that_quietly_does_nothing_is_not_reported_as_recorded(mock_client):
    """``mark_processed`` swallows every ``IntegrityError`` as a duplicate, so
    a row it never wrote returns without raising. Reporting the write from the
    absence of an exception would claim a record the store does not hold."""
    with patch("ticktick_mcp.tools.task_tools.mark_processed"):
        result = _complete(mock_client, OPEN_TASK, DONE_TASK)

    assert result["outcome"] == "completed"
    assert result["completion_recorded"] is False
    assert is_processed(TASK) is False


def test_a_write_that_raises_something_else_still_reports_the_completion(mock_client):
    """The same property one layer up, for a failure type the store itself
    does not produce."""
    with patch(
        "ticktick_mcp.tools.task_tools.mark_processed",
        side_effect=OSError("disk is full"),
    ):
        result = _complete(mock_client, OPEN_TASK, DONE_TASK)

    assert result["outcome"] == "completed"
    assert result["completion_recorded"] is False
    assert is_processed(TASK) is False


def test_a_second_completion_of_the_same_id_is_not_an_error(mock_client):
    """``mark_processed`` treats a duplicate as a no-op, and the tool must not
    report that as a failure to record."""
    init_db()
    first = _complete(mock_client, OPEN_TASK, DONE_TASK)
    second = _complete(mock_client, OPEN_TASK, DONE_TASK)

    assert first["completion_recorded"] is True
    assert second["completion_recorded"] is True
