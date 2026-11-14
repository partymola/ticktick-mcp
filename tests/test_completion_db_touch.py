"""Every change to the completion store also sets the file's timestamp.

A desktop file-sync client was measured (14 Nov 2026) uploading the store after
a timestamp change but not after an SQLite insert, so two machines sharing the
config directory each kept their own copy until the next unrelated change, and
the sync then kept one and set the other aside as a conflict.
"""

import errno
import logging
import sqlite3
from unittest.mock import patch

import pytest

import ticktick_mcp.completion_db as db


def _rows(path):
    with sqlite3.connect(str(path)) as conn:
        return {r[0] for r in conn.execute("SELECT task_id FROM completion_tracking")}


class TestAChangeTouchesTheFile:
    def test_recording_a_completion_touches_the_store(self, isolated_db):
        with patch("os.utime") as utime:
            db.mark_processed("t1", "p1", "Title", None)
        utime.assert_called_once_with(isolated_db)

    def test_clearing_a_completion_touches_the_store(self, isolated_db):
        db.mark_processed("t1", "p1", "Title", None)
        with patch("os.utime") as utime:
            db.clear_processed("t1")
        utime.assert_called_once_with(isolated_db)

    def test_the_touch_comes_after_the_row_is_committed(self, isolated_db):
        seen = []
        with patch("os.utime", side_effect=lambda p: seen.append(_rows(p))):
            db.mark_processed("t1", "p1", "Title", None)
            db.clear_processed("t1")
        assert seen == [{"t1"}, set()]

    def test_no_connection_is_open_when_the_store_is_touched(self, isolated_db):
        opened = []
        real_connect = db._connect

        def spy():
            conn = real_connect()
            opened.append(conn)
            return conn

        def all_closed(_path):
            for conn in opened:
                with pytest.raises(sqlite3.ProgrammingError):
                    conn.execute("SELECT 1")

        with patch.object(db, "_connect", spy), patch("os.utime", side_effect=all_closed):
            db.mark_processed("t1", "p1", "Title", None)
            db.clear_processed("t1")
        assert len(opened) == 2


class TestNothingChangedNothingTouched:
    """A needless touch on a machine holding a stale copy invites the sync to
    upload that copy over a newer one."""

    def test_a_duplicate_writes_nothing_and_touches_nothing(self, isolated_db):
        db.mark_processed("t1", "p1", "Title", None)
        with patch("os.utime") as utime:
            db.mark_processed("t1", "p1", "Title", None)
        utime.assert_not_called()

    def test_a_clear_that_deletes_nothing_touches_nothing(self, isolated_db):
        with patch("os.utime") as utime:
            db.clear_processed("absent")
        utime.assert_not_called()

    def test_setup_and_reads_touch_nothing(self, isolated_db):
        db.mark_processed("t1", "p1", "Title", None)
        with patch("os.utime") as utime:
            db.init_db()
            db.is_processed("t1")
            db.get_processed_ids_for_project("p1")
        utime.assert_not_called()


class TestAFailedTouchCostsNothing:
    # EROFS is a plain OSError, not a PermissionError, so a handler narrowed to
    # the latter fails these.
    READ_ONLY = OSError(errno.EROFS, "Read-only file system")

    def test_the_row_is_kept_when_the_touch_fails(self, isolated_db):
        with patch("os.utime", side_effect=self.READ_ONLY):
            db.mark_processed("t1", "p1", "Title", None)
        assert db.is_processed("t1")

    def test_the_row_is_cleared_when_the_touch_fails(self, isolated_db):
        db.mark_processed("t1", "p1", "Title", None)
        with patch("os.utime", side_effect=self.READ_ONLY):
            db.clear_processed("t1")
        assert not db.is_processed("t1")

    def test_a_failed_touch_is_logged(self, isolated_db, caplog):
        with caplog.at_level(logging.WARNING), patch("os.utime", side_effect=self.READ_ONLY):
            db.mark_processed("t1", "p1", "Title", None)
        assert "Could not touch" in caplog.text
