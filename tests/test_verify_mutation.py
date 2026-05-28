"""Tests for _verify_mutation: read-after-write verification of TickTick API responses."""

import pytest

from ticktick_mcp.verification import verify_mutation as _verify_mutation


class TestVerifyMutationExactFields:
    """Tests for fields checked by exact value match (title, content, priority)."""

    def test_matching_title_no_warning(self):
        expected = {"title": "Buy groceries"}
        actual = {"title": "Buy groceries"}
        assert _verify_mutation("create", expected, actual) == []

    def test_mismatched_title_warns(self):
        expected = {"title": "Buy groceries"}
        actual = {"title": "Buy something else"}
        result = _verify_mutation("create", expected, actual)
        assert len(result) == 1
        assert "title" in result[0]

    def test_matching_content_no_warning(self):
        expected = {"content": "Milk, eggs, bread"}
        actual = {"content": "Milk, eggs, bread"}
        assert _verify_mutation("update", expected, actual) == []

    def test_mismatched_content_warns(self):
        expected = {"content": "Milk, eggs, bread"}
        actual = {"content": "Different content"}
        result = _verify_mutation("update", expected, actual)
        assert len(result) == 1
        assert "content" in result[0]

    def test_matching_priority_no_warning(self):
        expected = {"priority": 5}
        actual = {"priority": 5}
        assert _verify_mutation("create", expected, actual) == []

    def test_mismatched_priority_warns(self):
        expected = {"priority": 5}
        actual = {"priority": 0}
        result = _verify_mutation("update", expected, actual)
        assert len(result) == 1
        assert "priority" in result[0]

    def test_none_expected_value_skipped(self):
        """Fields set to None in expected should not be checked."""
        expected = {"title": None, "content": None, "priority": None}
        actual = {"title": "Anything", "content": "Whatever"}
        assert _verify_mutation("create", expected, actual) == []

    def test_field_missing_from_expected_not_checked(self):
        """Fields not in expected dict should not be checked."""
        expected = {"title": "Test"}
        actual = {"title": "Test", "content": "Bonus content"}
        assert _verify_mutation("create", expected, actual) == []


class TestVerifyMutationPresenceFields:
    """Tests for fields checked by presence (dueDate, startDate, etc.)."""

    def test_duedate_present_no_warning(self):
        expected = {"dueDate": "2024-08-01T17:00:00+0000"}
        actual = {"dueDate": "2024-08-01T17:00:00.000+0000"}  # format may differ
        assert _verify_mutation("create", expected, actual) == []

    def test_duedate_missing_warns(self):
        """Reproduces bug #3: create_task drops dates."""
        expected = {"dueDate": "2024-08-01T17:00:00+0000"}
        actual = {"dueDate": None}
        result = _verify_mutation("create", expected, actual)
        assert len(result) == 1
        assert "dueDate" in result[0]
        assert "None" in result[0]

    def test_duedate_absent_from_response_warns(self):
        expected = {"dueDate": "2024-08-01T17:00:00+0000"}
        actual = {}  # field completely missing
        result = _verify_mutation("create", expected, actual)
        assert len(result) == 1
        assert "dueDate" in result[0]

    def test_startdate_present_no_warning(self):
        expected = {"startDate": "2024-08-01T09:00:00+0000"}
        actual = {"startDate": "2024-08-01T09:00:00.000+0000"}
        assert _verify_mutation("create", expected, actual) == []

    def test_startdate_missing_warns(self):
        expected = {"startDate": "2024-08-01T09:00:00+0000"}
        actual = {"startDate": None}
        result = _verify_mutation("create", expected, actual)
        assert len(result) == 1
        assert "startDate" in result[0]

    def test_reminders_present_no_warning(self):
        expected = {"reminders": ["TRIGGER:-PT30M"]}
        actual = {"reminders": ["TRIGGER:-PT30M"]}
        assert _verify_mutation("create", expected, actual) == []

    def test_reminders_missing_warns(self):
        expected = {"reminders": ["TRIGGER:-PT30M"]}
        actual = {"reminders": None}
        result = _verify_mutation("create", expected, actual)
        assert len(result) == 1
        assert "reminders" in result[0]

    def test_timezone_present_no_warning(self):
        expected = {"timeZone": "Europe/London"}
        actual = {"timeZone": "Europe/London"}
        assert _verify_mutation("create", expected, actual) == []

    def test_timezone_missing_warns(self):
        expected = {"timeZone": "Europe/London"}
        actual = {"timeZone": None}
        result = _verify_mutation("create", expected, actual)
        assert len(result) == 1
        assert "timeZone" in result[0]

    def test_none_presence_field_skipped(self):
        """If expected field is None, don't check presence."""
        expected = {"dueDate": None, "startDate": None}
        actual = {}
        assert _verify_mutation("create", expected, actual) == []


class TestVerifyMutationBugReproductions:
    """Tests that reproduce the 3 historical MCP bugs to verify they would be caught."""

    def test_bug2_update_wipes_dates_when_changing_priority(self):
        """Bug #2: updating priority wiped dueDate/startDate/reminders."""
        expected = {
            "priority": 5,
            "dueDate": "2024-08-01T17:00:00+0000",
            "startDate": "2024-08-01T09:00:00+0000",
            "reminders": ["TRIGGER:-PT30M"],
        }
        # Simulates the buggy API response where dates got wiped
        actual = {
            "priority": 5,
            "dueDate": None,
            "startDate": None,
            "reminders": None,
        }
        result = _verify_mutation("update", expected, actual)
        assert len(result) == 3  # dueDate, startDate, reminders
        fields_warned = [r.split(":")[0] for r in result]
        assert "dueDate" in fields_warned
        assert "startDate" in fields_warned
        assert "reminders" in fields_warned

    def test_bug3_create_drops_dates_and_reminders(self):
        """Bug #3: create_task builder silently dropped dates/reminders."""
        expected = {
            "title": "Team Meeting",
            "dueDate": "2024-08-01T17:00:00+0000",
            "reminders": ["TRIGGER:-PT15M"],
            "priority": 3,
        }
        # Simulates the buggy response where dates/reminders were dropped
        actual = {
            "title": "Team Meeting",
            "dueDate": None,
            "reminders": None,
            "priority": 3,
        }
        result = _verify_mutation("create", expected, actual)
        assert len(result) == 2  # dueDate, reminders
        fields_warned = [r.split(":")[0] for r in result]
        assert "dueDate" in fields_warned
        assert "reminders" in fields_warned

    def test_all_fields_correct_no_warnings(self):
        """Happy path: all fields match."""
        expected = {
            "title": "Team Meeting",
            "content": "Review project timelines",
            "dueDate": "2024-08-01T17:00:00+0000",
            "startDate": "2024-08-01T09:00:00+0000",
            "timeZone": "Europe/London",
            "reminders": ["TRIGGER:-PT15M"],
            "priority": 3,
        }
        actual = {
            "title": "Team Meeting",
            "content": "Review project timelines",
            "dueDate": "2024-08-01T17:00:00.000+0000",
            "startDate": "2024-08-01T09:00:00.000+0000",
            "timeZone": "Europe/London",
            "reminders": [{"id": "r1", "trigger": "TRIGGER:-PT15M"}],
            "priority": 3,
            "id": "task123",
            "status": 0,
        }
        assert _verify_mutation("create", expected, actual) == []


class TestVerifyMutationEdgeCases:
    """Edge cases and defensive checks."""

    def test_non_dict_actual_returns_error(self):
        expected = {"title": "Test"}
        result = _verify_mutation("create", expected, "not a dict")
        assert len(result) == 1
        assert "non-dict" in result[0]

    def test_empty_expected_no_warnings(self):
        expected = {}
        actual = {"title": "Something", "priority": 5}
        assert _verify_mutation("create", expected, actual) == []

    def test_multiple_mismatches_all_reported(self):
        expected = {
            "title": "Wrong",
            "content": "Wrong",
            "priority": 5,
            "dueDate": "2024-08-01",
            "startDate": "2024-08-01",
        }
        actual = {
            "title": "Different",
            "content": "Different",
            "priority": 0,
            "dueDate": None,
            "startDate": None,
        }
        result = _verify_mutation("create", expected, actual)
        assert len(result) == 5
