"""Tests for completion_db.py and the ticktick_mark_completion_processed MCP tool."""

import asyncio
import json

import pytest

# --- Helpers ---


def run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# --- Fixtures ---


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    """
    Redirect the DB to a temp directory for every test.
    Resets the module-level _DB_PATH cache between tests.
    """
    import ticktick_mcp.completion_db as db_module

    db_path = tmp_path / "completion_tracking.db"
    original = db_module._DB_PATH
    db_module._DB_PATH = db_path
    yield db_path
    db_module._DB_PATH = original


# =============================================================================
# completion_db.py unit tests
# =============================================================================


class TestInitDb:
    def test_creates_table(self, isolated_db):
        from ticktick_mcp.completion_db import _connect, init_db

        init_db()

        with _connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='completion_tracking'"
            ).fetchall()
        assert len(rows) == 1

    def test_idempotent(self, isolated_db):
        """Calling init_db twice should not raise."""
        from ticktick_mcp.completion_db import init_db

        init_db()
        init_db()  # should not raise


class TestIsProcessed:
    def test_returns_false_for_unknown_task(self, isolated_db):
        from ticktick_mcp.completion_db import init_db, is_processed

        init_db()
        assert is_processed("nonexistent") is False

    def test_returns_true_after_mark(self, isolated_db):
        from ticktick_mcp.completion_db import init_db, is_processed, mark_processed

        init_db()
        mark_processed("task1", "proj1", "Buy milk", "2025-06-01T10:00:00Z")
        assert is_processed("task1") is True


class TestMarkProcessed:
    def test_inserts_row(self, isolated_db):
        from ticktick_mcp.completion_db import _connect, init_db, mark_processed

        init_db()
        mark_processed("task1", "proj1", "My task", "2025-06-01T10:00:00Z", notes="done")

        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM completion_tracking WHERE task_id = ?", ("task1",)
            ).fetchone()

        assert row is not None
        assert row["project_id"] == "proj1"
        assert row["title"] == "My task"
        assert row["completed_time"] == "2025-06-01T10:00:00Z"
        assert row["notes"] == "done"
        assert row["processed_at"] is not None

    def test_duplicate_is_silent(self, isolated_db):
        """Inserting the same task_id twice should not raise - second call is a no-op."""
        from ticktick_mcp.completion_db import _connect, init_db, mark_processed

        init_db()
        mark_processed("task1", "proj1", "First title", None)
        mark_processed("task1", "proj1", "Second title", None)  # should not raise

        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM completion_tracking WHERE task_id = ?", ("task1",)
            ).fetchall()

        assert len(rows) == 1
        assert rows[0]["title"] == "First title"  # original preserved

    def test_optional_fields_accept_none(self, isolated_db):
        from ticktick_mcp.completion_db import _connect, init_db, mark_processed

        init_db()
        mark_processed("task2", "proj1", None, None, None)

        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM completion_tracking WHERE task_id = ?", ("task2",)
            ).fetchone()

        assert row is not None
        assert row["title"] is None
        assert row["completed_time"] is None
        assert row["notes"] is None


class TestGetProcessedIdsForProject:
    def test_returns_empty_set_for_unknown_project(self, isolated_db):
        from ticktick_mcp.completion_db import get_processed_ids_for_project, init_db

        init_db()
        result = get_processed_ids_for_project("proj_unknown")
        assert result == set()

    def test_returns_only_ids_for_given_project(self, isolated_db):
        from ticktick_mcp.completion_db import (
            get_processed_ids_for_project,
            init_db,
            mark_processed,
        )

        init_db()
        mark_processed("task1", "projA", "Task 1", None)
        mark_processed("task2", "projA", "Task 2", None)
        mark_processed("task3", "projB", "Task 3", None)

        result_a = get_processed_ids_for_project("projA")
        result_b = get_processed_ids_for_project("projB")

        assert result_a == {"task1", "task2"}
        assert result_b == {"task3"}

    def test_returns_set_type(self, isolated_db):
        from ticktick_mcp.completion_db import (
            get_processed_ids_for_project,
            init_db,
            mark_processed,
        )

        init_db()
        mark_processed("task1", "projA", None, None)

        result = get_processed_ids_for_project("projA")
        assert isinstance(result, set)


# =============================================================================
# ticktick_mark_completion_processed MCP tool tests
# =============================================================================


class TestMarkCompletionProcessedTool:
    def test_returns_ok_on_first_call(self, isolated_db):
        from ticktick_mcp.tools.completion_tools import ticktick_mark_completion_processed

        result = run(
            ticktick_mark_completion_processed(
                task_id="task1",
                project_id="projA",
                title="Fix the tap",
                completed_time="2025-06-01T10:00:00Z",
                notes="resolved",
            )
        )

        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["task_id"] == "task1"

    def test_returns_already_processed_on_second_call(self, isolated_db):
        from ticktick_mcp.tools.completion_tools import ticktick_mark_completion_processed

        run(
            ticktick_mark_completion_processed(
                task_id="task1",
                project_id="projA",
            )
        )
        result = run(
            ticktick_mark_completion_processed(
                task_id="task1",
                project_id="projA",
            )
        )

        data = json.loads(result)
        assert data["status"] == "already_processed"
        assert data["task_id"] == "task1"

    def test_minimal_call_no_optional_fields(self, isolated_db):
        from ticktick_mcp.tools.completion_tools import ticktick_mark_completion_processed

        result = run(
            ticktick_mark_completion_processed(
                task_id="task2",
                project_id="projB",
            )
        )

        data = json.loads(result)
        assert data["status"] == "ok"

    def test_persists_to_db(self, isolated_db):
        """After a successful tool call, is_processed should return True."""
        from ticktick_mcp.completion_db import is_processed
        from ticktick_mcp.tools.completion_tools import ticktick_mark_completion_processed

        run(
            ticktick_mark_completion_processed(
                task_id="task3",
                project_id="projC",
            )
        )

        assert is_processed("task3") is True
