"""Tests for the throttled freshness helper (``ensure_fresh``).

The throttle uses ``time.monotonic`` and module-level timestamps, so the
time-sensitive tests monkeypatch ``freshness.time.monotonic`` to a controllable
clock. The autouse ``_reset_freshness_state`` fixture (conftest) clears the
module state before each test.
"""

import ticktick_mcp.freshness as fr


class FakeClient:
    """Minimal client: counts sync() calls, optionally raising."""

    def __init__(self, fail: bool = False):
        self.sync_calls = 0
        self.fail = fail

    def sync(self):
        self.sync_calls += 1
        if self.fail:
            raise RuntimeError("simulated sync failure")


def _clock(monkeypatch):
    """Install a controllable monotonic clock; return its mutable holder."""
    holder = {"t": 1000.0}
    monkeypatch.setattr(fr.time, "monotonic", lambda: holder["t"])
    return holder


def test_first_call_syncs():
    c = FakeClient()
    assert fr.ensure_fresh(c) is True
    assert c.sync_calls == 1


def test_within_ttl_skips(monkeypatch):
    t = _clock(monkeypatch)
    c = FakeClient()
    assert fr.ensure_fresh(c) is True
    t["t"] += 1.0  # well inside the 15s default TTL
    assert fr.ensure_fresh(c) is True
    assert c.sync_calls == 1


def test_after_ttl_resyncs(monkeypatch):
    t = _clock(monkeypatch)
    c = FakeClient()
    fr.ensure_fresh(c)
    t["t"] += 16.0  # past the default 15s TTL
    fr.ensure_fresh(c)
    assert c.sync_calls == 2


def test_force_bypasses_ttl(monkeypatch):
    t = _clock(monkeypatch)
    c = FakeClient()
    fr.ensure_fresh(c)
    t["t"] += 1.0  # inside TTL
    assert fr.ensure_fresh(c, force=True) is True
    assert c.sync_calls == 2


def test_sync_failure_returns_false_without_raising():
    c = FakeClient(fail=True)
    assert fr.ensure_fresh(c) is False
    assert c.sync_calls == 1


def test_failure_backoff_prevents_tight_loop(monkeypatch):
    t = _clock(monkeypatch)
    c = FakeClient(fail=True)
    assert fr.ensure_fresh(c) is False  # attempt 1, fails -> backoff armed
    t["t"] += 1.0  # inside the 5s backoff
    assert fr.ensure_fresh(c) is False  # backoff: no new attempt
    assert c.sync_calls == 1
    t["t"] += 5.0  # past the backoff
    assert fr.ensure_fresh(c) is False  # attempts again, still fails
    assert c.sync_calls == 2


def test_force_ignores_backoff(monkeypatch):
    t = _clock(monkeypatch)
    c = FakeClient(fail=True)
    fr.ensure_fresh(c)  # fails, arms backoff
    t["t"] += 1.0  # inside backoff
    fr.ensure_fresh(c, force=True)  # force overrides the backoff
    assert c.sync_calls == 2


def test_successful_sync_clears_backoff(monkeypatch):
    t = _clock(monkeypatch)
    c = FakeClient(fail=True)
    fr.ensure_fresh(c)  # fail -> backoff armed
    c.fail = False
    fr.ensure_fresh(c, force=True)  # forced success clears backoff
    t["t"] += 16.0  # past TTL so a non-forced call will try again
    assert fr.ensure_fresh(c) is True
    assert c.sync_calls == 3


def test_env_ttl_override(monkeypatch):
    t = _clock(monkeypatch)
    monkeypatch.setenv("TICKTICK_MCP_SYNC_TTL_SECONDS", "100")
    c = FakeClient()
    fr.ensure_fresh(c)
    t["t"] += 50.0  # inside the 100s override
    fr.ensure_fresh(c)
    assert c.sync_calls == 1


def test_invalid_env_ttl_falls_back_to_default(monkeypatch):
    t = _clock(monkeypatch)
    monkeypatch.setenv("TICKTICK_MCP_SYNC_TTL_SECONDS", "not-a-number")
    c = FakeClient()
    fr.ensure_fresh(c)
    t["t"] += 16.0  # past the default 15s
    fr.ensure_fresh(c)
    assert c.sync_calls == 2


def test_none_client_returns_false():
    assert fr.ensure_fresh(None) is False
    assert fr.ensure_fresh(None, force=True) is False


def test_reset_clears_state():
    c = FakeClient()
    fr.ensure_fresh(c)
    fr.reset_freshness_state()
    fr.ensure_fresh(c)  # state cleared -> syncs again
    assert c.sync_calls == 2
