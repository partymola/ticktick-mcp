"""Protected-task guard: config wiring, id normalisation, and relations.

These cover what ``test_protected_tasks.py`` left unpinned. Three classes of
mutant used to survive the suite: the loader could be replaced wholesale (no
test went through the environment), a padded or recased id slipped past the
comparison, and a protected task could be mutated by a call naming only its
parent.
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
    ticktick_make_subtask,
    ticktick_move_task,
    update_task,
)

# Hard-coded on purpose: this string is the operator-facing contract documented
# in README.md. Reading it off the module would let a rename pass silently.
ENV_NAME = "TICKTICK_MCP_PROTECTED_TASK_IDS"

PROTECTED = "aaaaaaaaaaaaaaaaaaaaaaaa"
PARENT = "cccccccccccccccccccccccc"
OTHER = "bbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.fixture
def mock_client():
    client = MagicMock()
    # A real client's state is always a mapping. Left as a bare MagicMock it
    # iterates as empty, so the relation walk reads nothing and the guard
    # cannot tell that from a state it is unable to read.
    client.state = {"tasks": [], "projects": []}
    client.get_by_id.return_value = {}
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client",
        return_value=client,
    ):
        yield client


@pytest.fixture
def protect(monkeypatch):
    monkeypatch.setattr(task_tools, "PROTECTED_TASK_IDS", frozenset({PROTECTED}))


def _refused(result):
    return json.loads(result).get("outcome") == "protected_task"


# --- the env var is actually wired up ----------------------------------------


def test_env_var_reaches_the_guard(mock_client, monkeypatch):
    """End-to-end through the environment. Without this the loader can be
    replaced with `return frozenset()` and the whole suite stays green."""
    monkeypatch.setenv(ENV_NAME, f"{OTHER},{PROTECTED}")
    monkeypatch.setattr(task_tools, "PROTECTED_TASK_IDS", task_tools._load_protected_task_ids())
    assert _refused(asyncio.run(ticktick_complete_task(PROTECTED)))
    assert _refused(asyncio.run(ticktick_complete_task(OTHER)))


def test_unset_env_var_protects_nothing(monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    assert task_tools._load_protected_task_ids() == frozenset()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("aaa,bbb", {"aaa", "bbb"}),
        ("aaa bbb", {"aaa", "bbb"}),
        ("aaa, bbb  ccc", {"aaa", "bbb", "ccc"}),
        ("aaa,", {"aaa"}),
        ("  aaa  ", {"aaa"}),
        ("'aaa',\"bbb\"", {"aaa", "bbb"}),
        ("AAA", {"aaa"}),
        ("", set()),
        ("   ", set()),
        (",,,", set()),
    ],
)
def test_loader_parses_documented_shapes(raw, expected, monkeypatch):
    monkeypatch.setenv(ENV_NAME, raw)
    assert task_tools._load_protected_task_ids() == frozenset(expected)


# --- both sides of the comparison are normalised identically -----------------


@pytest.mark.parametrize(
    "supplied",
    [
        f" {PROTECTED}",
        f"{PROTECTED} ",
        f"\t{PROTECTED}\n",
        PROTECTED.upper(),
        f'"{PROTECTED}"',
        f"'{PROTECTED}'",
    ],
)
def test_padded_or_recased_id_is_still_refused(mock_client, protect, supplied):
    """The API resolves these to the same task, so the guard must too."""
    result = asyncio.run(ticktick_delete_tasks([supplied]))
    assert _refused(result)
    mock_client.task.delete.assert_not_called()


def test_non_string_id_does_not_crash_the_guard(mock_client, protect):
    """A malformed id must not raise out of the guard - that would be an
    error path around the protection rather than a refusal."""
    asyncio.run(ticktick_delete_tasks([{"unhashable": True}]))


# --- the bare-string delete shape --------------------------------------------


def test_delete_refuses_a_bare_string_protected_id(mock_client, protect):
    """``task_ids`` accepts a plain string, which is the shape the tool's own
    docstring example uses."""
    result = asyncio.run(ticktick_delete_tasks(PROTECTED))
    assert _refused(result)
    mock_client.task.delete.assert_not_called()


# --- relations: a protected task reached via a task nobody named -------------


def _parent_of_protected(client):
    client.get_by_id.side_effect = lambda i, *a, **k: (
        {"id": PARENT, "projectId": "p1", "title": "Parent", "childIds": [PROTECTED]}
        if i == PARENT
        else {}
    )


def test_delete_refuses_parent_of_a_protected_subtask(mock_client, protect):
    _parent_of_protected(mock_client)
    result = asyncio.run(ticktick_delete_tasks([PARENT]))
    assert _refused(result)
    mock_client.task.delete.assert_not_called()


def test_move_refuses_parent_of_a_protected_subtask(mock_client, protect):
    _parent_of_protected(mock_client)
    result = asyncio.run(ticktick_move_task(PARENT, "p2"))
    assert _refused(result)
    mock_client.task.move.assert_not_called()


def test_make_subtask_refuses_reparenting_a_child_of_a_protected_task(mock_client, protect):
    mock_client.get_by_id.side_effect = lambda i, *a, **k: (
        {"id": OTHER, "projectId": "p1", "title": "Child", "parentId": PROTECTED}
        if i == OTHER
        else {}
    )
    result = asyncio.run(ticktick_make_subtask(PARENT, OTHER))
    assert _refused(result)
    mock_client.task.make_subtask.assert_not_called()


def test_unrelated_task_with_unprotected_children_still_proceeds(mock_client, protect):
    """The relation guard must not refuse everything with a subtask."""
    mock_client.get_by_id.side_effect = lambda i, *a, **k: {
        "id": PARENT,
        "projectId": "p1",
        "title": "Parent",
        "childIds": [OTHER],
    }
    asyncio.run(ticktick_delete_tasks([PARENT]))
    assert mock_client.task.delete.called


def test_lookup_failure_leaves_the_direct_id_check_intact(mock_client, protect):
    """The relation check is additive. Even with local state unavailable, an
    id named directly is still refused by the stage-one check."""
    mock_client.get_by_id.side_effect = RuntimeError("state unavailable")
    result = asyncio.run(ticktick_delete_tasks([PROTECTED]))
    assert _refused(result)
    mock_client.task.delete.assert_not_called()


def test_one_unresolvable_id_does_not_skip_relation_checks_on_the_others(mock_client, protect):
    """A lookup that raises must not abandon the rest of the batch. The second
    id here is the parent of a protected task and has to be caught."""

    def lookup(i, *a, **k):
        if i == OTHER:
            raise RuntimeError("state unavailable")
        return {"id": PARENT, "projectId": "p1", "title": "Parent", "childIds": [PROTECTED]}

    mock_client.get_by_id.side_effect = lookup
    result = asyncio.run(ticktick_delete_tasks([OTHER, PARENT]))
    assert _refused(result)
    mock_client.task.delete.assert_not_called()


# --- a directly-named refusal touches nothing at all -------------------------


MUTATIONS = {
    "update": lambda: update_task(TaskObject(id=PROTECTED, projectId="p1", title="x")),
    "update_dict": lambda: update_task({"id": PROTECTED, "projectId": "p1", "title": "x"}),
    "complete": lambda: ticktick_complete_task(PROTECTED),
    "move": lambda: ticktick_move_task(PROTECTED, "p2"),
    "delete": lambda: ticktick_delete_tasks([PROTECTED]),
    "make_subtask_child": lambda: ticktick_make_subtask(OTHER, PROTECTED),
    "make_subtask_parent": lambda: ticktick_make_subtask(PROTECTED, OTHER),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_directly_named_refusal_touches_the_client_not_at_all(mock_client, protect, name):
    """Asserting one mutator was not called leaves room for a read; assert the
    whole client object was never used."""
    result = asyncio.run(MUTATIONS[name]())
    assert _refused(result)
    assert mock_client.mock_calls == [], f"{name} touched the client: {mock_client.mock_calls}"


# --- the relation guard must decide on post-sync state -----------------------


PARENT_ID = "dddddddddddddddddddddddd"


def _client_learning_a_protected_subtask_on_sync():
    """A protected subtask attached on another device: invisible in local state
    until a sync, which is exactly the case the guard exists for."""
    client = MagicMock()
    stale = {"id": PARENT_ID, "projectId": "p1", "title": "Parent", "childIds": []}
    fresh = {"id": PARENT_ID, "projectId": "p1", "title": "Parent", "childIds": [PROTECTED]}
    state = {"seen": stale}

    def _sync(*a, **k):
        state["seen"] = fresh
        return {}

    client.sync.side_effect = _sync
    client.get_by_id.side_effect = lambda i, *a, **k: state["seen"] if i == PARENT_ID else {}
    client.state = {"projects": []}
    return client


def test_delete_refuses_a_protected_subtask_only_visible_after_syncing(protect):
    client = _client_learning_a_protected_subtask_on_sync()
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        result = asyncio.run(ticktick_delete_tasks([PARENT_ID]))
    assert _refused(result), "the guard read pre-sync state and let the delete through"
    client.task.delete.assert_not_called()


def test_move_refuses_a_protected_subtask_only_visible_after_syncing(protect):
    client = _client_learning_a_protected_subtask_on_sync()
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        result = asyncio.run(ticktick_move_task(PARENT_ID, "p2"))
    assert _refused(result)
    client.task.move.assert_not_called()


def test_make_subtask_refuses_a_protected_subtask_only_visible_after_syncing(protect):
    """make_subtask never had a sync of its own. It inherits one now because
    the guard owns its freshness, which is the point of moving it there."""
    client = _client_learning_a_protected_subtask_on_sync()
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        result = asyncio.run(ticktick_make_subtask(PARENT_ID, OTHER))
    assert _refused(result)
    client.task.make_subtask.assert_not_called()


def test_the_guard_costs_no_sync_when_nothing_is_protected(mock_client, monkeypatch):
    """The guard short-circuits before syncing, so an operator who never sets
    the variable pays nothing for a feature they do not use. Asserted on the
    guard rather than on a tool: the tools sync for their own pre-reads
    regardless, which would mask this."""
    monkeypatch.setattr(task_tools, "PROTECTED_TASK_IDS", frozenset())
    assert task_tools._protected_relation_refusal(mock_client, ["t1"]) is None
    assert not mock_client.sync.called


def test_the_guard_forces_a_sync_rather_than_honouring_the_throttle(protect):
    """A throttled sync is not good enough here. Another tool syncing seconds
    earlier would let the guard serve a snapshot from before the protected
    subtask was attached, and the delete it then permits cannot be undone."""
    from ticktick_mcp.freshness import ensure_fresh

    client = _client_learning_a_protected_subtask_on_sync()
    # Something else syncs first, opening the throttle window.
    client.sync.side_effect = lambda *a, **k: {}
    ensure_fresh(client)

    # Only now does the protected subtask appear server-side.
    fresh = {"id": PARENT_ID, "projectId": "p1", "title": "Parent", "childIds": [PROTECTED]}
    seen = {"task": {"id": PARENT_ID, "projectId": "p1", "title": "Parent", "childIds": []}}

    def _sync(*a, **k):
        seen["task"] = fresh
        return {}

    client.sync.side_effect = _sync
    client.get_by_id.side_effect = lambda i, *a, **k: seen["task"] if i == PARENT_ID else {}

    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        result = asyncio.run(ticktick_delete_tasks([PARENT_ID]))
    assert _refused(result), "the throttle served a pre-subtask snapshot to the guard"
    client.task.delete.assert_not_called()


def test_the_guard_refuses_when_it_cannot_refresh(protect, monkeypatch):
    """ensure_fresh is fail-soft, so a failure leaves an arbitrarily old
    snapshot. Deciding on it would fail OPEN on a delete that cannot be
    undone; a refusal is recoverable."""
    monkeypatch.setattr(task_tools, "ensure_fresh", lambda *a, **k: False)
    client = MagicMock()
    # A readable state, so the refusal can only come from the failed refresh.
    # Without it the unreadable-state check answers first and this passes even
    # with the refresh branch deleted.
    client.state = {"tasks": [], "projects": []}
    client.get_by_id.return_value = {"id": "t1", "projectId": "p1", "title": "T"}
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        result = json.loads(asyncio.run(ticktick_delete_tasks(["t1"])))
    assert result.get("outcome") == "protection_unverifiable"
    # Both refusal paths share the outcome, so without this the readable-state
    # line above is load-bearing setup that no assertion protects - delete it
    # and the test still passes with the refresh branch gone.
    assert "could not be refreshed" in result["error"]
    client.task.delete.assert_not_called()


GRANDCHILD_PARENT = "eeeeeeeeeeeeeeeeeeeeeeee"
MIDDLE = "ffffffffffffffffffffffff"


def test_delete_refuses_a_protected_grandchild(protect):
    """TickTick propagates a delete through the whole subtree, so checking only
    the named task's own childIds destroys a protected task two levels down."""
    tree = {
        GRANDCHILD_PARENT: {
            "id": GRANDCHILD_PARENT,
            "projectId": "p1",
            "title": "Top",
            "childIds": [MIDDLE],
        },
        MIDDLE: {"id": MIDDLE, "projectId": "p1", "title": "Middle", "childIds": [PROTECTED]},
    }
    client = MagicMock()
    client.state = {"tasks": list(tree.values())}
    client.get_by_id.side_effect = lambda i, *a, **k: tree.get(i, {})
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        result = json.loads(asyncio.run(ticktick_delete_tasks([GRANDCHILD_PARENT])))
    assert result.get("outcome") == "protected_task"
    client.task.delete.assert_not_called()


def test_a_descendant_recorded_only_by_its_own_parent_id_is_found(protect):
    """A task can carry parentId without the parent listing it in childIds, so
    childIds alone is not a complete view of the subtree."""
    client = MagicMock()
    client.state = {
        "tasks": [
            {"id": GRANDCHILD_PARENT, "projectId": "p1", "title": "Top"},
            {
                "id": PROTECTED,
                "projectId": "p1",
                "title": "Hidden child",
                "parentId": GRANDCHILD_PARENT,
            },
        ]
    }
    client.get_by_id.side_effect = lambda i, *a, **k: next(
        (t for t in client.state["tasks"] if t["id"] == i), {}
    )
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        result = json.loads(asyncio.run(ticktick_delete_tasks([GRANDCHILD_PARENT])))
    assert result.get("outcome") == "protected_task"
    client.task.delete.assert_not_called()


def test_a_parent_child_cycle_does_not_hang_the_guard(protect):
    """Malformed state must not spin the descendant walk forever."""
    client = MagicMock()
    a = {"id": "aaa1", "projectId": "p1", "title": "A", "childIds": ["bbb1"]}
    b = {"id": "bbb1", "projectId": "p1", "title": "B", "childIds": ["aaa1"]}
    client.state = {"tasks": [a, b]}
    client.get_by_id.side_effect = lambda i, *x, **k: {"aaa1": a, "bbb1": b}.get(i, {})
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        asyncio.run(ticktick_delete_tasks(["aaa1"]))
    assert client.task.delete.called


def test_orphans_with_a_non_string_parent_id_are_not_pooled_into_one_bucket(protect):
    """The reverse index is keyed on the NORMALISED parent id, which is "" for
    a non-string. Keying those in pools every orphan under one entry, and the
    next id that also normalises to "" - an empty or whitespace task id from a
    caller - inherits the whole pool and is refused for a relation it has not
    got."""
    client = MagicMock()
    client.state = {
        "tasks": [
            # Truthy, so the entry is not skipped before normalisation - a
            # falsy parentId would be dropped either way and pin nothing.
            {"id": PROTECTED, "projectId": "p1", "title": "Orphan", "parentId": 7},
        ]
    }
    client.get_by_id.side_effect = lambda i, *a, **k: {}
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        result = json.loads(asyncio.run(ticktick_delete_tasks(["   "], project_id="p1")))
    # Positively, not just "not refused": any error payload carries no outcome
    # at all, so the negative alone would also pass on a crash.
    assert result.get("outcome") != "protected_task"
    assert client.task.delete.called


@pytest.mark.parametrize("state", [None, [], "tasks", 0], ids=["none", "list", "str", "int"])
def test_state_that_is_not_a_mapping_refuses_rather_than_allowing(protect, state):
    """Relations live in ``state``, so without a readable one the guard cannot
    rule a protected subtask in or out. It must answer the way it answers a
    failed refresh - refuse - and not fall through to an empty index, which
    would let an unnamed protected task be deleted with no way back.

    Asserting merely that an error came back would also pass on a crash: the
    tool catches the AttributeError and returns an error payload too. So this
    pins the outcome, and that the delete never went out."""
    client = MagicMock()
    client.state = state
    client.get_by_id.side_effect = lambda i, *a, **k: {}
    with patch(
        "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
    ):
        result = json.loads(
            asyncio.run(ticktick_delete_tasks(["ffff1111ffff1111ffff1111"], project_id="p1"))
        )
    assert result.get("outcome") == "protection_unverifiable"
    assert "is not readable" in result["error"]
    # Retrying a state the client cannot hold does not fix it, so this path
    # must not hand back the failed-refresh advice.
    assert "Retry once" not in result["error"]
    client.task.delete.assert_not_called()


def test_protected_mode_costs_one_sync_per_structural_call(protect):
    """The guard and the tool each force a refresh, and nothing between them
    touches the server. Dropping the handoff is invisible to every other test -
    behaviour is unchanged, only the cost - so it needs its own assertion."""
    for call, sent in (
        (lambda: ticktick_delete_tasks(["ffff1111ffff1111ffff1111"], project_id="p1"), "delete"),
        (lambda: ticktick_move_task("ffff1111ffff1111ffff1111", "p1"), "move"),
        (lambda: ticktick_make_subtask(OTHER, "ffff1111ffff1111ffff1111"), "make_subtask"),
    ):
        client = MagicMock()
        client.state = {"tasks": [], "projects": [{"id": "p1", "name": "P"}]}
        client.inbox_id = "inbox1"
        client.get_by_id.side_effect = lambda i, *a, **k: {"id": i, "projectId": "p1", "title": "T"}
        with patch(
            "ticktick_mcp.tools.task_tools.TickTickClientSingleton.get_client", return_value=client
        ):
            asyncio.run(call())
        assert client.sync.call_count == 1, f"{sent} synced {client.sync.call_count}x"
