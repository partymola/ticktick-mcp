"""Tests for src/ticktick_mcp/helpers.py.

Covers format_response, require_ticktick_client decorator,
_get_all_tasks_from_ticktick, and _parse_due_date.

The shared conftest.py monkey-patches helpers.require_ticktick_client to a
no-op so the @decorator on tools doesn't gate other tests. To test the real
decorator here, we capture it from a fresh module reload at import time, then
restore the conftest patch so other test files aren't affected.
"""

import asyncio
import datetime
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

import ticktick_mcp.helpers as helpers_module

# --- Capture the *original* require_ticktick_client before it gets clobbered ---
# conftest.py replaced this attribute with `lambda f: f` and stashed the real
# one first. Reloading to recover it would rebind every other name in the module
# -- notably ToolLogicError -- and make the suite order-dependent.
_real_require_ticktick_client = helpers_module._original_require_ticktick_client

# Pull in everything else after reload settles
from ticktick_mcp.helpers import (  # noqa: E402
    _get_all_tasks_from_ticktick,
    _parse_due_date,
    format_response,
)


def run(coro):
    return asyncio.run(coro)


# ============================================================ #
# format_response                                              #
# ============================================================ #


class TestFormatResponseDict:
    def test_dict_returns_indent2_json(self):
        out = format_response({"a": 1, "b": "two"})
        # json.dumps(..., indent=2) puts each key on its own line
        assert "\n" in out
        parsed = json.loads(out)
        assert parsed == {"a": 1, "b": "two"}

    def test_dict_default_str_handles_datetime(self):
        """datetime values are stringified by default=str (otherwise they'd raise TypeError)."""
        dt = datetime.datetime(2024, 7, 26, 12, 0, 0)
        out = format_response({"when": dt})
        parsed = json.loads(out)
        # datetime is stringified via default=str
        assert "2024-07-26" in parsed["when"]

    def test_nested_dict_preserved(self):
        out = format_response({"outer": {"inner": [1, 2, 3]}})
        parsed = json.loads(out)
        assert parsed["outer"]["inner"] == [1, 2, 3]


class TestFormatResponseList:
    def test_list_returns_json_array(self):
        out = format_response([1, 2, 3])
        parsed = json.loads(out)
        assert parsed == [1, 2, 3]

    def test_empty_list_returns_empty_json_array(self):
        out = format_response([])
        assert json.loads(out) == []

    def test_list_of_dicts(self):
        out = format_response([{"id": "a"}, {"id": "b"}])
        parsed = json.loads(out)
        assert len(parsed) == 2
        assert parsed[0]["id"] == "a"


class TestFormatResponseScalar:
    def test_none_returns_null(self):
        """None is serialised to the JSON literal 'null'."""
        assert format_response(None) == "null"

    def test_string_returns_result_wrapper(self):
        """Unexpected scalars are wrapped in {'result': str(value)}."""
        out = format_response("hello")
        parsed = json.loads(out)
        assert parsed == {"result": "hello"}

    def test_int_returns_result_wrapper(self):
        out = format_response(42)
        parsed = json.loads(out)
        assert parsed == {"result": "42"}

    def test_bool_returns_result_wrapper(self):
        """bool is also handled by the else branch (not dict/list/None)."""
        out = format_response(True)
        parsed = json.loads(out)
        assert parsed == {"result": "True"}


class TestFormatResponseError:
    def test_non_serializable_dict_returns_error(self):
        """A dict containing non-string non-coercible keys triggers TypeError;
        format_response should catch it and return an error dict."""

        class BadKey:
            def __hash__(self):
                return 1

            def __eq__(self, other):
                return False

        out = format_response({BadKey(): "value"})
        parsed = json.loads(out)
        assert "error" in parsed
        assert "Failed to serialize response" in parsed["error"]
        assert "details" in parsed


# ============================================================ #
# require_ticktick_client decorator                            #
# ============================================================ #


class TestRequireTickTickClient:
    def test_client_none_returns_error_json(self):
        """When the singleton has no client, the decorator short-circuits and
        returns a JSON error string without invoking the wrapped function."""
        called = {"flag": False}

        @_real_require_ticktick_client
        async def fake_tool():
            called["flag"] = True
            return "should not be returned"

        with patch(
            "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
            return_value=None,
        ):
            result = run(fake_tool())

        assert called["flag"] is False
        parsed = json.loads(result)
        assert "error" in parsed
        assert "TickTick client not initialized" in parsed["error"]

    def test_client_none_error_includes_last_init_error(self):
        """The short-circuit error surfaces the underlying init failure."""

        @_real_require_ticktick_client
        async def fake_tool():
            return "should not be returned"

        with (
            patch(
                "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
                return_value=None,
            ),
            patch(
                "ticktick_mcp.helpers.TickTickClientSingleton.last_error",
                return_value="login rate limited",
            ),
        ):
            result = run(fake_tool())

        parsed = json.loads(result)
        assert "TickTick client not initialized" in parsed["error"]
        assert "login rate limited" in parsed["error"]

    def test_rate_limited_returns_stop_message(self):
        """A 429 login rate-limit yields a rate_limited status and a STOP
        instruction so agents back off instead of retrying."""

        @_real_require_ticktick_client
        async def fake_tool():
            return "should not be returned"

        with (
            patch(
                "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
                return_value=None,
            ),
            patch(
                "ticktick_mcp.helpers.TickTickClientSingleton.is_rate_limited",
                return_value=True,
            ),
        ):
            result = run(fake_tool())

        parsed = json.loads(result)
        assert parsed["status"] == "rate_limited"
        assert "429" in parsed["error"]
        assert "STOP" in parsed["error"]
        assert "retry" in parsed

    def test_client_present_calls_through(self):
        """When the client is available, the wrapped function is called."""

        @_real_require_ticktick_client
        async def fake_tool(x):
            return f"ok-{x}"

        with patch(
            "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
            return_value=MagicMock(),  # any truthy object
        ):
            result = run(fake_tool("foo"))

        assert result == "ok-foo"

    def test_decorator_preserves_args_and_kwargs(self):
        @_real_require_ticktick_client
        async def fake_tool(a, b, c=None):
            return (a, b, c)

        with patch(
            "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
            return_value=MagicMock(),
        ):
            result = run(fake_tool(1, 2, c=3))

        assert result == (1, 2, 3)


# ============================================================ #
# _get_all_tasks_from_ticktick                                 #
# ============================================================ #


class TestGetAllTasksFromTickTick:
    def test_client_none_raises_connection_error(self):
        with patch(
            "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
            return_value=None,
        ):
            with pytest.raises(ConnectionError):
                _get_all_tasks_from_ticktick()

    def test_iterates_state_projects_plus_inbox(self):
        """The helper queries every project in state plus the inbox id."""
        client = MagicMock()
        client.state = {"projects": [{"id": "p1"}, {"id": "p2"}]}
        client.inbox_id = "inbox"
        # Return one task per project
        client.task.get_from_project = MagicMock(side_effect=lambda pid: [{"id": f"task-{pid}"}])

        with patch(
            "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
            return_value=client,
        ):
            result = _get_all_tasks_from_ticktick()

        # 3 projects, 1 task each
        assert len(result) == 3
        # All three project ids should have been queried
        called_pids = {c.args[0] for c in client.task.get_from_project.call_args_list}
        assert called_pids == {"p1", "p2", "inbox"}

    def test_dict_response_appended_as_single_item(self):
        """If get_from_project returns a dict (not a list), it's appended as one item."""
        client = MagicMock()
        client.state = {"projects": [{"id": "p1"}]}
        client.inbox_id = None  # skip inbox path
        client.task.get_from_project = MagicMock(return_value={"id": "single"})

        with patch(
            "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
            return_value=client,
        ):
            result = _get_all_tasks_from_ticktick()

        assert result == [{"id": "single"}]

    def test_none_response_is_skipped(self):
        """If get_from_project returns None, no task is added for that project."""
        client = MagicMock()
        client.state = {"projects": [{"id": "p1"}]}
        client.inbox_id = None
        client.task.get_from_project = MagicMock(return_value=None)

        with patch(
            "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
            return_value=client,
        ):
            result = _get_all_tasks_from_ticktick()

        assert result == []

    def test_unexpected_type_logged_but_not_aborted(self, caplog):
        """A string or other unexpected return type is logged but doesn't abort."""
        client = MagicMock()
        client.state = {"projects": [{"id": "p1"}]}
        client.inbox_id = None
        client.task.get_from_project = MagicMock(return_value="unexpected")

        with (
            patch(
                "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            caplog.at_level(logging.WARNING),
        ):
            result = _get_all_tasks_from_ticktick()

        assert result == []
        # NOTE: A truthy string passes the `if tasks_in_project:` check and
        # then falls through both the list/dict isinstance branches to the
        # "Unexpected data type" warning. Characterised as-is.

    def test_per_project_failure_does_not_abort(self):
        """One project raising shouldn't stop the others from being collected."""
        client = MagicMock()
        client.state = {"projects": [{"id": "p1"}, {"id": "p2"}]}
        client.inbox_id = None

        def fetch(pid):
            if pid == "p1":
                raise Exception("boom")
            return [{"id": f"task-{pid}"}]

        client.task.get_from_project = MagicMock(side_effect=fetch)

        with patch(
            "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
            return_value=client,
        ):
            result = _get_all_tasks_from_ticktick()

        # p1 raised, but p2 still returned a task
        assert result == [{"id": "task-p2"}]

    def test_state_projects_access_failure_defaults_to_empty(self):
        """If state.get('projects') raises, the helper falls back to []."""
        client = MagicMock()
        # Make state.get raise an exception
        bad_state = MagicMock()
        bad_state.get = MagicMock(side_effect=Exception("state broken"))
        client.state = bad_state
        client.inbox_id = "inbox"
        client.task.get_from_project = MagicMock(return_value=[{"id": "t1"}])

        with patch(
            "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
            return_value=client,
        ):
            result = _get_all_tasks_from_ticktick()

        # state failed → no projects from state, but inbox still queried
        assert result == [{"id": "t1"}]

    def test_inbox_id_access_failure_logged(self, caplog):
        """An exception accessing inbox_id is logged but doesn't abort."""
        client = MagicMock()
        client.state = {"projects": [{"id": "p1"}]}
        # Make inbox_id access raise on attribute read
        type(client).inbox_id = property(
            lambda self: (_ for _ in ()).throw(Exception("inbox broken"))
        )
        client.task.get_from_project = MagicMock(return_value=[{"id": "t1"}])

        with (
            patch(
                "ticktick_mcp.helpers.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            caplog.at_level(logging.ERROR),
        ):
            result = _get_all_tasks_from_ticktick()

        # state['projects'] still gave us p1
        assert result == [{"id": "t1"}]

        # Clean up the class-level property to avoid leaking into other tests
        del type(client).inbox_id


# ============================================================ #
# _parse_due_date                                              #
# ============================================================ #


class TestParseDueDate:
    def test_none_returns_none(self):
        assert _parse_due_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_due_date("") is None

    def test_non_string_returns_none(self):
        """Numbers, lists, dicts etc. → None."""
        assert _parse_due_date(12345) is None
        assert _parse_due_date([1, 2]) is None
        assert _parse_due_date({"x": 1}) is None

    def test_short_string_returns_none(self, caplog):
        """Strings shorter than 10 chars can't contain a YYYY-MM-DD prefix."""
        with caplog.at_level(logging.WARNING):
            assert _parse_due_date("2024") is None
        # The function logs a warning about it being too short
        assert any("too short" in rec.message for rec in caplog.records)

    def test_valid_date_only_string(self):
        result = _parse_due_date("2024-07-26")
        assert result == datetime.date(2024, 7, 26)

    def test_valid_datetime_prefix_string(self):
        """Only the YYYY-MM-DD prefix is used; the time portion is ignored."""
        result = _parse_due_date("2024-07-26T18:00:00+0900")
        assert result == datetime.date(2024, 7, 26)

    def test_invalid_date_returns_none(self, caplog):
        """Malformed date prefix is logged and returns None."""
        with caplog.at_level(logging.WARNING):
            assert _parse_due_date("not-a-dateXXX") is None
        assert any("Could not parse" in rec.message for rec in caplog.records)

    def test_invalid_month_returns_none(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert _parse_due_date("2024-99-01") is None

    def test_returns_date_not_datetime(self):
        """The return type is datetime.date, not datetime.datetime."""
        result = _parse_due_date("2024-07-26")
        assert type(result) is datetime.date

    def test_exact_10_char_string_parses(self):
        """Boundary: exactly 10 chars works (>= 10 check)."""
        result = _parse_due_date("2024-01-01")
        assert result == datetime.date(2024, 1, 1)
