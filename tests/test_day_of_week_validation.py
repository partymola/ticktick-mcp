"""Tests for day-of-week validation in create_task and update_task."""

import datetime
import pytest
import zoneinfo

from ticktick_mcp.helpers import ToolLogicError
from ticktick_mcp.tools.task_tools import _validate_day_of_week, TaskObject


class TestValidateDayOfWeek:
    """Unit tests for _validate_day_of_week helper."""

    def test_matching_day_passes(self):
        # 2026-04-13 is a Monday
        _validate_day_of_week("2026-04-13T20:45:00+01:00", "Monday", "dueDate")

    def test_mismatched_day_raises(self):
        # 2026-04-14 is Tuesday, not Monday
        with pytest.raises(ToolLogicError, match="Tuesday.*not Monday"):
            _validate_day_of_week("2026-04-14T20:45:00+01:00", "Monday", "dueDate")

    def test_case_insensitive(self):
        _validate_day_of_week("2026-04-13T10:00:00+01:00", "monday", "dueDate")
        _validate_day_of_week("2026-04-13T10:00:00+01:00", "MONDAY", "dueDate")

    def test_whitespace_tolerance(self):
        _validate_day_of_week("2026-04-13T10:00:00+01:00", "  Monday  ", "dueDate")

    def test_no_validation_when_expected_day_is_none(self):
        # Should not raise - None means no check
        # (The function is only called when expectedDayOfWeek is set;
        # this tests that None date_value is a no-op)
        _validate_day_of_week(None, "Monday", "dueDate")

    def test_datetime_object_input(self):
        dt = datetime.datetime(2026, 4, 13, 20, 45, tzinfo=zoneinfo.ZoneInfo("Europe/London"))
        _validate_day_of_week(dt, "Monday", "dueDate")

    def test_datetime_object_mismatch(self):
        dt = datetime.datetime(2026, 4, 14, 20, 45, tzinfo=zoneinfo.ZoneInfo("Europe/London"))
        with pytest.raises(ToolLogicError, match="Tuesday.*not Monday"):
            _validate_day_of_week(dt, "Monday", "dueDate")

    def test_timezone_resolution_changes_day(self):
        # 2026-04-14 00:30 UTC is still 2026-04-13 in US Eastern (UTC-4 in April)
        # So in America/New_York it's Sunday April 13... wait, April 13 is Monday.
        # Let me pick a better example:
        # 2026-04-14 00:30 UTC = 2026-04-14 01:30 BST (still Tuesday in London)
        # But 2026-04-13 23:30 UTC = 2026-04-14 00:30 BST (Tuesday in London)
        # vs 2026-04-13 in UTC (Monday)

        # UTC date says Monday April 13 at 23:30
        utc_str = "2026-04-13T23:30:00+00:00"
        # In UTC, it's Monday
        _validate_day_of_week(utc_str, "Monday", "dueDate")
        # In Europe/London (BST = UTC+1), 23:30 UTC = 00:30 BST on April 14 = Tuesday
        with pytest.raises(ToolLogicError, match="Tuesday in Europe/London.*not Monday"):
            _validate_day_of_week(utc_str, "Monday", "dueDate", "Europe/London")

    def test_timezone_in_error_message(self):
        with pytest.raises(ToolLogicError, match="in Europe/London"):
            _validate_day_of_week(
                "2026-04-14T20:45:00+01:00", "Monday", "dueDate", "Europe/London"
            )

    def test_non_english_day_name_rejected(self):
        # "Lunes" is Spanish for Monday - should be rejected with a clear message
        with pytest.raises(ToolLogicError, match="Invalid expectedDayOfWeek.*Lunes.*English"):
            _validate_day_of_week("2026-04-13T20:45:00+01:00", "Lunes", "dueDate")

    def test_invalid_timezone_falls_back(self):
        # Bad timezone name should not crash, just skip tz conversion
        _validate_day_of_week("2026-04-13T20:45:00+01:00", "Monday", "dueDate", "Not/A/Timezone")

    def test_error_includes_field_name(self):
        with pytest.raises(ToolLogicError, match="dueDate"):
            _validate_day_of_week("2026-04-14T10:00:00+01:00", "Monday", "dueDate")

    def test_all_days_of_week(self):
        # Week of 2026-04-13 (Monday) through 2026-04-19 (Sunday)
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        for i, day in enumerate(days):
            date_str = f"2026-04-{13 + i}T12:00:00+01:00"
            _validate_day_of_week(date_str, day, "dueDate")


class TestTaskObjectExpectedDayOfWeek:
    """Test that expectedDayOfWeek is excluded from serialization."""

    def test_excluded_from_model_dump(self):
        task = TaskObject(
            id="test123",
            projectId="proj456",
            dueDate=datetime.datetime(2026, 4, 13, 20, 45, tzinfo=zoneinfo.ZoneInfo("Europe/London")),
            expectedDayOfWeek="Monday",
            timeZone="Europe/London",
        )
        dumped = task.model_dump(mode="json")
        assert "expectedDayOfWeek" not in dumped

    def test_excluded_from_model_dump_exclude_unset(self):
        task = TaskObject(
            id="test123",
            projectId="proj456",
            dueDate=datetime.datetime(2026, 4, 13, 20, 45, tzinfo=zoneinfo.ZoneInfo("Europe/London")),
            expectedDayOfWeek="Monday",
            timeZone="Europe/London",
        )
        dumped = task.model_dump(exclude_unset=True)
        assert "expectedDayOfWeek" not in dumped

    def test_present_in_model_fields_set(self):
        task = TaskObject(
            id="test123",
            projectId="proj456",
            expectedDayOfWeek="Monday",
        )
        assert "expectedDayOfWeek" in task.model_fields_set
