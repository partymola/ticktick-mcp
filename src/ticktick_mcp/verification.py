"""Read-after-write verification for TickTick API mutations.

This module is intentionally free of package-internal imports so it can be
tested without triggering the TickTick client/config import chain.
"""

import logging


def verify_mutation(operation: str, expected: dict, actual: dict) -> list:
    """
    Verify that API response matches what was requested.
    Returns list of mismatch descriptions. Empty list = all verified.

    Checks exact value for title/content/priority.
    Checks presence (non-None) for date/time fields where format may differ.
    """
    if not isinstance(actual, dict):
        return [f"API returned non-dict response: {type(actual)}"]

    mismatches = []

    EXACT_FIELDS = {'title', 'content', 'priority'}
    PRESENCE_FIELDS = {'dueDate', 'startDate', 'timeZone', 'reminders', 'repeat', 'repeatFlag'}

    for field in EXACT_FIELDS:
        if field in expected and expected[field] is not None:
            actual_val = actual.get(field)
            if actual_val != expected[field]:
                mismatches.append(
                    f"{field}: sent '{expected[field]}' but API returned '{actual_val}'"
                )

    for field in PRESENCE_FIELDS:
        if field in expected and expected[field] is not None:
            if actual.get(field) is None:
                mismatches.append(
                    f"{field}: was set but API returned None (data may be lost)"
                )

    if mismatches:
        logging.warning(f"Verification failed after {operation}: {'; '.join(mismatches)}")

    return mismatches
