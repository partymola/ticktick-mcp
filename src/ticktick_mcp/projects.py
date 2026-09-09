"""Resolving a project reference (id or name) to a project id.

Lives outside ``tools/`` because every tool group needs it. Homing it in one
tool module would deepen the one cross-import there already is
(``completion_tools`` reaches into ``filter_tools`` for ``TaskFilterer``),
which is the import to stop repeating rather than to copy.
"""

from __future__ import annotations

import unicodedata
from typing import Optional

from .freshness import ensure_fresh
from .helpers import ToolLogicError


def _fold(name: str) -> str:
    """Trim, NFC-normalise, then casefold.

    Normalisation is not cosmetic here: an accented name stored composed and
    typed decomposed differs by code point, and casefold does not reconcile
    the two. Without it such a name resolves to nothing, which callers that
    confirm the result report as "no project matches" and so as a claim about
    the account that is not true.
    """
    return unicodedata.normalize("NFC", name.strip()).casefold()


def _project_entries(client) -> list:
    """The project dicts in local state, or none if it cannot be read.

    Anything that is not a dict is treated as nothing to match against, which
    covers a client carrying ``state`` as ``None`` and one carrying a truthy
    non-dict that would otherwise reach ``.get`` and raise. Entries may still
    lack ``id`` or ``name``, which is why both callers re-check.
    """
    state = getattr(client, "state", None)
    if not isinstance(state, dict):
        # A truthy non-dict reaches .get() and raises, which escapes the tools
        # as an unexplained failure rather than as "nothing to match against".
        return []
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

    An id, or anything this cannot resolve, is returned untouched. Local state
    lags, id formats are the server's business, and a resolver that rejected
    what it did not recognise would break callers that work now. What becomes
    of an unresolved value is the caller's business: some tools fail in
    ``ticktick-py``'s own local lookup, some send it to the API, and some
    refuse it before either.

    The one failure is ambiguity. Two projects sharing a name raise rather
    than resolve, because picking either files the task somewhere the caller
    will not think to look, and sync order is not a tie-break anyone chose.
    Names that differ only by Unicode normal form share a name for this
    purpose, so they raise too.
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
    folded = _fold(wanted)
    matches = [
        p["id"]
        for p in projects
        # An entry with no usable id cannot be resolved to, so it does not
        # count as a match - and must not raise KeyError on the way past.
        if isinstance(p.get("id"), str)
        and isinstance(p.get("name"), str)
        and _fold(p["name"]) == folded
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
