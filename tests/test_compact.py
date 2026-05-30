"""Tests for compact list rendering (src/ticktick_mcp/compact.py).

Compact mode is the default for the list-returning tools. It must:
  * drop the heavy content/desc/items blobs and add a contentPreview,
  * keep a 65-task project comfortably under the size budget (the real
    money project overflowed the MCP result cap in full mode),
  * never drop a task silently -- over-budget results keep the soonest-due
    tasks and append an explicit truncation note,
  * reproduce the pre-compact output byte-for-byte when detail="full".

All task data here is SYNTHETIC. The repo is public; no real account data
may live in the tests. ``_make_task`` reproduces the field shape and size
profile of a real TickTick task so the size assertions are meaningful.
"""

import asyncio
import datetime
import json
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.compact import (
    CONTENT_PREVIEW_CHARS,
    DEFAULT_CHAR_BUDGET,
    compact_task,
    normalise_detail,
    render_task_list,
)
from ticktick_mcp.helpers import format_response
from ticktick_mcp.tools.filter_tools import ticktick_filter_tasks
from ticktick_mcp.tools.task_tools import ticktick_get_tasks_from_project


def run(coro):
    return asyncio.run(coro)


# Fields the heavy blobs the real payload carried; compact must drop them.
HEAVY_FIELDS = ("content", "desc", "items")
# Light fields compact must keep.
KEEP_FIELDS = (
    "id",
    "projectId",
    "title",
    "dueDate",
    "startDate",
    "priority",
    "status",
    "isAllDay",
    "timeZone",
    "tags",
)


def _make_task(i: int, *, content_len: int = 700, due="2026-06-15T20:45:00.000+0000"):
    """Build one synthetic task with the same field shape as a real one.

    The metadata fields (etag, modifiedTime, creator, ...) reproduce the
    bulk a real task carries so the full-vs-compact size drop is realistic.
    """
    marker = f"TASK{i:04d}MARKER"
    # Marker sits at the very start so it survives the contentPreview cut.
    body = f"{marker} " + ("lorem ipsum dolor sit amet consectetur " * 40)
    return {
        "id": f"{i:024x}",
        "projectId": "1" * 24,
        "sortOrder": -43980465111040 - i,
        "title": f"Synthetic task number {i}",
        "content": body[:content_len],
        "desc": "",
        "startDate": due,
        "dueDate": due,
        "timeZone": "Europe/London",
        "isFloating": False,
        "isAllDay": False,
        "reminders": [],
        "priority": i % 6,
        "status": 0,
        "items": [],
        "modifiedTime": "2026-05-29T12:34:56.000+0000",
        "etag": f"etag{i:08x}",
        "deleted": 0,
        "createdTime": "2026-01-01T09:00:00.000+0000",
        "creator": 123456789,
        "kind": "TEXT",
        "tags": ["finance"] if i % 7 == 0 else [],
    }


# ---------------------------------------------------------------------------
# normalise_detail
# ---------------------------------------------------------------------------


class TestNormaliseDetail:
    def test_default_none_is_compact(self):
        assert normalise_detail(None) == "compact"

    def test_passthrough_compact_full(self):
        assert normalise_detail("compact") == "compact"
        assert normalise_detail("full") == "full"

    def test_case_insensitive_and_trimmed(self):
        assert normalise_detail("  FULL ") == "full"
        assert normalise_detail("Compact") == "compact"

    def test_invalid_raises_value_error(self):
        with pytest.raises(ValueError):
            normalise_detail("compatc")
        with pytest.raises(ValueError):
            normalise_detail(123)


# ---------------------------------------------------------------------------
# compact_task
# ---------------------------------------------------------------------------


class TestCompactTask:
    def test_drops_heavy_fields(self):
        out = compact_task(_make_task(1))
        for heavy in HEAVY_FIELDS:
            assert heavy not in out

    def test_keeps_light_fields(self):
        task = _make_task(1)
        out = compact_task(task)
        for field in KEEP_FIELDS:
            assert out[field] == task[field]

    def test_drops_bulky_metadata(self):
        out = compact_task(_make_task(1))
        for noise in ("etag", "modifiedTime", "createdTime", "creator", "sortOrder", "kind"):
            assert noise not in out

    def test_content_preview_truncates_long_content(self):
        out = compact_task(_make_task(1, content_len=700))
        assert out["contentPreview"].startswith("TASK0001MARKER")
        assert out["contentPreview"].endswith("...")
        # 200 chars + the "..." marker.
        assert len(out["contentPreview"]) == CONTENT_PREVIEW_CHARS + 3

    def test_content_preview_short_content_not_truncated(self):
        task = _make_task(1)
        task["content"] = "short note"
        out = compact_task(task)
        assert out["contentPreview"] == "short note"

    def test_content_preview_empty_when_no_content(self):
        task = _make_task(1)
        task["content"] = ""
        assert compact_task(task)["contentPreview"] == ""
        task.pop("content")
        assert compact_task(task)["contentPreview"] == ""

    def test_preserves_verification_warnings(self):
        task = _make_task(1)
        task["_verification_warnings"] = ["something"]
        assert compact_task(task)["_verification_warnings"] == ["something"]


# ---------------------------------------------------------------------------
# render_task_list -- full mode is byte-for-byte identical to the old output
# ---------------------------------------------------------------------------


class TestRenderFullMode:
    def test_full_reproduces_legacy_output_exactly(self):
        tasks = [_make_task(i) for i in range(10)]
        assert render_task_list(tasks, detail="full") == format_response(tasks)

    def test_full_empty_list(self):
        assert render_task_list([], detail="full") == format_response([])


# ---------------------------------------------------------------------------
# render_task_list -- compact, the 65-task size drop (acceptance criterion)
# ---------------------------------------------------------------------------


class TestCompactSizeDrop:
    def test_65_task_project_drops_under_budget(self):
        tasks = [_make_task(i) for i in range(65)]
        full = format_response(tasks)
        compact = render_task_list(tasks, detail="compact")

        # Full overflowed the MCP cap; compact must fit the budget.
        assert len(full) > 70_000
        assert len(compact) < DEFAULT_CHAR_BUDGET
        assert len(compact) < len(full) * 0.5

        parsed = json.loads(compact)
        assert isinstance(parsed, list)
        # 65 tasks fit under budget -> plain list, no truncation note.
        assert len(parsed) == 65
        assert all("_truncation_note" not in t for t in parsed)

    def test_compact_keyword_search_still_works(self):
        tasks = [_make_task(i) for i in range(65)]
        parsed = json.loads(render_task_list(tasks, detail="compact"))
        # The unique marker lives in contentPreview, so keyword search over
        # the compact list still finds the task.
        hits = [t for t in parsed if "TASK0042MARKER" in t.get("contentPreview", "")]
        assert len(hits) == 1
        assert hits[0]["title"] == "Synthetic task number 42"

    def test_compact_drops_content_field(self):
        parsed = json.loads(render_task_list([_make_task(1)], detail="compact"))
        assert "content" not in parsed[0]
        assert "contentPreview" in parsed[0]


# ---------------------------------------------------------------------------
# render_task_list -- over-budget truncation, no silent drops
# ---------------------------------------------------------------------------


class TestCompactTruncation:
    def _big_set(self, n=200):
        # Distinct, strictly-ascending due dates so "soonest-due first" is
        # observable: task i is due i days after a fixed base date.
        base = datetime.date(2026, 1, 1)
        out = []
        for i in range(n):
            due = (base + datetime.timedelta(days=i)).isoformat()
            t = _make_task(i, due=f"{due}T20:45:00.000+0000")
            out.append(t)
        return out

    def test_over_budget_appends_note_and_accounts_for_all(self):
        tasks = self._big_set(200)
        rendered = render_task_list(tasks, detail="compact")
        assert len(rendered) <= DEFAULT_CHAR_BUDGET

        parsed = json.loads(rendered)
        note = parsed[-1]
        assert "_truncation_note" in note
        returned = parsed[:-1]

        assert note["_total_count"] == 200
        assert note["_returned_count"] == len(returned)
        assert note["_omitted_count"] == 200 - len(returned)
        # Nothing silently dropped: returned + omitted == total.
        assert note["_returned_count"] + note["_omitted_count"] == 200
        assert note["_omitted_count"] > 0

    def test_truncation_keeps_soonest_due_first(self):
        tasks = self._big_set(200)
        parsed = json.loads(render_task_list(tasks, detail="compact"))
        returned = parsed[:-1]
        due_dates = [t["dueDate"] for t in returned]
        # Soonest-due first: the kept set is sorted ascending by dueDate.
        assert due_dates == sorted(due_dates)
        # The earliest task is kept; the latest is omitted.
        earliest = min(t["dueDate"] for t in tasks)
        latest = max(t["dueDate"] for t in tasks)
        assert any(t["dueDate"] == earliest for t in returned)
        assert all(t["dueDate"] != latest for t in returned)

    def test_undated_tasks_sort_last_under_truncation(self):
        tasks = self._big_set(200)
        # Make a couple of tasks undated; they should be the first omitted.
        tasks[0]["dueDate"] = None
        tasks[1].pop("dueDate", None)
        parsed = json.loads(render_task_list(tasks, detail="compact"))
        returned = parsed[:-1]
        undated_returned = [t for t in returned if not t.get("dueDate")]
        # With 200 dated competitors, undated tasks lose and get omitted.
        assert undated_returned == []


# ---------------------------------------------------------------------------
# Tool-level wiring: ticktick_get_tasks_from_project
# ---------------------------------------------------------------------------


class TestGetTasksFromProjectDetail:
    def _client_with(self, tasks):
        c = MagicMock()
        c.task.get_from_project = MagicMock(return_value=tasks)
        return c

    def test_default_is_compact(self):
        tasks = [_make_task(1)]
        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=self._client_with(tasks),
        ):
            result = run(ticktick_get_tasks_from_project(project_id="p1"))
        parsed = json.loads(result)
        assert "content" not in parsed[0]
        assert "contentPreview" in parsed[0]

    def test_full_preserves_content(self):
        tasks = [_make_task(1)]
        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=self._client_with(tasks),
        ):
            result = run(ticktick_get_tasks_from_project(project_id="p1", detail="full"))
        parsed = json.loads(result)
        assert parsed[0]["content"] == tasks[0]["content"]
        assert "contentPreview" not in parsed[0]

    def test_invalid_detail_returns_error(self):
        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=self._client_with([]),
        ):
            result = run(ticktick_get_tasks_from_project(project_id="p1", detail="bogus"))
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Invalid detail" in parsed["error"]


# ---------------------------------------------------------------------------
# Tool-level wiring: ticktick_filter_tasks
# ---------------------------------------------------------------------------


class TestFilterTasksDetail:
    def test_default_is_compact(self):
        tasks = [_make_task(1)]
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=MagicMock(),
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=tasks,
            ),
        ):
            result = run(ticktick_filter_tasks({"status": "uncompleted"}))
        parsed = json.loads(result)
        assert "content" not in parsed[0]
        assert parsed[0]["contentPreview"].startswith("TASK0001MARKER")

    def test_full_preserves_content(self):
        tasks = [_make_task(1)]
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=MagicMock(),
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=tasks,
            ),
        ):
            result = run(ticktick_filter_tasks({"status": "uncompleted"}, detail="full"))
        parsed = json.loads(result)
        assert parsed[0]["content"] == tasks[0]["content"]

    def test_invalid_detail_returns_error(self):
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=MagicMock(),
        ):
            result = run(ticktick_filter_tasks({"status": "uncompleted"}, detail="nope"))
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Invalid detail" in parsed["error"]
