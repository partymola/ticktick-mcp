"""Tests for update_task fix: partial updates should preserve unmodified fields."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp.tools.task_tools import TaskObject, update_task


def run(coro):
    """Helper to run an async function synchronously."""
    return asyncio.run(coro)


# --- Fixtures ---


@pytest.fixture
def mock_client():
    """Create a mock TickTick client with task.update method."""
    client = MagicMock()
    # task.update returns whatever was passed to it (simulates API echo)
    client.task.update = MagicMock(side_effect=lambda d: d)
    return client


@pytest.fixture
def existing_task_with_dates():
    """Simulate a task returned by client.get_by_id with dates, reminders, priority."""
    return {
        "id": "task123",
        "projectId": "proj456",
        "title": "Buy groceries",
        "content": "Milk, eggs, bread",
        "priority": 3,
        "startDate": "2024-08-01T09:00:00+0000",
        "dueDate": "2024-08-01T17:00:00+0000",
        "timeZone": "Europe/London",
        "reminders": [
            {"id": "rem1", "trigger": "TRIGGER:-PT30M"},
            {"id": "rem2", "trigger": "TRIGGER:PT0S"},
        ],
        "status": 0,
        "tags": ["shopping"],
        # Read-only fields that the API returns but should be filtered out
        "creator": "user@example.com",
        "deleted": 0,
        "kind": "TEXT",
        "isFloating": False,
        "etag": "abc123etag",
        "createdTime": "2024-07-01T10:00:00+0000",
        "modifiedTime": "2024-07-15T10:00:00+0000",
    }


# --- Tests: Partial updates preserve existing fields ---


class TestUpdateTaskPartialUpdate:
    def test_update_only_priority_preserves_dates_and_reminders(
        self, mock_client, existing_task_with_dates
    ):
        """Updating only priority should not wipe dates or reminders."""
        mock_client.get_by_id = MagicMock(return_value=existing_task_with_dates.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", priority=5)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]

        assert sent_to_api["priority"] == 5
        assert sent_to_api["startDate"] == "2024-08-01T09:00:00+0000"
        assert sent_to_api["dueDate"] == "2024-08-01T17:00:00+0000"
        assert sent_to_api["title"] == "Buy groceries"
        assert sent_to_api["tags"] == ["shopping"]

    def test_update_only_dates_preserves_priority(self, mock_client, existing_task_with_dates):
        """Updating only dates should not reset priority to 0."""
        mock_client.get_by_id = MagicMock(return_value=existing_task_with_dates.copy())

        task_obj = TaskObject(
            id="task123",
            projectId="proj456",
            dueDate="2024-09-01T17:00:00+00:00",
            expectedDayOfWeek="Sunday",
        )

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]

        # Priority should be preserved (not reset to default 0)
        assert sent_to_api["priority"] == 3
        assert sent_to_api["title"] == "Buy groceries"


# --- Tests: Reminder normalization ---


class TestUpdateTaskReminderNormalization:
    def test_reminders_object_format_normalized_to_strings(
        self, mock_client, existing_task_with_dates
    ):
        """Reminders returned as [{id, trigger}] objects should become ["TRIGGER:..."] strings."""
        mock_client.get_by_id = MagicMock(return_value=existing_task_with_dates.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", title="Updated title")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]
        assert sent_to_api["reminders"] == ["TRIGGER:-PT30M", "TRIGGER:PT0S"]

    def test_reminders_already_strings_left_unchanged(self, mock_client):
        """Reminders that are already strings should pass through unchanged."""
        existing = {
            "id": "task123",
            "projectId": "proj456",
            "title": "Task",
            "priority": 1,
            "reminders": ["TRIGGER:-PT15M"],
        }
        mock_client.get_by_id = MagicMock(return_value=existing.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", title="New title")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]
        assert sent_to_api["reminders"] == ["TRIGGER:-PT15M"]

    def test_reminders_mixed_format_normalized(self, mock_client):
        """A mix of object and string reminders should all be normalized."""
        existing = {
            "id": "task123",
            "projectId": "proj456",
            "title": "Task",
            "reminders": [
                {"id": "r1", "trigger": "TRIGGER:-PT1H"},
                "TRIGGER:PT0S",
            ],
        }
        mock_client.get_by_id = MagicMock(return_value=existing.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", title="New title")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]
        assert sent_to_api["reminders"] == ["TRIGGER:-PT1H", "TRIGGER:PT0S"]


# --- Tests: Reminder verification against what the API really echoes ---


REMINDED = {
    "id": "task123",
    "projectId": "proj456",
    "title": "T",
    "dueDate": "2024-08-01T17:00:00+0000",
    "parentId": "parent1",
    "reminders": [{"id": "r1", "trigger": "TRIGGER:-PT30M"}],
    "etag": "abc",
    "modifiedTime": "2024-07-15T10:00:00+0000",
    "createdTime": "2024-07-01T10:00:00+0000",
    "creator": 1,
    "deleted": 0,
}
CLEARED = {**REMINDED, "reminders": []}


def _keeps_reminder(body):
    return {**body, "reminders": ["TRIGGER:-PT30M"]}


def _v2_body(client):
    return client.http_post.call_args.kwargs["json"]["update"][0]


class TestUpdateTaskReminderWarnings:
    """Measured against the live API: TickTick's open API leaves an empty reminder
    list out of its response, and keeps existing reminders when sent [] or null.
    Its v2 batch route removes them, given the whole task."""

    def _update(self, client, *reads, **fields):
        client.BASE_URL = "https://api.example/api/v2/"
        client.get_by_id = MagicMock(side_effect=list(reads))
        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ):
            return json.loads(run(update_task(task_object=TaskObject(id="task123", **fields))))

    def test_a_task_with_no_reminders_updates_without_a_warning(self, mock_client):
        existing = {"id": "task123", "projectId": "proj456", "title": "T", "reminders": []}
        mock_client.task.update = MagicMock(
            side_effect=lambda d: {k: v for k, v in d.items() if k != "reminders"}
        )

        result = self._update(mock_client, existing, content="New")

        assert result["content"] == "New"
        assert "_verification_warnings" not in result

    def test_a_task_with_reminders_keeps_them_without_a_warning(self, mock_client):
        existing = {
            "id": "task123",
            "projectId": "proj456",
            "title": "T",
            "reminders": [{"id": "r1", "trigger": "TRIGGER:-PT30M"}],
        }

        result = self._update(mock_client, existing, content="New")

        assert result["reminders"] == ["TRIGGER:-PT30M"]
        assert "_verification_warnings" not in result

    def test_an_untouched_reminder_the_api_drops_is_reported(self, mock_client):
        """Not a response anyone has measured: this guards the wipe the check
        exists for. The caller never mentioned reminders, so only checking the
        merged body, rather than the fields they set, can notice one going."""
        existing = {
            "id": "task123",
            "projectId": "proj456",
            "title": "T",
            "reminders": [{"id": "r1", "trigger": "TRIGGER:-PT30M"}],
        }
        mock_client.task.update = MagicMock(
            side_effect=lambda d: {k: v for k, v in d.items() if k != "reminders"}
        )

        result = self._update(mock_client, existing, content="New")

        assert any("data may be lost" in w for w in result["_verification_warnings"])

    def test_the_tool_description_says_how_a_clear_behaves(self):
        """The docstring is what a model reads before sending the clear."""
        doc = " ".join(update_task.__doc__.split())
        assert "``reminders: []`` removes whatever reminders the task has" in doc
        assert "``reminders: null`` leaves them alone" in doc

    def test_a_clear_the_open_api_ignores_goes_through_the_v2_route(self, mock_client):
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)

        result = self._update(mock_client, REMINDED, REMINDED, CLEARED, reminders=[])

        body = _v2_body(mock_client)
        assert body["reminders"] == []
        assert body["reminder"] == ""
        mock_client.http_post.assert_called_once_with(
            f"{mock_client.BASE_URL}batch/task",
            json={"add": [], "update": [body], "delete": []},
            cookies=mock_client.cookies,
            headers=mock_client.HEADERS,
        )
        assert result["reminders"] == []
        assert "_verification_warnings" not in result

    def test_a_null_leaves_the_reminders_alone(self, mock_client):
        """A model filling in optional keys writes null meaning unset, and a
        removal cannot be undone, so only an explicit empty list asks for one."""
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)

        result = self._update(mock_client, REMINDED, content="New", reminders=None)

        mock_client.http_post.assert_not_called()
        assert result["reminders"] == ["TRIGGER:-PT30M"]
        assert "_verification_warnings" not in result

    def test_the_read_back_follows_a_real_sync(self, mock_client):
        """An earlier sync inside the throttle window would read the state from
        before the v2 request, and report every clear as failed."""
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)

        self._update(mock_client, REMINDED, REMINDED, CLEARED, reminders=[])

        calls = [call[0] for call in mock_client.method_calls]
        assert "sync" in calls[calls.index("http_post") :]

    def test_a_task_missing_from_the_read_back_is_not_called_cleared(self, mock_client):
        """get_by_id answers {} for a task it cannot find, and that holds no
        reminders only in the sense that it holds nothing."""
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)

        result = self._update(mock_client, REMINDED, REMINDED, {}, reminders=[])

        assert result["reminders"] == ["TRIGGER:-PT30M"]
        assert any("could not be confirmed" in w for w in result["_verification_warnings"])

    @pytest.mark.parametrize(
        "field, value",
        [("title", "New"), ("content", "New"), ("desc", "New"), ("priority", 5)],
    )
    def test_nothing_goes_to_the_v2_route_from_a_read_behind_the_update(
        self, mock_client, field, value
    ):
        """The route overwrites without a conflict check, so sending a read that
        does not show the caller's own change yet would undo it. This also covers
        an open-API update that silently did nothing."""
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)

        result = self._update(mock_client, REMINDED, REMINDED, reminders=[], **{field: value})

        mock_client.http_post.assert_not_called()
        assert any("could not be confirmed" in w for w in result["_verification_warnings"])

    def test_a_null_beside_a_clear_does_not_block_it(self, mock_client):
        """A model filling in optional keys writes null, and no stored title
        will ever match one."""
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)

        result = self._update(mock_client, REMINDED, REMINDED, CLEARED, title=None, reminders=[])

        mock_client.http_post.assert_called_once()
        assert "_verification_warnings" not in result

    def test_a_clear_on_a_task_with_nothing_to_clear_that_left_the_list_is_quiet(self, mock_client):
        """Completing a task in the same call takes it out of the synced list,
        so there is no stored task to read and no reminder to remove."""
        mock_client.task.update = MagicMock(
            side_effect=lambda d: {k: v for k, v in d.items() if k != "reminders"}
        )

        result = self._update(mock_client, CLEARED, {}, status=2, reminders=[])

        mock_client.http_post.assert_not_called()
        assert "_verification_warnings" not in result

    def test_a_reminder_in_the_echo_warns_when_the_task_cannot_be_read(self, mock_client):
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)

        result = self._update(mock_client, REMINDED, {}, status=2, reminders=[])

        mock_client.http_post.assert_not_called()
        assert any("could not be confirmed" in w for w in result["_verification_warnings"])

    def test_the_v2_body_is_the_whole_task_as_it_stands_after_the_update(self, mock_client):
        """That route loses the dates from a sparse body, so the body comes from
        a fresh read: the sparse echo would wipe them, and the read from before
        the update would undo whatever else the caller just changed."""
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)
        after_update = {**REMINDED, "content": "New"}

        self._update(mock_client, REMINDED, after_update, CLEARED, content="New", reminders=[])

        body = _v2_body(mock_client)
        assert body["content"] == "New"
        assert body["dueDate"] == REMINDED["dueDate"]
        assert body["parentId"] == REMINDED["parentId"]
        assert not {"etag", "modifiedTime", "createdTime", "creator", "deleted"} & set(body)

    def test_nothing_goes_to_the_v2_route_without_a_whole_task_to_send(self, mock_client):
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)

        result = self._update(mock_client, REMINDED, {}, reminders=[])

        mock_client.http_post.assert_not_called()
        assert any("could not be confirmed" in w for w in result["_verification_warnings"])

    def test_a_clear_that_does_not_take_is_reported(self, mock_client):
        """The re-read decides, not the POST having returned."""
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)

        result = self._update(mock_client, REMINDED, REMINDED, REMINDED, reminders=[])

        mock_client.http_post.assert_called_once()
        assert result["reminders"] == ["TRIGGER:-PT30M"]
        assert any("could not be confirmed" in w for w in result["_verification_warnings"])

    def test_a_v2_failure_does_not_fail_an_update_that_landed(self, mock_client):
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)
        mock_client.http_post = MagicMock(side_effect=RuntimeError("HTTP 500"))
        after_update = {**REMINDED, "content": "New"}

        result = self._update(mock_client, REMINDED, after_update, content="New", reminders=[])

        mock_client.http_post.assert_called_once()
        assert result["content"] == "New"
        assert "error" not in result
        assert any("could not be confirmed" in w for w in result["_verification_warnings"])

    def test_a_clear_that_cannot_be_read_back_is_not_called_cleared(self, mock_client):
        """A refresh that failed leaves the snapshot from before the v2 request,
        which says nothing about whether it took."""
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)

        with patch("ticktick_mcp.tools.task_tools.ensure_fresh", side_effect=[True, False]):
            result = self._update(mock_client, REMINDED, REMINDED, CLEARED, reminders=[])

        assert result["reminders"] == ["TRIGGER:-PT30M"]
        assert any("could not be confirmed" in w for w in result["_verification_warnings"])

    def test_a_clear_goes_through_the_v2_route_when_the_echo_is_empty(self, mock_client):
        """An empty response sends the tool to a re-read, which is the only
        place left to see that the reminder stayed."""
        mock_client.task.update = MagicMock(return_value="")
        after = {**REMINDED, "content": "New"}
        cleared = {**after, "reminders": []}

        reads = (REMINDED, after, after, cleared)

        result = self._update(mock_client, *reads, content="New", reminders=[])

        mock_client.http_post.assert_called_once()
        assert result["outcome"] == "updated"
        assert result["content"] == "New"
        assert result["reminders"] == []
        assert "_verification_warnings" not in result

    def test_a_clear_that_does_not_take_on_an_empty_echo_is_reported(self, mock_client):
        mock_client.task.update = MagicMock(return_value="")

        result = self._update(mock_client, REMINDED, REMINDED, REMINDED, REMINDED, reminders=[])

        assert result["outcome"] == "updated"
        assert any("could not be confirmed" in w for w in result["_verification_warnings"])

    def test_a_clear_that_worked_on_an_empty_echo_needs_no_v2_request(self, mock_client):
        """The re-read is the thing to judge by, not the task read beforehand."""
        mock_client.task.update = MagicMock(return_value="")

        result = self._update(mock_client, REMINDED, CLEARED, CLEARED, reminders=[])

        mock_client.http_post.assert_not_called()
        assert result["outcome"] == "updated"
        assert "_verification_warnings" not in result

    def test_a_task_with_no_reminders_of_its_own_on_an_empty_echo_is_left_alone(self, mock_client):
        """The caller asked for no clear, so a reminder in the re-read is news
        from somewhere else, and removing it would destroy what they never named."""
        mock_client.task.update = MagicMock(return_value="")

        result = self._update(mock_client, CLEARED, REMINDED, content="New")

        mock_client.http_post.assert_not_called()
        assert result["outcome"] == "updated"
        assert "_verification_warnings" not in result

    def test_a_replacement_list_stays_on_the_open_api(self, mock_client):
        """Measured: a non-empty list replaces the reminders there."""
        mock_client.task.update = MagicMock(side_effect=lambda d: d)

        result = self._update(mock_client, REMINDED, reminders=["TRIGGER:-PT5M"])

        mock_client.http_post.assert_not_called()
        assert result["reminders"] == ["TRIGGER:-PT5M"]
        assert "_verification_warnings" not in result

    def test_removing_the_date_reports_the_reminders_going_with_it(self, mock_client):
        """Measured: dueDate and startDate sent as null take the reminders too.
        The warning is deliberate and true, not a fault to silence."""
        existing = {
            "id": "task123",
            "projectId": "proj456",
            "title": "T",
            "dueDate": "2024-08-01T17:00:00+0000",
            "startDate": "2024-08-01T17:00:00+0000",
            "reminders": [{"id": "r1", "trigger": "TRIGGER:-PT30M"}],
        }
        mock_client.task.update = MagicMock(
            side_effect=lambda d: {k: v for k, v in d.items() if k != "reminders" and v is not None}
        )

        result = self._update(mock_client, existing, dueDate=None, startDate=None)

        sent = mock_client.task.update.call_args[0][0]
        assert sent["dueDate"] is None
        assert sent["startDate"] is None
        mock_client.http_post.assert_not_called()
        warnings = result["_verification_warnings"]
        assert len(warnings) == 1
        assert warnings[0].startswith("reminders:")

    def test_a_clear_the_open_api_honoured_needs_no_v2_request(self, mock_client):
        """It ignores a clear today, but the server is free to start honouring
        one, and then the second request has nothing to do. The stored task
        decides that, not the sparse echo."""
        mock_client.task.update = MagicMock(
            side_effect=lambda d: {k: v for k, v in d.items() if k != "reminders"}
        )

        result = self._update(mock_client, REMINDED, CLEARED, reminders=[])

        mock_client.http_post.assert_not_called()
        assert "_verification_warnings" not in result

    def test_an_echo_without_reminders_does_not_end_the_clear(self, mock_client):
        """If the echo left reminders out while the task kept them, the read of
        the stored task is what still finds them."""
        mock_client.task.update = MagicMock(
            side_effect=lambda d: {k: v for k, v in d.items() if k != "reminders"}
        )

        self._update(mock_client, REMINDED, REMINDED, CLEARED, reminders=[])

        mock_client.http_post.assert_called_once()

    def test_a_task_with_no_reminders_of_its_own_is_left_alone(self, mock_client):
        """The merged body carries the task's own empty list, which looks like a
        clear. A reminder added from another device between the read and the
        write is not one the caller asked to remove."""
        mock_client.task.update = MagicMock(side_effect=_keeps_reminder)

        result = self._update(mock_client, CLEARED, content="New")

        mock_client.http_post.assert_not_called()
        assert "_verification_warnings" not in result


# --- Tests: Read-only field filtering ---


class TestUpdateTaskFieldFiltering:
    def test_readonly_fields_filtered_out(self, mock_client, existing_task_with_dates):
        """Fields like creator, deleted, kind, isFloating should not be sent to the API."""
        mock_client.get_by_id = MagicMock(return_value=existing_task_with_dates.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", title="Updated")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]

        assert "creator" not in sent_to_api
        assert "deleted" not in sent_to_api
        assert "kind" not in sent_to_api
        assert "isFloating" not in sent_to_api
        assert "etag" not in sent_to_api
        assert "createdTime" not in sent_to_api
        assert "modifiedTime" not in sent_to_api

    def test_updatable_fields_preserved(self, mock_client, existing_task_with_dates):
        """All updatable fields from the existing task should be included."""
        mock_client.get_by_id = MagicMock(return_value=existing_task_with_dates.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", title="Updated")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]

        assert "id" in sent_to_api
        assert "projectId" in sent_to_api
        assert "title" in sent_to_api
        assert "content" in sent_to_api
        assert "priority" in sent_to_api
        assert "startDate" in sent_to_api
        assert "dueDate" in sent_to_api
        assert "timeZone" in sent_to_api
        assert "status" in sent_to_api
        assert "tags" in sent_to_api


# --- Tests: Error handling ---


class TestUpdateTaskErrorHandling:
    def test_client_exception_returns_error(self, mock_client):
        """Exceptions from the client should be caught and returned as error responses."""
        mock_client.get_by_id = MagicMock(side_effect=Exception("Connection timeout"))

        task_obj = TaskObject(id="task123", projectId="proj456", priority=5)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            result = run(update_task(task_object=task_obj))

        parsed = json.loads(result)
        assert "error" in parsed
        assert "task123" in parsed["error"]


# --- Tests: exclude_unset behavior ---


class TestUpdateTaskExcludeUnset:
    def test_default_priority_not_sent_when_unset(self, mock_client):
        """TaskObject has priority=0 as default. If not explicitly set, it should not
        be included in update_fields and should not overwrite existing priority."""
        existing = {
            "id": "task123",
            "projectId": "proj456",
            "title": "Task",
            "priority": 5,
        }
        mock_client.get_by_id = MagicMock(return_value=existing.copy())

        # Only set title - priority is NOT set, so default 0 should not override
        task_obj = TaskObject(id="task123", projectId="proj456", title="New title")

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]
        assert sent_to_api["priority"] == 5

    def test_explicit_priority_zero_is_sent(self, mock_client):
        """If the user explicitly sets priority=0, it should overwrite existing priority."""
        existing = {
            "id": "task123",
            "projectId": "proj456",
            "title": "Task",
            "priority": 5,
        }
        mock_client.get_by_id = MagicMock(return_value=existing.copy())

        task_obj = TaskObject(id="task123", projectId="proj456", priority=0)

        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ):
            run(update_task(task_object=task_obj))

        sent_to_api = mock_client.task.update.call_args[0][0]
        assert sent_to_api["priority"] == 0
