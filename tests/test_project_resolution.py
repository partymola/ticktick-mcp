"""Resolving a project by name instead of its opaque ID.

Ambiguity is the whole risk here: two projects sharing a name must raise, not
resolve to whichever came first in sync order, because a wrong answer files the
task somewhere the caller will not look for it.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ticktick_mcp.tools.completion_tools import (
    ticktick_get_unprocessed_completions,
    ticktick_mark_completion_processed,
)
from ticktick_mcp.tools.filter_tools import ticktick_filter_tasks
from ticktick_mcp.tools.task_tools import (
    TaskObject,
    ToolLogicError,
    _resolve_project_id,
    ticktick_create_task,
    ticktick_delete_tasks,
    ticktick_get_tasks_from_project,
    ticktick_move_task,
    update_task,
)

INBOX = "000000000000000000000000"
WORK = "111111111111111111111111"
HOME = "222222222222222222222222"
DUPE_A = "333333333333333333333333"
DUPE_B = "444444444444444444444444"


def _client(projects=None):
    client = MagicMock()
    client.inbox_id = INBOX
    client.state = {
        "projects": projects
        if projects is not None
        else [
            {"id": WORK, "name": "Work"},
            {"id": HOME, "name": "Home Admin"},
        ]
    }
    client.task.builder.return_value = {"title": "T"}
    client.task.create.return_value = {"id": "t1", "title": "T"}
    client.state.setdefault("tasks", [])
    return client


@pytest.fixture
def mock_client():
    client = _client()
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
        return_value=client,
    ):
        yield client


# --- the resolver itself ------------------------------------------------------


def test_known_id_resolves_to_itself():
    assert _resolve_project_id(_client(), WORK) == WORK


def test_an_id_beats_a_project_named_after_it():
    """The only case where the id short-circuit is load-bearing: a project
    named with another project's id. Falling through to name matching would
    resolve to the wrong project, and the caller would never see why."""
    client = _client([{"id": WORK, "name": "Work"}, {"id": HOME, "name": WORK}])
    assert _resolve_project_id(client, WORK) == WORK


def test_inbox_id_resolves_to_itself():
    assert _resolve_project_id(_client(), INBOX) == INBOX


def test_none_is_left_alone_so_the_inbox_default_survives():
    assert _resolve_project_id(_client(), None) is None


@pytest.mark.parametrize(
    "supplied", ["Work", "work", "WORK", "  Work  ", "Home Admin", "home admin"]
)
def test_unique_name_resolves(supplied):
    expected = HOME if "home" in supplied.lower() else WORK
    assert _resolve_project_id(_client(), supplied) == expected


def test_inbox_resolves_by_name():
    assert _resolve_project_id(_client(), "Inbox") == INBOX


def test_ambiguous_name_raises_and_never_guesses():
    """Two projects named the same must not silently resolve to either."""
    client = _client([{"id": DUPE_A, "name": "Personal"}, {"id": DUPE_B, "name": "Personal"}])
    with pytest.raises(ToolLogicError) as exc:
        _resolve_project_id(client, "Personal")
    msg = str(exc.value)
    assert DUPE_A in msg and DUPE_B in msg, "the error must name both candidates"


@pytest.mark.parametrize("unknown", ["999999999999999999999999", "p1", "Nonexistent Project", "42"])
def test_anything_unresolvable_passes_through_untouched(unknown):
    """Additive only. Local state lags and id formats are the server's
    business, so a value this cannot resolve must reach the API exactly as it
    does today - rejecting it would break callers that work now."""
    assert _resolve_project_id(_client(), unknown) == unknown


def test_resolution_survives_a_project_entry_missing_a_name():
    client = _client([{"id": WORK}, {"id": HOME, "name": "Home Admin"}])
    assert _resolve_project_id(client, "Home Admin") == HOME


@pytest.mark.parametrize("unusable", [{"name": "Home Admin"}, {"id": 5, "name": "Home Admin"}])
def test_resolution_survives_a_project_entry_with_no_usable_id(unusable):
    """An entry whose id is missing or not a string cannot be resolved TO, so
    it is not a match. Counting it raises KeyError on the missing case and
    reports a spurious ambiguity on the non-string one."""
    client = _client([unusable, {"id": HOME, "name": "Home Admin"}])
    assert _resolve_project_id(client, "Home Admin") == HOME


def test_a_client_whose_state_is_none_resolves_rather_than_raising():
    """A coalesce is not the same as a default: getattr hands back the None and
    the lookup after it raises."""
    client = MagicMock()
    client.state = None
    client.inbox_id = None
    client.sync.side_effect = lambda *a, **k: {}
    assert _resolve_project_id(client, "Home Admin") == "Home Admin"


# --- wired into the tools -----------------------------------------------------


def test_create_task_accepts_a_project_name(mock_client):
    """builder() owns the projectId, so assert on what it was handed."""
    asyncio.run(ticktick_create_task(title="T", project_id="Work"))
    assert mock_client.task.builder.call_args.kwargs.get("projectId") == WORK


def test_get_tasks_from_project_accepts_a_name(mock_client):
    """The tool asks the client for the project, so assert the resolved id is
    what it asked for - not merely that a list came back."""
    mock_client.task.get_from_project.return_value = [
        {"id": "t1", "projectId": WORK, "title": "T", "status": 0}
    ]
    asyncio.run(ticktick_get_tasks_from_project("Work"))
    mock_client.task.get_from_project.assert_called_once_with(WORK)


def test_create_task_reports_an_ambiguous_name_rather_than_filing_it(monkeypatch):
    client = _client([{"id": DUPE_A, "name": "Personal"}, {"id": DUPE_B, "name": "Personal"}])
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
        return_value=client,
    ):
        result = json.loads(asyncio.run(ticktick_create_task(title="T", project_id="Personal")))
    assert "error" in result
    client.task.create.assert_not_called()


# --- every surface taking a project id resolves names ------------------------


def _wired_client():
    client = _client()
    client.task.get_from_project.return_value = []
    client.get_by_id.return_value = {"id": "t1", "projectId": WORK, "title": "T", "status": 0}
    client.task.move.return_value = {"id": "t1"}
    client.task.delete.return_value = [{"id": "t1"}]
    return client


SURFACES = {
    "create_task": lambda: ticktick_create_task(title="T", project_id="Work"),
    "get_tasks_from_project": lambda: ticktick_get_tasks_from_project("Work"),
    "move_task": lambda: ticktick_move_task("t1", "Work"),
    "delete_tasks": lambda: ticktick_delete_tasks(["t1"], project_id="Work"),
    "filter_tasks": lambda: ticktick_filter_tasks({"status": "uncompleted", "project_id": "Work"}),
    "update_task": lambda: update_task(TaskObject(id="t1", projectId="Work", title="T")),
    "get_unprocessed_completions": lambda: ticktick_get_unprocessed_completions("Work"),
    "mark_completion_processed": lambda: ticktick_mark_completion_processed(
        task_id="t1", project_id="Work"
    ),
}


@pytest.mark.parametrize("name", sorted(SURFACES))
def test_every_project_id_surface_reports_ambiguity(name, monkeypatch):
    """An unwired surface silently uses the raw name - filter_tasks returned []
    with no error. Ambiguity is the one input that must visibly fail on all of
    them, so it is the cheapest probe for whether the wiring exists at all."""
    client = _wired_client()
    client.state["projects"] = [
        {"id": DUPE_A, "name": "Work"},
        {"id": DUPE_B, "name": "Work"},
    ]
    with (
        patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
        ),
        patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ),
        patch(
            "ticktick_mcp.tools.completion_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ),
        patch("ticktick_mcp.tools.completion_tools.init_db"),
    ):
        raw = asyncio.run(SURFACES[name]())
    blob = raw if isinstance(raw, str) else json.dumps(raw)
    assert "ambiguous" in blob, f"{name} did not resolve the project reference at all"
    assert "unexpected error" not in blob, (
        f"{name} let the ambiguity reach a broad handler, which relabels a clear "
        "message as an internal fault and logs a traceback"
    )


# --- resolution must happen after the sync that populates the project list ---


def _cold_client():
    """A client whose project list only appears once sync() has run, which is
    what a project created or renamed on another device looks like."""
    client = _client(projects=[])
    client.task.get_from_project.return_value = []
    client.get_by_id.return_value = {"id": "t1", "projectId": WORK, "title": "T", "status": 0}
    client.task.update.return_value = {"id": "t1", "projectId": WORK, "status": 0}
    client.task.get_completed.return_value = []

    def _sync(*a, **k):
        client.state["projects"] = [{"id": WORK, "name": "Work"}]
        return {}

    client.sync.side_effect = _sync
    return client


def test_get_tasks_from_project_resolves_after_syncing(monkeypatch):
    client = _cold_client()
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        asyncio.run(ticktick_get_tasks_from_project("Work"))
    client.task.get_from_project.assert_called_once_with(WORK)


def test_filter_tasks_resolves_after_syncing(monkeypatch):
    """Resolving first left the raw name in the comparison, so the first call
    after a project appeared elsewhere returned [] with no error and the second
    worked - the worst shape of bug, because it looks like a real answer."""
    client = _cold_client()
    task = {"id": "t1", "projectId": WORK, "title": "T", "status": 0}
    with (
        patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
        ),
        patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ),
        patch("ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick", return_value=[task]),
    ):
        raw = asyncio.run(ticktick_filter_tasks({"status": "uncompleted", "project_id": "Work"}))
    assert [t["id"] for t in json.loads(raw)] == ["t1"]


def test_update_task_resolves_after_syncing():
    client = _cold_client()
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        asyncio.run(update_task(TaskObject(id="t1", projectId="Work", title="T")))
    sent = client.task.update.call_args[0][0]
    payload = sent if isinstance(sent, dict) else {}
    assert payload.get("projectId") == WORK, "the raw name was POSTed instead of the id"


# --- the resolved id must actually be USED, not merely computed --------------


def test_delete_uses_the_resolved_id_not_the_name(mock_client):
    """Ambiguity alone cannot catch a caller that resolves and then discards
    the answer - it still raises. These pin the value reaching the client."""
    mock_client.get_by_id.return_value = {}
    asyncio.run(ticktick_delete_tasks(["t1"], project_id="Work"))
    sent = mock_client.task.delete.call_args[0][0]
    payload = sent[0] if isinstance(sent, list) else sent
    assert payload["projectId"] == WORK


def test_move_uses_the_resolved_id_not_the_name(mock_client):
    mock_client.get_by_id.return_value = {"id": "t1", "projectId": HOME, "title": "T"}
    asyncio.run(ticktick_move_task("t1", "Work"))
    assert mock_client.task.move.call_args[0][1] == WORK


def test_get_unprocessed_completions_uses_the_resolved_id(mock_client):
    with (
        patch(
            "ticktick_mcp.tools.completion_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ),
        patch("ticktick_mcp.tools.completion_tools.init_db"),
        patch(
            "ticktick_mcp.tools.completion_tools.get_processed_ids_for_project", return_value=set()
        ) as got,
        patch("ticktick_mcp.tools.completion_tools.TaskFilterer") as filterer,
    ):
        filterer.return_value.filter = AsyncMock(return_value=[])
        asyncio.run(ticktick_get_unprocessed_completions("Work"))
    got.assert_called_once_with(WORK)


def test_mark_completion_processed_keys_the_db_by_the_resolved_id(mock_client):
    """The value is the completion-DB key. A name-keyed row is invisible to a
    later id-keyed read, and nothing in the server can repair it."""
    with (
        patch(
            "ticktick_mcp.tools.completion_tools.TickTickClientSingleton.get_client",
            return_value=mock_client,
        ),
        patch("ticktick_mcp.tools.completion_tools.init_db"),
        patch("ticktick_mcp.tools.completion_tools.is_processed", return_value=False),
        patch("ticktick_mcp.tools.completion_tools.mark_processed") as marked,
    ):
        asyncio.run(ticktick_mark_completion_processed(task_id="t1", project_id="Work"))
    assert WORK in marked.call_args.args or WORK in marked.call_args.kwargs.values()


def test_get_unprocessed_completions_resolves_after_syncing():
    """Neither completion tool syncs for any other reason, so without an
    explicit refresh they resolve against whatever the last unrelated call
    left in state - arbitrarily stale on a long-lived server."""
    client = _cold_client()
    with (
        patch(
            "ticktick_mcp.tools.completion_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ),
        patch("ticktick_mcp.tools.completion_tools.init_db"),
        patch(
            "ticktick_mcp.tools.completion_tools.get_processed_ids_for_project", return_value=set()
        ) as got,
        patch("ticktick_mcp.tools.completion_tools.TaskFilterer") as filterer,
    ):
        filterer.return_value.filter = AsyncMock(return_value=[])
        asyncio.run(ticktick_get_unprocessed_completions("Work"))
    got.assert_called_once_with(WORK)


def test_mark_completion_processed_resolves_after_syncing():
    client = _cold_client()
    with (
        patch(
            "ticktick_mcp.tools.completion_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ),
        patch("ticktick_mcp.tools.completion_tools.init_db"),
        patch("ticktick_mcp.tools.completion_tools.is_processed", return_value=False),
        patch("ticktick_mcp.tools.completion_tools.mark_processed") as marked,
    ):
        asyncio.run(ticktick_mark_completion_processed(task_id="t1", project_id="Work"))
    assert WORK in marked.call_args.args or WORK in marked.call_args.kwargs.values(), (
        "a stale project list left the raw name as the completion-DB key"
    )


@pytest.mark.parametrize(
    "name,call,assertion",
    [
        (
            "create_task",
            lambda: ticktick_create_task(title="T", project_id="Work"),
            lambda c: c.task.builder.call_args.kwargs.get("projectId"),
        ),
        (
            "move_task",
            lambda: ticktick_move_task("t1", "Work"),
            lambda c: c.task.move.call_args[0][1],
        ),
        (
            "delete_tasks",
            lambda: ticktick_delete_tasks(["t1"], project_id="Work"),
            lambda c: (c.task.delete.call_args[0][0][0])["projectId"],
        ),
    ],
)
def test_remaining_surfaces_resolve_after_syncing(name, call, assertion):
    """The three sites the earlier ordering fix skipped. Each is reachable with
    a plain name and a cold client, and each would otherwise send the raw
    string on to the API."""
    client = _cold_client()
    if name == "delete_tasks":
        client.get_by_id.return_value = {}
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        asyncio.run(call())
    assert assertion(client) == WORK


# --- freshness belongs to the resolver, not to the callers -------------------


def test_resolver_syncs_itself_for_a_name():
    """No caller has to remember. The requirement was invisible in a signature
    and five separate call sites got it wrong."""
    client = _cold_client()
    assert _resolve_project_id(client, "Work") == WORK
    assert client.sync.called


def test_resolver_does_not_sync_for_a_known_id():
    """Ids win regardless of freshness, so an id must cost no round-trip -
    otherwise every completed-branch filter query starts syncing."""
    client = _client()
    _resolve_project_id(client, WORK)
    assert not client.sync.called


def test_resolver_does_not_sync_for_the_inbox_id():
    client = _client()
    _resolve_project_id(client, INBOX)
    assert not client.sync.called


def test_an_id_still_beats_a_name_match_introduced_by_the_sync():
    """The id short-circuit runs on pre-sync state, so the sync can introduce
    the very project the id belongs to. Without a re-check afterwards the id
    falls through to name matching and a project named with that id wins it."""
    client = _client(projects=[])

    def _sync(*a, **k):
        client.state["projects"] = [
            {"id": WORK, "name": "Work"},
            {"id": HOME, "name": WORK},
        ]
        return {}

    client.sync.side_effect = _sync
    assert _resolve_project_id(client, WORK) == WORK


# --- the completion-DB key must be an id, never an unresolved name -----------


@pytest.mark.parametrize(
    "call",
    [
        lambda: ticktick_get_unprocessed_completions("Nonexistent"),
        lambda: ticktick_mark_completion_processed(task_id="t1", project_id="Nonexistent"),
    ],
    ids=["get_unprocessed", "mark_processed"],
)
def test_completion_tools_refuse_an_unresolved_project(call):
    """Everywhere else an unresolved value merely reaches the API and fails
    there. Here it becomes the database key, so the row is written under a name
    no id-keyed read can find and nothing can repair."""
    client = _client()
    with (
        patch(
            "ticktick_mcp.tools.completion_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ),
        patch("ticktick_mcp.tools.completion_tools.init_db"),
        patch("ticktick_mcp.tools.completion_tools.mark_processed") as marked,
        patch("ticktick_mcp.tools.completion_tools.get_processed_ids_for_project") as got,
    ):
        result = json.loads(asyncio.run(call()))
    assert result.get("status") == "error"
    marked.assert_not_called()
    got.assert_not_called()


@pytest.mark.parametrize(
    "call",
    [
        lambda: ticktick_get_unprocessed_completions(WORK),
        lambda: ticktick_mark_completion_processed(task_id="t1", project_id=WORK),
    ],
    ids=["get_unprocessed", "mark_processed"],
)
def test_completion_tools_say_unverifiable_not_missing_when_the_refresh_fails(call, monkeypatch):
    """A real project id must not be reported as nonexistent because the sync
    failed. That sends the caller hunting for the wrong problem, and for
    mark_processed the work is purely local anyway."""
    client = _client(projects=[])  # id not in the stale list
    monkeypatch.setattr("ticktick_mcp.tools.completion_tools.ensure_fresh", lambda *a, **k: False)
    monkeypatch.setattr("ticktick_mcp.projects.ensure_fresh", lambda *a, **k: False)
    with (
        patch(
            "ticktick_mcp.tools.completion_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ),
        patch("ticktick_mcp.tools.completion_tools.init_db"),
        patch("ticktick_mcp.tools.completion_tools.mark_processed") as marked,
    ):
        result = json.loads(asyncio.run(call()))
    assert result.get("outcome") == "project_list_unverifiable"
    assert "No project matches" not in json.dumps(result)
    marked.assert_not_called()


@pytest.mark.parametrize(
    "call",
    [
        lambda: ticktick_get_unprocessed_completions("Personal"),
        lambda: ticktick_mark_completion_processed(task_id="t1", project_id="Personal"),
    ],
    ids=["get_unprocessed", "mark_processed"],
)
def test_ambiguity_appearing_only_after_the_forced_refresh_is_still_an_error(call):
    """The re-resolve after the forced refresh can raise for the first time -
    the second project only shows up in that sync. Outside a handler it escapes
    as an unhandled exception instead of a caller-visible error.

    Pre-sync state must hold NO project of this name, and the throttle must be
    warm. Otherwise the first resolve's own sync reveals the duplicate and it
    is that resolve which raises - a call that was already inside the handler,
    so the test would pass against the unfixed code and pin nothing."""
    from ticktick_mcp.freshness import ensure_fresh as real_ensure_fresh

    client = _client(projects=[{"id": WORK, "name": "Work"}])
    client.sync.side_effect = lambda *a, **k: {}
    real_ensure_fresh(client)
    client.sync.reset_mock()

    def _sync(*a, **k):
        client.state["projects"] = [
            {"id": DUPE_A, "name": "Personal"},
            {"id": DUPE_B, "name": "Personal"},
        ]
        return {}

    client.sync.side_effect = _sync
    with (
        patch(
            "ticktick_mcp.tools.completion_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ),
        patch("ticktick_mcp.tools.completion_tools.init_db"),
        patch("ticktick_mcp.tools.completion_tools.mark_processed") as marked,
    ):
        result = json.loads(asyncio.run(call()))
    assert result.get("status") == "error"
    assert "ambiguous" in json.dumps(result)
    marked.assert_not_called()
    # Exactly one sync: the forced refresh. More would mean the first resolve
    # also synced, and it - not the re-resolve - could be the raiser.
    assert client.sync.call_count == 1


def test_a_name_already_in_state_costs_no_forced_sync():
    """The completion tools resolve, then force a refresh only if that came
    back unresolved. Dropping the first resolve looks harmless - the re-resolve
    after the forced sync returns the same id - so the only thing that notices
    is the sync it did not need: a full-account fetch on every call."""
    from ticktick_mcp.freshness import ensure_fresh as real_ensure_fresh

    client = _client([{"id": WORK, "name": "Work"}])
    client.sync.side_effect = lambda *a, **k: {}
    real_ensure_fresh(client)  # a recent unrelated read leaves the window warm
    client.sync.reset_mock()

    with (
        patch(
            "ticktick_mcp.tools.completion_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ),
        patch("ticktick_mcp.tools.completion_tools.init_db"),
        patch("ticktick_mcp.tools.completion_tools.is_processed", return_value=False),
        patch("ticktick_mcp.tools.completion_tools.mark_processed"),
    ):
        asyncio.run(ticktick_mark_completion_processed(task_id="t1", project_id="Work"))
    client.sync.assert_not_called()
