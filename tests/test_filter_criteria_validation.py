"""Every ``filter_criteria`` value is checked before the query runs.

An unchecked criterion does not fail visibly. It filters nothing, or filters
everything, and the tool answers with a plausible task list. A model reads
that as "no tasks match" rather than "you sent something invalid", which is a
confident wrong answer in a tool whose whole job is answering what is there.
"""

import asyncio
import datetime
import json
import time
import unicodedata
from unittest.mock import MagicMock, patch

import pytest

from ticktick_mcp import freshness
from ticktick_mcp.tools.filter_tools import (
    _RECOGNISED_KEYS,
    _build_property_filter,
    ticktick_filter_tasks,
)


def run(coro):
    return asyncio.run(coro)


def _client_with_projects(*projects, inbox_id="inbox-1"):
    """A client whose local state holds exactly ``projects``.

    ``state`` must be a real dict: the resolver iterates it, and a MagicMock
    attribute is truthy and not iterable in the way the code expects.
    """
    client = MagicMock()
    client.state = {"projects": list(projects)}
    client.inbox_id = inbox_id
    return client


def _project(pid, name):
    return {"id": pid, "name": name}


# ---------------------------------------------------------------------------
# Criteria that need no client
# ---------------------------------------------------------------------------


class TestTheCriteriaThatNeedNoClient:
    """``_build_property_filter`` refuses a bad value instead of dropping it."""

    def test_an_unrecognised_criterion_is_refused_and_named(self):
        with pytest.raises(ValueError) as exc:
            _build_property_filter({"proejct_id": "p1"})
        assert "proejct_id" in str(exc.value)

    def test_the_refusal_names_every_criterion_that_is_recognised(self):
        """A caller that mistyped a key needs the right spelling, not a no.

        Read off the constant rather than a second copy of the list: naming a
        subset would let any key silently drop out of the message.
        """
        with pytest.raises(ValueError) as exc:
            _build_property_filter({"nonsense": 1})
        message = str(exc.value)
        for known in _RECOGNISED_KEYS:
            assert known in message, f"{known} is accepted but not offered"

    def test_every_recognised_key_is_actually_read(self):
        """A key in the set that nothing reads is the original defect wearing
        a different hat: accepted, ignored, and answered with a task list.

        Handing each one a value no criterion can accept proves something
        looks at it. A key that is merely tolerated raises nothing.
        """
        for key in _RECOGNISED_KEYS:
            with pytest.raises(ValueError, match=key):
                _build_property_filter({key: object()})

    def test_every_documented_criterion_is_accepted(self):
        """The guard must not refuse what the docstring advertises."""
        pf, tz, sort = _build_property_filter(
            {
                "status": "completed",
                "project_id": "p1",
                "priority": 3,
                "tag_label": "work",
                "tz": "Europe/London",
                "sort_by_priority": True,
                "due_start_date": "2024-08-01",
                "due_end_date": "2024-08-31",
                "completion_start_date": "2024-08-01",
                "completion_end_date": "2024-08-31",
            }
        )
        assert pf.status == "completed"
        assert pf.priority == 3
        assert sort is True
        assert tz is not None

    @pytest.mark.parametrize("bad", [2, 4, 99, -1])
    def test_a_priority_outside_ticktick_s_scale_is_refused(self, bad):
        with pytest.raises(ValueError) as exc:
            _build_property_filter({"priority": bad})
        assert "priority" in str(exc.value).lower()

    def test_the_priority_refusal_names_the_values_that_work(self):
        with pytest.raises(ValueError) as exc:
            _build_property_filter({"priority": 99})
        message = str(exc.value)
        for allowed in ("0", "1", "3", "5"):
            assert allowed in message

    @pytest.mark.parametrize("good", [0, 1, 3, 5])
    def test_every_real_priority_is_accepted(self, good):
        pf, _, _ = _build_property_filter({"priority": good})
        assert pf.priority == good

    def test_a_boolean_priority_is_refused(self):
        """``True == 1`` in Python, so a bare membership test lets it through
        and silently filters for Low priority."""
        with pytest.raises(ValueError):
            _build_property_filter({"priority": True})

    def test_a_non_numeric_priority_is_refused(self):
        """In the tool's voice: ``PropertyFilter.priority`` is ``Optional[int]``
        and pydantic's refusal is also a ``ValueError``, so asserting only
        "raises" passes with the check removed."""
        with pytest.raises(ValueError) as exc:
            _build_property_filter({"priority": "high"})
        assert "TickTick uses" in str(exc.value)

    @pytest.mark.parametrize("bad", [[], {}, ["completed"], {"status": "completed"}])
    def test_an_unhashable_status_is_refused_rather_than_crashing(self, bad):
        """Set membership on an unhashable value raises ``TypeError``, which
        is not a ``ValueError`` and so escapes the tool's handlers. Under
        ``mcp`` 2.1 a bare handler exception's message does not cross the
        wire, so the model would get an error naming only the tool.
        """
        with pytest.raises(ValueError):
            _build_property_filter({"status": bad})

    def test_an_unknown_timezone_is_refused_rather_than_dropped(self):
        """Dropping it returns real rows under a timezone never applied,
        which is worse than an empty list because nothing looks wrong."""
        with pytest.raises(ValueError) as exc:
            _build_property_filter({"tz": "Not/AZone"})
        assert "Not/AZone" in str(exc.value)

    def test_a_non_string_timezone_is_refused(self):
        """In the tool's voice, for the same reason as the priority case:
        ``PeriodFilter.tz`` is ``Optional[ZoneInfo]`` and rejects ``5`` itself."""
        with pytest.raises(ValueError) as exc:
            _build_property_filter({"tz": 5})
        assert "Expected an IANA timezone name" in str(exc.value)

    def test_a_padded_timezone_is_trimmed_like_the_text_criteria(self):
        pf, tz, _ = _build_property_filter({"tz": "  Europe/London  "})
        assert str(tz) == "Europe/London"
        assert str(pf.due_date_filter.tz) == "Europe/London"

    def test_a_real_timezone_still_reaches_both_date_filters(self):
        pf, tz, _ = _build_property_filter({"tz": "Europe/London"})
        assert str(tz) == "Europe/London"
        assert str(pf.due_date_filter.tz) == "Europe/London"
        assert str(pf.completion_date_filter.tz) == "Europe/London"

    @pytest.mark.parametrize(
        "field",
        [
            "due_start_date",
            "due_end_date",
            "completion_start_date",
            "completion_end_date",
        ],
    )
    def test_an_unparseable_date_is_refused_naming_the_field(self, field):
        """An unparseable bound silently becomes no bound, so the window the
        caller asked for is not the window that ran."""
        with pytest.raises(ValueError) as exc:
            _build_property_filter({field: "not-a-date"})
        message = str(exc.value)
        assert field in message
        assert "not-a-date" in message

    @pytest.mark.parametrize(
        "field",
        [
            "due_start_date",
            "due_end_date",
            "completion_start_date",
            "completion_end_date",
        ],
    )
    def test_an_empty_date_still_means_no_bound(self, field):
        """Absent and empty are how a caller says "no bound"; neither is a
        bad value. Asserting only that it did not raise would also pass if the
        empty string were parsed into some bound, so the bound itself is read.
        """
        pf, _, _ = _build_property_filter({field: ""})
        which, _, edge = field.partition("_")
        window = pf.due_date_filter if which == "due" else pf.completion_date_filter
        assert getattr(window, edge) is None

    def test_a_real_date_is_still_parsed(self):
        pf, _, _ = _build_property_filter({"due_start_date": "2024-08-01"})
        assert pf.due_date_filter.start_date.day == 1

    @pytest.mark.parametrize(
        "value", ["2026-09-01T25:99:99", "2026-09-01garbage", "2026-09-01 is when I want it"]
    )
    def test_a_bound_is_read_to_the_day_and_the_rest_ignored(self, value):
        """Documented in AGENTS.md, so it carries a test: the parse falls back
        to the first ten characters, and comparison is day-granular. A future
        tightening should turn this red rather than pass unnoticed.
        """
        pf, _, _ = _build_property_filter({"due_start_date": value})
        assert pf.due_date_filter.start_date.date() == datetime.date(2026, 9, 1)

    @pytest.mark.parametrize("prefix", ["due", "completion"])
    def test_a_window_that_ends_before_it_begins_is_refused(self, prefix):
        """It matches nothing, which is the empty answer this check exists
        to stop."""
        with pytest.raises(ValueError) as exc:
            _build_property_filter(
                {f"{prefix}_start_date": "2026-09-10", f"{prefix}_end_date": "2026-09-01"}
            )
        assert prefix in str(exc.value)

    def test_a_window_of_one_day_is_not_refused(self):
        """The boundary the refusal must not eat: start equal to end."""
        pf, _, _ = _build_property_filter(
            {"due_start_date": "2026-09-01", "due_end_date": "2026-09-01"}
        )
        assert pf.due_date_filter.start_date.date() == pf.due_date_filter.end_date.date()

    def test_times_running_backwards_inside_one_day_are_not_refused(self):
        """`contains()` compares dates, so this window means that whole day and
        is answerable. Comparing the datetimes instead would refuse it, and the
        one-day test above cannot tell the two comparisons apart."""
        pf, _, _ = _build_property_filter(
            {"due_start_date": "2026-09-01T18:00", "due_end_date": "2026-09-01T09:00"}
        )
        assert pf.due_date_filter.start_date.date() == datetime.date(2026, 9, 1)

    def test_the_inverted_window_refusal_quotes_what_the_caller_sent(self):
        """`PeriodFilter` converts an aware bound to server-local naive, so a
        message built from the parsed value names a date nobody typed, and for
        a mixed naive and aware pair it varies by host."""
        with pytest.raises(ValueError) as exc:
            _build_property_filter({"due_start_date": "2026-09-10", "due_end_date": "2026-09-01"})
        message = str(exc.value)
        assert "'2026-09-10'" in message
        assert "'2026-09-01'" in message

    def test_a_blank_timezone_means_unset(self):
        pf, tz, _ = _build_property_filter({"tz": "   "})
        assert tz is None
        assert pf.due_date_filter.tz is None

    def test_a_null_sort_flag_means_unset_like_every_other_criterion(self):
        """A model emitting JSON writes `null` for an optional flag, and every
        other criterion reads that as absent."""
        _, _, sort = _build_property_filter({"sort_by_priority": None})
        assert sort is False

    def test_the_json_string_form_is_validated_too(self):
        """Models send the string form, and it decodes before validation, so
        it must not be a way past these checks."""
        with pytest.raises(ValueError) as exc:
            _build_property_filter('{"priority": 99}')
        assert "TickTick uses" in str(exc.value)

    def test_a_non_boolean_sort_flag_is_refused(self):
        """``bool("false")`` is True, so the string form silently sorts."""
        with pytest.raises(ValueError):
            _build_property_filter({"sort_by_priority": "false"})

    @pytest.mark.parametrize("flag", [True, False])
    def test_a_real_sort_flag_is_accepted(self, flag):
        _, _, sort = _build_property_filter({"sort_by_priority": flag})
        assert sort is flag

    def test_an_empty_project_id_is_refused(self):
        with pytest.raises(ValueError) as exc:
            _build_property_filter({"project_id": "   "})
        assert "Expected a non-empty string" in str(exc.value)

    def test_a_non_string_project_id_is_refused(self):
        """Asserting only "raises" would not pin this.

        ``PropertyFilter`` types both fields as ``str``, and a pydantic
        ``ValidationError`` is itself a ``ValueError``, so removing the check
        still raises. What it must not do is answer in pydantic's voice, naming
        an internal model instead of the criterion the caller sent.
        """
        with pytest.raises(ValueError) as exc:
            _build_property_filter({"project_id": 12})
        assert "Expected a non-empty string" in str(exc.value)

    def test_a_non_string_tag_label_is_refused(self):
        with pytest.raises(ValueError) as exc:
            _build_property_filter({"tag_label": ["work"]})
        assert "Expected a non-empty string" in str(exc.value)

    @pytest.mark.parametrize("key", ["tag_label", "project_id"])
    def test_a_padded_text_criterion_is_trimmed_rather_than_kept(self, key):
        """Checking a value without trimming it leaves the defect in place:
        a tag is matched by exact membership, so a padded one matches nothing
        and answers with the empty list this check exists to prevent."""
        pf, _, _ = _build_property_filter({key: "  work  "})
        assert getattr(pf, key) == "work"

    def test_an_empty_tag_label_is_refused(self):
        with pytest.raises(ValueError) as exc:
            _build_property_filter({"tag_label": ""})
        assert "Expected a non-empty string" in str(exc.value)


# ---------------------------------------------------------------------------
# The project reference, which needs the client
# ---------------------------------------------------------------------------


class TestTheProjectReference:
    """A project the account does not have is refused, not answered empty."""

    def test_a_project_that_does_not_exist_is_refused_rather_than_answered_empty(self):
        client = _client_with_projects(_project("p1", "Home"))
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[{"id": "t1", "status": 0, "projectId": "p1"}],
            ),
        ):
            result = run(ticktick_filter_tasks({"project_id": "no-such-project"}))

        parsed = json.loads(result)
        assert isinstance(parsed, dict), "an unknown project must not read as an empty result"
        assert parsed["status"] == "error"
        assert "no-such-project" in parsed["error"]

    def test_a_project_list_that_could_not_be_refreshed_does_not_deny_the_project(self):
        """A cache that failed to load is nothing to judge against. Refusing
        on it tells the caller their project does not exist, which is a
        statement about the account the code cannot back."""
        client = _client_with_projects()  # empty, as an unreadable state reads
        client.sync = MagicMock(side_effect=Exception("network down"))
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[],
            ),
        ):
            result = run(ticktick_filter_tasks({"project_id": "p1"}))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert parsed["outcome"] == "project_list_unverifiable"
        assert "No project matches" not in json.dumps(parsed)

    def test_a_known_project_id_is_answered_normally(self):
        client = _client_with_projects(_project("p1", "Home"))
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[{"id": "t1", "status": 0, "projectId": "p1"}],
            ),
        ):
            result = run(ticktick_filter_tasks({"project_id": "p1"}))

        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert [t["id"] for t in parsed] == ["t1"]

    def test_a_project_name_still_resolves_to_its_id(self):
        client = _client_with_projects(_project("p1", "Home"))
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[
                    {"id": "t1", "status": 0, "projectId": "p1"},
                    {"id": "t2", "status": 0, "projectId": "p2"},
                ],
            ),
        ):
            result = run(ticktick_filter_tasks({"project_id": "home"}))

        parsed = json.loads(result)
        assert [t["id"] for t in parsed] == ["t1"]

    def test_the_inbox_is_a_known_project(self):
        client = _client_with_projects(inbox_id="inbox-1")
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[{"id": "t1", "status": 0, "projectId": "inbox-1"}],
            ),
        ):
            result = run(ticktick_filter_tasks({"project_id": "Inbox"}))

        parsed = json.loads(result)
        assert [t["id"] for t in parsed] == ["t1"]

    def test_a_stale_snapshot_is_refreshed_before_the_project_is_refused(self):
        """The refusal path's refresh must be FORCED, and its result used.

        A throttled refresh returns True without syncing, so a project created
        since the last sync would be refused as non-existent. Nothing pinned
        this: the failed-refresh test above takes the same branch whether the
        call is forced or not, because a raising sync fails either way.
        """
        client = _client_with_projects()  # snapshot has not seen p1 yet
        client.sync = MagicMock(
            side_effect=lambda: client.state.__setitem__("projects", [_project("p1", "Home")])
        )
        # Inside the TTL, so an unforced ensure_fresh reports fresh and syncs nothing.
        freshness._last_sync_monotonic = time.monotonic()

        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[{"id": "t1", "status": 0, "projectId": "p1"}],
            ),
        ):
            result = run(ticktick_filter_tasks({"project_id": "p1"}))

        parsed = json.loads(result)
        assert isinstance(parsed, list), f"a forced refresh would have found p1: {parsed}"
        assert [t["id"] for t in parsed] == ["t1"]
        client.sync.assert_called()

    @pytest.mark.parametrize(
        ("stored", "typed"),
        [
            ("NFC", "NFD"),
            # The mirror is not redundant: folding only the CALLER's side
            # satisfies the first case and fails this one, and a name pasted
            # from macOS arrives decomposed.
            ("NFD", "NFC"),
        ],
    )
    def test_an_accented_project_resolves_whichever_way_it_is_spelled(self, stored, typed):
        """Two spellings of one accented name differ by code point, and
        casefold does not reconcile them. Unresolved now means a refusal
        naming the project, so the account is told something untrue."""
        client = _client_with_projects(_project("p1", unicodedata.normalize(stored, "Café")))
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[{"id": "t1", "status": 0, "projectId": "p1"}],
            ),
        ):
            result = run(
                ticktick_filter_tasks({"project_id": unicodedata.normalize(typed, "Café")})
            )

        parsed = json.loads(result)
        assert isinstance(parsed, list), f"the project exists: {parsed}"
        assert [t["id"] for t in parsed] == ["t1"]

    def test_a_project_name_stored_with_padding_still_resolves(self):
        """`_fold` strips both sides. Longstanding behaviour that moved into a
        function this change introduced, so it needs its own pin here."""
        client = _client_with_projects(_project("p1", "  Home  "))
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[{"id": "t1", "status": 0, "projectId": "p1"}],
            ),
        ):
            result = run(ticktick_filter_tasks({"project_id": "Home"}))

        parsed = json.loads(result)
        assert [t["id"] for t in parsed] == ["t1"]

    @pytest.mark.parametrize("unreadable", [None, "junk", []])
    def test_a_state_that_cannot_be_read_is_not_a_missing_project(self, unreadable):
        """A sync that succeeds but leaves nothing readable is not evidence
        about the account, so it must not answer as a denial. A truthy
        non-dict also used to reach ``.get`` and escape as an unexplained
        failure."""
        client = _client_with_projects()
        client.state = unreadable
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[],
            ),
        ):
            result = run(ticktick_filter_tasks({"project_id": "p1"}))

        parsed = json.loads(result)
        assert parsed["outcome"] == "project_list_unverifiable"
        assert "No project matches" not in json.dumps(parsed)

    def test_a_known_project_id_costs_no_forced_refresh(self):
        """An id resolves off the snapshot, so the refusal path's extra sync
        must not fire for callers who got it right."""
        client = _client_with_projects(_project("p1", "Home"))
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[],
            ),
            # `projects`, not `filter_tools`: the forced refresh lives inside
            # confirm_project_id. Patching the tool module watches the wrong
            # name and the assertion below passes over anything.
            patch("ticktick_mcp.projects.ensure_fresh", return_value=True) as fresh,
        ):
            run(ticktick_filter_tasks({"project_id": "p1"}))

        # Positionally too: ensure_fresh(client, True) is the same call.
        def forced(call):
            return call.kwargs.get("force") is True or (len(call.args) > 1 and call.args[1] is True)

        assert not any(forced(call) for call in fresh.call_args_list)

    def test_ambiguity_appearing_only_after_the_forced_refresh_is_an_error(self):
        """`_confirmed_project` resolves twice, and the second call can raise
        for the first time. `ToolLogicError` is deliberately not a
        `ValueError`, so a handler listing only `ValueError` would let it
        escape as an unhandled exception."""
        client = _client_with_projects(_project("p1", "Home"))
        client.sync = MagicMock(
            side_effect=lambda: client.state.__setitem__(
                "projects", [_project("p1", "Home"), _project("p2", "Home")]
            )
        )
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ):
            result = run(ticktick_filter_tasks({"project_id": "Elsewhere"}))

        parsed = json.loads(result)
        assert parsed["status"] == "error"

    def test_the_completed_branch_refuses_before_it_fetches(self):
        """The refusal must come before `get_completed`, which is a live call
        the bad value could never have used."""
        client = _client_with_projects(_project("p1", "Home"))
        client.task.get_completed = MagicMock(return_value=[])
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ):
            result = run(
                ticktick_filter_tasks(
                    {
                        "status": "completed",
                        "project_id": "no-such-project",
                        "completion_start_date": "2026-09-01",
                        "completion_end_date": "2026-09-30",
                    }
                )
            )

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        client.task.get_completed.assert_not_called()

    def test_an_ambiguous_project_name_is_still_an_error(self):
        client = _client_with_projects(_project("p1", "Home"), _project("p2", "Home"))
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=client,
        ):
            result = run(ticktick_filter_tasks({"project_id": "Home"}))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "ambiguous" in parsed["error"].lower()

    def test_no_project_criterion_needs_no_project_check(self):
        client = _client_with_projects()
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[{"id": "t1", "status": 0}],
            ),
        ):
            result = run(ticktick_filter_tasks({"status": "uncompleted"}))

        parsed = json.loads(result)
        assert [t["id"] for t in parsed] == ["t1"]


# ---------------------------------------------------------------------------
# The refusals reach the model as results, not as exceptions
# ---------------------------------------------------------------------------


class TestTheRefusalsReachTheCaller:
    @pytest.mark.parametrize(
        "criteria",
        [
            {"priority": 99},
            {"tz": "Not/AZone"},
            {"due_start_date": "not-a-date"},
            {"sort_by_priority": "false"},
            {"unknown_key": 1},
        ],
    )
    def test_a_refusal_is_an_error_result_rather_than_an_exception(self, criteria):
        with patch(
            "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
            return_value=_client_with_projects(),
        ):
            result = run(ticktick_filter_tasks(criteria))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert parsed["error"]

    def test_a_refusal_happens_before_any_task_is_fetched(self):
        """Validation that runs after the fetch still costs the round trip
        the bad value could never have used."""
        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=_client_with_projects(),
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[],
            ) as fetch,
        ):
            run(ticktick_filter_tasks({"priority": 99}))

        fetch.assert_not_called()


class TestTheConfirmSequenceItself:
    """Properties of `projects.confirm_project_id` that only a NAME, or only a
    part-failed refresh, can distinguish."""

    def test_a_refresh_that_populates_then_fails_is_not_treated_as_read(self):
        """`ticktick-py` writes `state["projects"]` partway through `sync`, so a
        response missing a later key leaves the list populated and then raises.
        The two copies this function was merged from disagreed here: one
        re-checked outside the refresh gate and would have answered success off
        a list it never confirmed. Refusing is the documented side."""
        client = _client_with_projects()

        def _populate_then_fail():
            client.state["projects"] = [_project("p1", "Home")]
            raise RuntimeError("sync died after writing the project list")

        client.sync = MagicMock(side_effect=_populate_then_fail)
        freshness._last_sync_monotonic = time.monotonic()

        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[],
            ),
        ):
            result = run(ticktick_filter_tasks({"project_id": "p1"}))

        parsed = json.loads(result)
        assert parsed["outcome"] == "project_list_unverifiable"

    def test_the_re_resolved_value_is_the_one_used(self):
        """A NAME, not an id. With an id the second `is_known_project_id` flips
        true off the refreshed snapshot whether or not the re-resolve's return
        value was kept, so an id can never catch the assignment being dropped.

        The warm throttle below is load-bearing, not tidy-up: without it the
        resolver's own unforced sync resolves the name on the first pass,
        `confirm_project_id` returns at its first early exit, and the limb this
        test exists to reach is never entered. The final assertion checks a
        forced refresh actually happened, so removing the throttle line fails
        the test rather than quietly emptying it.
        """
        client = _client_with_projects()
        client.sync = MagicMock(
            side_effect=lambda: client.state.__setitem__("projects", [_project("p1", "Home")])
        )
        freshness._last_sync_monotonic = time.monotonic()

        with (
            patch(
                "ticktick_mcp.tools.filter_tools.TickTickClientSingleton.get_client",
                return_value=client,
            ),
            patch(
                "ticktick_mcp.tools.filter_tools._get_all_tasks_from_ticktick",
                return_value=[{"id": "t1", "status": 0, "projectId": "p1"}],
            ),
            patch("ticktick_mcp.projects.ensure_fresh", wraps=freshness.ensure_fresh) as refreshed,
        ):
            result = run(ticktick_filter_tasks({"project_id": "Home"}))

        parsed = json.loads(result)
        assert isinstance(parsed, list), f"the refresh introduced 'Home': {parsed}"
        assert [t["id"] for t in parsed] == ["t1"]
        assert any(
            call.kwargs.get("force") or (len(call.args) > 1 and call.args[1])
            for call in refreshed.call_args_list
        ), "the forced-refresh limb was never reached, so nothing here was tested"
