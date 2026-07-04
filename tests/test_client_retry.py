"""Initialisation retry behaviour of ``TickTickClientSingleton``.

A transient login failure must not brick the server for its whole
lifetime: after a cooldown (``INIT_RETRY_SECONDS``) the next
``get_client()`` call re-attempts construction. Within the cooldown no
re-attempt is made, so back-to-back tool calls do not each pay for a
doomed login.
"""

import os
import time
from unittest.mock import MagicMock, patch

import pytest

import ticktick_mcp.client as client_module
from ticktick_mcp.client import TickTickClientSingleton


@pytest.fixture(autouse=True)
def _reset_singleton():
    TickTickClientSingleton._instance = None
    TickTickClientSingleton._last_failure_monotonic = None
    TickTickClientSingleton._last_error = None
    yield
    TickTickClientSingleton._instance = None
    TickTickClientSingleton._last_failure_monotonic = None
    TickTickClientSingleton._last_error = None


def _expire_cooldown():
    TickTickClientSingleton._last_failure_monotonic = time.monotonic() - (
        client_module.INIT_RETRY_SECONDS + 1
    )


class TestInitRetry:
    def test_success_is_cached(self):
        instance = MagicMock()
        with patch.object(client_module, "TickTickClient", return_value=instance) as ctor:
            assert TickTickClientSingleton.get_client() is instance
            assert TickTickClientSingleton.get_client() is instance
        assert ctor.call_count == 1

    def test_failure_within_cooldown_does_not_retry(self):
        with patch.object(
            client_module, "TickTickClient", side_effect=RuntimeError("login failed")
        ) as ctor:
            assert TickTickClientSingleton.get_client() is None
            assert TickTickClientSingleton.get_client() is None
        assert ctor.call_count == 1
        assert TickTickClientSingleton.last_error() == "login failed"

    def test_retry_after_cooldown(self):
        with patch.object(
            client_module, "TickTickClient", side_effect=RuntimeError("login failed")
        ):
            assert TickTickClientSingleton.get_client() is None

        _expire_cooldown()
        instance = MagicMock()
        with patch.object(client_module, "TickTickClient", return_value=instance) as ctor:
            assert TickTickClientSingleton.get_client() is instance
        assert ctor.call_count == 1

    def test_success_after_retry_clears_failure_state(self):
        with patch.object(
            client_module, "TickTickClient", side_effect=RuntimeError("login failed")
        ):
            TickTickClientSingleton.get_client()

        _expire_cooldown()
        with patch.object(client_module, "TickTickClient", return_value=MagicMock()):
            TickTickClientSingleton.get_client()

        assert TickTickClientSingleton.last_error() is None
        assert TickTickClientSingleton._last_failure_monotonic is None

    def test_repeated_failure_restarts_cooldown(self):
        with patch.object(client_module, "TickTickClient", side_effect=RuntimeError("first")):
            TickTickClientSingleton.get_client()

        _expire_cooldown()
        with patch.object(
            client_module, "TickTickClient", side_effect=RuntimeError("second")
        ) as ctor:
            assert TickTickClientSingleton.get_client() is None
            # Immediately after the failed retry we are inside a fresh
            # cooldown window, so no further attempt is made.
            assert TickTickClientSingleton.get_client() is None
        assert ctor.call_count == 1
        assert TickTickClientSingleton.last_error() == "second"


class TestInitRetrySecondsEnv:
    def test_default_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TICKTICK_MCP_INIT_RETRY_SECONDS", None)
            assert client_module._init_retry_seconds() == 60.0

    def test_valid_override(self):
        with patch.dict(os.environ, {"TICKTICK_MCP_INIT_RETRY_SECONDS": "5"}):
            assert client_module._init_retry_seconds() == 5.0

    def test_invalid_falls_back_to_default(self):
        with patch.dict(os.environ, {"TICKTICK_MCP_INIT_RETRY_SECONDS": "soon"}):
            assert client_module._init_retry_seconds() == 60.0

    def test_negative_falls_back_to_default(self):
        with patch.dict(os.environ, {"TICKTICK_MCP_INIT_RETRY_SECONDS": "-1"}):
            assert client_module._init_retry_seconds() == 60.0
