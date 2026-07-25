"""Warnings ``create_task`` raises about tasks that will not behave as expected.

A task with no ``due_date`` already warns. An all-day task has a date but still
never fires a timed reminder, which reads as a working reminder right up until
the day it does not arrive.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.compact import CONTENT_PREVIEW_CHARS
from ticktick_mcp.tools.task_tools import ticktick_create_task

DUE = "2027-03-15T20:45:00+00:00"


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.task.builder.return_value = {"title": "T"}
    client.task.create.return_value = {"id": "t1", "title": "T"}
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
        return_value=client,
    ):
        yield client


def _warnings(result):
    return json.loads(result).get("_verification_warnings", [])


def _has(result, needle):
    return any(needle in w for w in _warnings(result))


def test_all_day_task_warns_it_will_not_remind(mock_client):
    result = asyncio.run(
        ticktick_create_task(title="T", due_date=DUE, expected_day_of_week="Monday", all_day=True)
    )
    assert _has(result, "all-day")


def test_timed_task_does_not_warn(mock_client):
    result = asyncio.run(
        ticktick_create_task(title="T", due_date=DUE, expected_day_of_week="Monday")
    )
    assert not _has(result, "all-day")


def test_all_day_warning_is_separate_from_the_dateless_one(mock_client):
    """A dateless task must not pick up the all-day wording, and vice versa -
    they are different failures with different fixes."""
    dateless = asyncio.run(ticktick_create_task(title="T"))
    assert _has(dateless, "No due_date set")
    assert not _has(dateless, "all-day")


def test_all_day_false_is_not_treated_as_all_day(mock_client):
    result = asyncio.run(
        ticktick_create_task(title="T", due_date=DUE, expected_day_of_week="Monday", all_day=False)
    )
    assert not _has(result, "all-day")


# --- title characters TickTick parses as markers -----------------------------


@pytest.mark.parametrize(
    "title",
    [
        "MCB #5",
        "Check #kitchen tap",
        "Ask @joy about it",
        "Paint fence ~2h",
    ],
)
def test_marker_characters_in_title_warn(mock_client, title):
    result = asyncio.run(
        ticktick_create_task(title=title, due_date=DUE, expected_day_of_week="Monday")
    )
    assert _has(result, "parsed by TickTick")


@pytest.mark.parametrize(
    "title",
    [
        "Fix C# build",
        "Email support@ the vendor",
        "Rewire MCB 5",
        "Budget ~ the usual",
        "Read chapter 3",
    ],
)
def test_ordinary_titles_do_not_warn(mock_client, title):
    """The marker only binds to a following word, so a bare '#' or a trailing
    '@' is ordinary prose and must stay quiet or the warning gets ignored."""
    result = asyncio.run(
        ticktick_create_task(title=title, due_date=DUE, expected_day_of_week="Monday")
    )
    assert not _has(result, "parsed by TickTick")


def test_marker_warning_names_the_character_found(mock_client):
    result = asyncio.run(
        ticktick_create_task(title="MCB #5", due_date=DUE, expected_day_of_week="Monday")
    )
    assert any("#" in w for w in _warnings(result))


# --- content longer than the compact preview window --------------------------


def test_content_beyond_the_preview_window_warns(mock_client):
    """Compact reads are the default, so content past the preview is invisible
    to any later keyword search."""
    result = asyncio.run(
        ticktick_create_task(
            title="T",
            content="x" * (CONTENT_PREVIEW_CHARS + 1),
            due_date=DUE,
            expected_day_of_week="Monday",
        )
    )
    assert _has(result, "compact")


def test_content_within_the_preview_window_does_not_warn(mock_client):
    result = asyncio.run(
        ticktick_create_task(
            title="T",
            content="x" * CONTENT_PREVIEW_CHARS,
            due_date=DUE,
            expected_day_of_week="Monday",
        )
    )
    assert not _has(result, "compact")


def test_no_content_does_not_warn(mock_client):
    result = asyncio.run(
        ticktick_create_task(title="T", due_date=DUE, expected_day_of_week="Monday")
    )
    assert not _has(result, "compact")


def test_threshold_tracks_the_shared_constant_not_a_copy(mock_client):
    """The boundary must come from compact.py. A duplicated literal drifts the
    day the preview window changes, and the warning goes quiet or cries wolf."""
    from ticktick_mcp import compact

    assert CONTENT_PREVIEW_CHARS is compact.CONTENT_PREVIEW_CHARS
