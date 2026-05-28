"""Tests for tools/conversion_tools.py.

Covers ticktick_convert_datetime_to_ticktick_format.
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch

from ticktick_mcp.tools.conversion_tools import (
    ticktick_convert_datetime_to_ticktick_format,
)


def run(coro):
    return asyncio.run(coro)


class TestConvertDatetime:

    def test_valid_iso_with_timezone(self):
        """A normal ISO datetime + valid tz returns a JSON with 'ticktick_format'."""
        with patch(
            "ticktick_mcp.tools.conversion_tools.convert_date_to_tick_tick_format",
            return_value="2024-07-26T18:00:00.000+0900",
        ) as mock_conv:
            result = run(ticktick_convert_datetime_to_ticktick_format(
                datetime_iso_string="2024-07-26T18:00:00",
                tz="Asia/Seoul",
            ))

        parsed = json.loads(result)
        assert parsed == {"ticktick_format": "2024-07-26T18:00:00.000+0900"}
        # Verify the underlying converter was invoked with the parsed datetime and tz
        args, _ = mock_conv.call_args
        assert args[1] == "Asia/Seoul"

    def test_date_only_string(self):
        """A date-only ISO string is parsed and converted."""
        with patch(
            "ticktick_mcp.tools.conversion_tools.convert_date_to_tick_tick_format",
            return_value="2024-08-15T00:00:00.000+0900",
        ):
            result = run(ticktick_convert_datetime_to_ticktick_format(
                datetime_iso_string="2024-08-15",
                tz="Asia/Seoul",
            ))

        parsed = json.loads(result)
        assert "ticktick_format" in parsed
        assert parsed["ticktick_format"].startswith("2024-08-15")

    def test_iso_string_with_offset(self):
        """An ISO string that already has a UTC offset is handled."""
        with patch(
            "ticktick_mcp.tools.conversion_tools.convert_date_to_tick_tick_format",
            return_value="2024-07-26T18:00:00.000+0200",
        ):
            result = run(ticktick_convert_datetime_to_ticktick_format(
                datetime_iso_string="2024-07-26T18:00:00+02:00",
                tz="Europe/Paris",
            ))

        parsed = json.loads(result)
        assert parsed["ticktick_format"] == "2024-07-26T18:00:00.000+0200"

    def test_invalid_iso_string_returns_error(self):
        """An unparseable ISO string returns an error JSON."""
        result = run(ticktick_convert_datetime_to_ticktick_format(
            datetime_iso_string="not-a-date",
            tz="Europe/London",
        ))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Invalid datetime format" in parsed["error"]

    def test_empty_string_returns_error(self):
        """Empty input string returns an error JSON."""
        result = run(ticktick_convert_datetime_to_ticktick_format(
            datetime_iso_string="",
            tz="Europe/London",
        ))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "error" in parsed

    def test_invalid_timezone_returns_error(self):
        """An invalid tz name causes the converter to raise; we return a JSON error.

        Whether this surfaces as ValueError (caught directly) or generic
        Exception depends on the underlying ticktick-py implementation, but
        either way it must come back as {'status': 'error', 'error': ...}.
        """
        with patch(
            "ticktick_mcp.tools.conversion_tools.convert_date_to_tick_tick_format",
            side_effect=ValueError("Unknown timezone"),
        ):
            result = run(ticktick_convert_datetime_to_ticktick_format(
                datetime_iso_string="2024-07-26T18:00:00",
                tz="Not/A/Zone",
            ))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Invalid datetime format" in parsed["error"]

    def test_other_exception_returns_error(self):
        """Non-ValueError exceptions are caught by the generic except branch."""
        with patch(
            "ticktick_mcp.tools.conversion_tools.convert_date_to_tick_tick_format",
            side_effect=RuntimeError("something else"),
        ):
            result = run(ticktick_convert_datetime_to_ticktick_format(
                datetime_iso_string="2024-07-26T18:00:00",
                tz="Europe/London",
            ))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Conversion failed" in parsed["error"]
        assert "something else" in parsed["error"]

    def test_response_is_valid_json(self):
        """Every code path must return a parseable JSON string."""
        # Success path
        with patch(
            "ticktick_mcp.tools.conversion_tools.convert_date_to_tick_tick_format",
            return_value="x",
        ):
            result = run(ticktick_convert_datetime_to_ticktick_format(
                datetime_iso_string="2024-01-01",
                tz="UTC",
            ))
        json.loads(result)  # must not raise

        # Error path
        result_err = run(ticktick_convert_datetime_to_ticktick_format(
            datetime_iso_string="bogus",
            tz="UTC",
        ))
        json.loads(result_err)  # must not raise


class TestConvertDatetimeRealLibrary:
    """Integration-ish tests that exercise the real ticktick-py converter."""

    def test_real_conversion_returns_ticktick_format(self):
        """Without mocking the underlying converter, output should follow
        TickTick's format: 'YYYY-MM-DDTHH:mm:ss+ZZZZ' (UTC-shifted)."""
        result = run(ticktick_convert_datetime_to_ticktick_format(
            datetime_iso_string="2024-07-26T18:00:00",
            tz="Europe/London",
        ))

        parsed = json.loads(result)
        assert "ticktick_format" in parsed
        # The library converts to UTC and emits a +0000 offset
        assert parsed["ticktick_format"].endswith("+0000")

    def test_real_unknown_timezone_returns_error(self):
        """An unknown IANA tz triggers UnknownTimeZoneError (a KeyError subclass)
        which goes through the generic except branch and is reported as an error."""
        result = run(ticktick_convert_datetime_to_ticktick_format(
            datetime_iso_string="2024-07-26T18:00:00",
            tz="Made/Up/Zone",
        ))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        # NOTE: Known quirk - UnknownTimeZoneError is NOT a ValueError, so it
        # is caught by the generic exception handler (the "Conversion failed"
        # branch), not the "Invalid datetime format or timezone" branch.
        assert "Conversion failed" in parsed["error"]

    def test_real_invalid_iso_returns_value_error_branch(self):
        """A genuinely invalid ISO string is a ValueError from fromisoformat()."""
        result = run(ticktick_convert_datetime_to_ticktick_format(
            datetime_iso_string="2024-13-99",
            tz="Europe/London",
        ))

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Invalid datetime format" in parsed["error"]
