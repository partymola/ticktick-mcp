"""Resolving a project reference (id or name) to a project id.

Lives outside ``tools/`` because every tool group needs it and none of them
should import another.
"""

from __future__ import annotations

from typing import Optional

from .freshness import ensure_fresh
from .helpers import ToolLogicError


def is_known_project_id(client, value: Optional[str]) -> bool:
    """True if ``value`` is already a project id in local state (or the inbox).

    Ids win regardless of freshness, so a caller that passed one needs no sync
    to resolve it - callers use this to skip a refresh they would gain nothing
    from, and to tell "resolved to an id" from "passed through unresolved".
    """
    if not isinstance(value, str) or not value.strip():
        return False
    projects = [
        p for p in (getattr(client, "state", {}).get("projects") or []) if isinstance(p, dict)
    ]
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

    projects = [
        p for p in (getattr(client, "state", {}).get("projects") or []) if isinstance(p, dict)
    ]
    inbox_id = getattr(client, "inbox_id", None)
    folded = wanted.casefold()
    matches = [
        p["id"]
        for p in projects
        if isinstance(p.get("name"), str) and p["name"].strip().casefold() == folded
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
