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
    TickTickClientSingleton._last_status = None
    yield
    TickTickClientSingleton._instance = None
    TickTickClientSingleton._last_failure_monotonic = None
    TickTickClientSingleton._last_error = None
    TickTickClientSingleton._last_status = None


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


class TestRateLimitDetection:
    def test_is_rate_limited_true_on_429(self):
        TickTickClientSingleton._last_status = 429
        assert TickTickClientSingleton.is_rate_limited() is True

    def test_is_rate_limited_false_on_other_error(self):
        TickTickClientSingleton._last_status = 500
        assert TickTickClientSingleton.is_rate_limited() is False

    def test_is_rate_limited_false_when_no_error(self):
        TickTickClientSingleton._last_status = None
        assert TickTickClientSingleton.is_rate_limited() is False

    def test_a_message_that_merely_contains_429_is_not_a_rate_limit(self):
        """Read the status, never the text.

        A task id or a URL containing "429" would otherwise put the server
        into a five-minute cooldown and tell the agent to stop retrying.
        """
        TickTickClientSingleton._last_status = None
        TickTickClientSingleton._last_error = "Max retries exceeded with url: /api/v2/task/6429abc"
        assert TickTickClientSingleton.is_rate_limited() is False

    def test_the_status_is_taken_from_a_real_construction_failure(self):
        with patch.object(
            client_module,
            "TickTickClient",
            side_effect=client_module.TickTickHTTPError("nope (HTTP 429)", status=429),
        ):
            assert TickTickClientSingleton.get_client() is None
        assert TickTickClientSingleton.is_rate_limited() is True

    def test_429_uses_longer_cooldown(self):
        """After a 429, a retry attempt is blocked for the rate-limit cooldown
        even once the ordinary init cooldown has elapsed."""
        with patch.object(
            client_module,
            "TickTickClient",
            side_effect=client_module.TickTickHTTPError(
                "Could Not Complete Request (HTTP 429)", status=429
            ),
        ):
            assert TickTickClientSingleton.get_client() is None

        # Age the failure past the init cooldown but not the rate-limit one.
        TickTickClientSingleton._last_failure_monotonic = time.monotonic() - (
            client_module.INIT_RETRY_SECONDS + 1
        )
        assert client_module.RATELIMIT_RETRY_SECONDS > client_module.INIT_RETRY_SECONDS + 1

        with patch.object(client_module, "TickTickClient", return_value=MagicMock()) as ctor:
            # Still inside the rate-limit window: no re-attempt.
            assert TickTickClientSingleton.get_client() is None
        assert ctor.call_count == 0


class TestCheckStatusCodeAugmentation:
    def test_non_200_includes_http_code(self):
        response = MagicMock()
        response.status_code = 429
        with pytest.raises(RuntimeError, match=r"HTTP 429"):
            client_module._augmented_check_status_code(response, "Could Not Complete Request")

    def test_200_does_not_raise(self):
        response = MagicMock()
        response.status_code = 200
        client_module._augmented_check_status_code(response, "Could Not Complete Request")

    def test_missing_status_code_falls_back_to_bare_message(self):
        response = object()  # no status_code attribute
        with pytest.raises(RuntimeError) as exc:
            client_module._augmented_check_status_code(response, "boom")
        assert "HTTP" not in str(exc.value)


class TestRateLimitRetrySecondsEnv:
    def test_default_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TICKTICK_MCP_RATELIMIT_RETRY_SECONDS", None)
            assert client_module._ratelimit_retry_seconds() == 300.0

    def test_valid_override(self):
        with patch.dict(os.environ, {"TICKTICK_MCP_RATELIMIT_RETRY_SECONDS": "120"}):
            assert client_module._ratelimit_retry_seconds() == 120.0

    def test_invalid_falls_back_to_default(self):
        with patch.dict(os.environ, {"TICKTICK_MCP_RATELIMIT_RETRY_SECONDS": "soon"}):
            assert client_module._ratelimit_retry_seconds() == 300.0

    def test_negative_falls_back_to_default(self):
        with patch.dict(os.environ, {"TICKTICK_MCP_RATELIMIT_RETRY_SECONDS": "-1"}):
            assert client_module._ratelimit_retry_seconds() == 300.0


class TestV2TokenCache:
    def test_write_read_roundtrip(self, tmp_path):
        with patch.object(client_module, "dotenv_dir_path", tmp_path):
            client_module._write_v2_token("tok-abc")
            assert client_module._read_v2_token() == "tok-abc"

    def test_read_missing_returns_none(self, tmp_path):
        with patch.object(client_module, "dotenv_dir_path", tmp_path):
            assert client_module._read_v2_token() is None

    def test_read_corrupt_returns_none(self, tmp_path):
        with patch.object(client_module, "dotenv_dir_path", tmp_path):
            (tmp_path / client_module._V2_TOKEN_FILENAME).write_text("not json{")
            assert client_module._read_v2_token() is None

    def test_an_undecodable_cache_is_deleted_rather_than_bricking_the_server(self, tmp_path):
        """read_text raises UnicodeDecodeError, a ValueError, not an OSError.

        It escaped before the injection point, so nothing cleared the cache
        and every retry failed identically - unrecoverable without hand-
        deleting a file users are told to keep.
        """
        with patch.object(client_module, "dotenv_dir_path", tmp_path):
            path = tmp_path / client_module._V2_TOKEN_FILENAME
            path.write_bytes(b"\xff\xfe not utf-8")
            assert client_module._read_v2_token() is None
            assert not path.exists()

    def test_a_json_cache_of_the_wrong_shape_is_deleted(self, tmp_path):
        with patch.object(client_module, "dotenv_dir_path", tmp_path):
            path = tmp_path / client_module._V2_TOKEN_FILENAME
            path.write_text("not json{")
            assert client_module._read_v2_token() is None
            assert not path.exists()

    def test_the_token_is_never_written_at_a_readable_mode(self, tmp_path):
        """A write-then-chmod leaves it readable for the length of the write."""
        seen = []
        real_open = os.open

        def spy(path, flags, mode=0o777, **kwargs):
            seen.append(mode)
            return real_open(path, flags, mode, **kwargs)

        with (
            patch.object(client_module, "dotenv_dir_path", tmp_path),
            patch.object(client_module.os, "open", spy),
        ):
            client_module._write_v2_token("tok")

        assert seen == [0o600]

    def test_a_failure_to_tighten_does_not_lose_the_token(self, tmp_path):
        def refuse(fd, mode):
            raise PermissionError(1, "Operation not permitted")

        with (
            patch.object(client_module, "dotenv_dir_path", tmp_path),
            patch.object(client_module.os, "fchmod", refuse),
        ):
            client_module._write_v2_token("tok")
            assert client_module._read_v2_token() == "tok"

    def test_write_ignores_non_string(self, tmp_path):
        """A mocked client's non-string access_token must not be serialised."""
        with patch.object(client_module, "dotenv_dir_path", tmp_path):
            client_module._write_v2_token(MagicMock())
            assert not (tmp_path / client_module._V2_TOKEN_FILENAME).exists()

    def test_write_sets_0600(self, tmp_path):
        with patch.object(client_module, "dotenv_dir_path", tmp_path):
            client_module._write_v2_token("tok")
            mode = (tmp_path / client_module._V2_TOKEN_FILENAME).stat().st_mode & 0o777
            assert mode == 0o600

    def test_delete_removes_file(self, tmp_path):
        with patch.object(client_module, "dotenv_dir_path", tmp_path):
            client_module._write_v2_token("tok")
            client_module._delete_v2_token()
            assert client_module._read_v2_token() is None


class TestConstructClientTokenReuse:
    def test_valid_cached_token_injected_no_signon(self, tmp_path):
        seen = {}

        class FakeClient:
            def __init__(self, username, password, oauth):
                seen["injected"] = client_module._INJECTED_V2_TOKEN
                self.access_token = "should-not-be-rewritten"

        with (
            patch.object(client_module, "dotenv_dir_path", tmp_path),
            patch.object(client_module, "TickTickClient", FakeClient),
            patch.object(client_module, "OAuth2", MagicMock()),
        ):
            client_module._write_v2_token("cached-tok")
            result = TickTickClientSingleton._construct_client()

            assert isinstance(result, FakeClient)
            # The cached token was injected during construction (no signon)...
            assert seen["injected"] == "cached-tok"
            # ...cleared afterwards...
            assert client_module._INJECTED_V2_TOKEN is None
            # ...and the valid path leaves the cache untouched.
            assert client_module._read_v2_token() == "cached-tok"

    def test_stale_token_falls_back_and_refreshes(self, tmp_path):
        class FakeClient:
            def __init__(self, username, password, oauth):
                if client_module._INJECTED_V2_TOKEN:
                    raise client_module.TickTickHTTPError(
                        "Could Not Complete Request (HTTP 401)", status=401
                    )
                self.access_token = "fresh-tok"

        with (
            patch.object(client_module, "dotenv_dir_path", tmp_path),
            patch.object(client_module, "TickTickClient", FakeClient),
            patch.object(client_module, "OAuth2", MagicMock()),
        ):
            client_module._write_v2_token("stale-tok")
            result = TickTickClientSingleton._construct_client()

            assert result.access_token == "fresh-tok"
            assert client_module._INJECTED_V2_TOKEN is None
            # Stale cache cleared, then refreshed with the newly-issued token.
            assert client_module._read_v2_token() == "fresh-tok"


class TestOnlyARejectionCostsTheCachedToken:
    """A transient failure must not delete the token and re-run signon.

    TickTick throttles user/signon, which is the whole reason the cache
    exists. Discarding a valid session token in answer to a 429 then POSTs
    signon immediately, prolonging the block.
    """

    def _construct_with(self, exc, tmp_path):
        class FakeClient:
            def __init__(self, username, password, oauth):
                if client_module._INJECTED_V2_TOKEN:
                    raise exc
                self.access_token = "fresh-tok"

        with (
            patch.object(client_module, "dotenv_dir_path", tmp_path),
            patch.object(client_module, "TickTickClient", FakeClient),
            patch.object(client_module, "OAuth2", MagicMock()),
        ):
            client_module._write_v2_token("valid-tok")
            raised = None
            try:
                TickTickClientSingleton._construct_client()
            except Exception as e:  # noqa: BLE001 - the test is what it raises
                raised = e
            return raised, client_module._read_v2_token()

    @pytest.mark.parametrize(
        "exc",
        [
            client_module.TickTickHTTPError("throttled (HTTP 429)", status=429),
            client_module.TickTickHTTPError("server error (HTTP 500)", status=500),
            TimeoutError("read timed out"),
            ConnectionResetError("reset"),
            RuntimeError("something nobody classified"),
        ],
        ids=["rate-limit", "server-error", "timeout", "reset", "unclassified"],
    )
    def test_a_transient_failure_keeps_the_cached_token(self, exc, tmp_path):
        raised, cached = self._construct_with(exc, tmp_path)
        assert raised is not None
        assert cached == "valid-tok"

    @pytest.mark.parametrize("status", [401, 403], ids=["unauthorised", "forbidden"])
    def test_a_rejection_clears_it(self, status, tmp_path):
        exc = client_module.TickTickHTTPError(f"rejected (HTTP {status})", status=status)
        raised, cached = self._construct_with(exc, tmp_path)
        assert raised is None
        assert cached == "fresh-tok"

    def test_no_cache_logs_in_and_caches_token(self, tmp_path):
        class FakeClient:
            def __init__(self, username, password, oauth):
                assert client_module._INJECTED_V2_TOKEN is None
                self.access_token = "new-tok"

        with (
            patch.object(client_module, "dotenv_dir_path", tmp_path),
            patch.object(client_module, "TickTickClient", FakeClient),
            patch.object(client_module, "OAuth2", MagicMock()),
        ):
            assert client_module._read_v2_token() is None
            result = TickTickClientSingleton._construct_client()

            assert result.access_token == "new-tok"
            assert client_module._read_v2_token() == "new-tok"


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
