"""Date format conversion tool.

TickTick stores dates in a specific compact ISO 8601 variant ("...+0000",
no colon in the offset). Agents typically have a normal ISO 8601 string
plus a timezone in hand; this tool converts between the two by delegating
to ``ticktick.helpers.time_methods.convert_date_to_tick_tick_format``.
"""

import datetime
import logging

from ticktick.helpers.time_methods import convert_date_to_tick_tick_format

from ..helpers import format_response
from ..mcp_instance import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
async def ticktick_convert_datetime_to_ticktick_format(
    datetime_iso_string: str,
    tz: str,
) -> str:
    """Convert an ISO 8601 datetime string into TickTick's storage format.

    TickTick uses ``YYYY-MM-DDTHH:MM:SS+0000`` (UTC offset, no colon).

    Args:
        datetime_iso_string (str): ISO 8601 datetime, e.g.
            ``"2026-04-13T20:45:00+01:00"``. Naive strings are accepted
            and interpreted in ``tz``.
        tz (str): IANA timezone name used for the UTC conversion, e.g.
            ``"Europe/London"`` or ``"America/Los_Angeles"``.

    Returns:
        On success: ``{"ticktick_format": "2026-04-13T19:45:00+0000"}``.
        On parse error: ``{"error": "Invalid datetime format...",
        "status": "error"}``. On any other conversion error:
        ``{"error": "Conversion failed: ...", "status": "error"}``.

    Agent Usage Guide:
        - Call this when you need to set ``startDate`` or ``dueDate`` on
          a task dict by hand. The ``ticktick_create_task`` and
          ``ticktick_update_task`` tools accept ISO strings directly and
          run this conversion internally.

    Example:
        ticktick_convert_datetime_to_ticktick_format(
            datetime_iso_string="2026-04-13T20:45:00+01:00",
            tz="Europe/London",
        )
    """
    if not datetime_iso_string:
        return format_response(
            {
                "error": "Invalid datetime format or timezone: empty input",
                "status": "error",
            }
        )

    try:
        parsed = datetime.datetime.fromisoformat(datetime_iso_string)
    except (TypeError, ValueError) as exc:
        return format_response(
            {
                "error": f"Invalid datetime format or timezone: {exc}",
                "status": "error",
            }
        )

    try:
        formatted = convert_date_to_tick_tick_format(parsed, tz)
    except ValueError as exc:
        return format_response(
            {
                "error": f"Invalid datetime format or timezone: {exc}",
                "status": "error",
            }
        )
    except Exception as exc:
        return format_response({"error": f"Conversion failed: {exc}", "status": "error"})

    return format_response({"ticktick_format": formatted})
