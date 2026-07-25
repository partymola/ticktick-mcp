"""Resolving a project reference (id or name) to a project id.

Lives outside ``tools/`` because every tool group needs it. Homing it in one
tool module would deepen the one cross-import there already is
(``completion_tools`` reaches into ``filter_tools`` for ``TaskFilterer``),
which is the import to stop repeating rather than to copy.
"""

from __future__ import annotations

from typing import Optional

from .freshness import ensure_fresh
from .helpers import ToolLogicError


def _project_entries(client) -> list:
    """The project dicts in local state.

    A falsy ``state`` is coalesced rather than defaulted: a client carrying it
    as None satisfies ``getattr(client, "state", {})``, which then hands the
    None to the lookup after it. Entries may still lack ``id`` or ``name``,
    which is why both callers re-check.
    """
    state = getattr(client, "state", None) or {}
    return [p for p in (state.get("projects") or []) if isinstance(p, dict)]


def is_known_project_id(client, value: Optional[str]) -> bool:
    """True if ``value`` is already a project id in local state (or the inbox).

    Ids win regardless of freshness, so a caller that passed one needs no sync
    to resolve it - callers use this to skip a refresh they would gain nothing
    from, and to tell "resolved to an id" from "passed through unresolved".
    """
    if not isinstance(value, str) or not value.strip():
        return False
    projects = _project_entries(client)
    known = {p.get("id") for p in projects}
    known.add(getattr(client, "inbox_id", None))
    return value.strip() in known


def resolve_project_id(client, value: Optional[str]) -> Optional[str]:
    """Accept a project name where an id is expected, and return the id.

    Purely additive: an id, or anything this cannot resolve, is returned
    untouched and reaches the API exactly as it does today. Local state lags,
    id formats are the server's business, and a resolver that rejected what it
    did not recognise would break callers that work now.

    The single new failure is ambiguity. Two projects sharing a name raise
    rather than resolve, because picking either files the task somewhere the
    caller will not think to look, and sync order is not a tie-break anyone
    chose.
    """
    if not isinstance(value, str) or not value.strip():
        return value

    wanted = value.strip()
    if is_known_project_id(client, wanted):
        return wanted

    ensure_fresh(client)

    # Again after the sync: it may have introduced the project this id names,
    # and a project titled with that id would otherwise win the name match.
    if is_known_project_id(client, wanted):
        return wanted

    projects = _project_entries(client)
    inbox_id = getattr(client, "inbox_id", None)
    folded = wanted.casefold()
    matches = [
        p["id"]
        for p in projects
        # An entry with no usable id cannot be resolved to, so it does not
        # count as a match - and must not raise KeyError on the way past.
        if isinstance(p.get("id"), str)
        and isinstance(p.get("name"), str)
        and p["name"].strip().casefold() == folded
    ]
    # state["projects"] is projectProfiles and excludes the inbox, so this
    # cannot double-match a user project that is also called "Inbox".
    if inbox_id and folded == "inbox":
        matches.append(inbox_id)

    if len(matches) > 1:
        raise ToolLogicError(
            f"Project name {wanted!r} is ambiguous - {len(matches)} projects share it "
            f"({', '.join(sorted(matches))}). Pass the id of the one you mean."
        )
    return matches[0] if matches else value
