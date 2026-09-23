"""Regression tests for the bundled pybecker database."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading
import time
from unittest.mock import MagicMock

from custom_components.becker.pybecker.database import Database


def test_connection_usable_across_threads(tmp_path: Path) -> None:
    """The connection is created in the executor but used on the event loop.

    async_setup_entry builds the Becker (and therefore the sqlite
    connection) in a worker thread via async_add_executor_job, while every
    later database call runs as a coroutine on the event loop thread.
    Regression test for the ProgrammingError this raised when the
    connection was bound to its creating thread.
    """
    db_file = str(tmp_path / "centronic-stick.db")

    with ThreadPoolExecutor(max_workers=1) as executor:
        database = executor.submit(Database, db_file).result()

    # Access from a different (here: the main) thread must not raise.
    assert database.get_all_units() == []
    assert database.get_unit(1) == ["1737b", 0, 0]
    database.close()


def test_export_units_returns_all_rows(tmp_path: Path) -> None:
    """export_units returns every unit row, not just configured ones."""
    db_file = str(tmp_path / "centronic-stick.db")
    db = Database(db_file)
    db.import_units([{"code": "1737b", "increment": 42, "configured": 1}])

    rows = db.export_units()

    assert len(rows) == 5  # the five seeded units
    first = next(r for r in rows if r["code"] == "1737b")
    assert first == {"code": "1737b", "increment": 42, "configured": 1}
    db.close()


def test_import_units_updates_matching_codes_only(tmp_path: Path) -> None:
    """import_units updates increment+configured for the given codes only."""
    db_file = str(tmp_path / "centronic-stick.db")
    db = Database(db_file)

    db.import_units([{"code": "1737c", "increment": 7, "configured": 1}])

    rows = {r["code"]: r for r in db.export_units()}
    assert rows["1737c"] == {"code": "1737c", "increment": 7, "configured": 1}
    assert rows["1737b"] == {"code": "1737b", "increment": 0, "configured": 0}
    db.close()


def test_database_methods_serialize_connection_access(tmp_path: Path) -> None:
    """A second thread waits while another operation owns the database lock."""
    db = Database(str(tmp_path / "centronic-stick.db"))
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_database_lock() -> None:
        with db._lock:
            lock_acquired.set()
            release_lock.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        holder = executor.submit(hold_database_lock)
        assert lock_acquired.wait(timeout=1)

        reader = executor.submit(db.get_unit, 1)
        time.sleep(0.05)
        assert not reader.done()

        release_lock.set()
        holder.result(timeout=1)
        assert reader.result(timeout=1) == ["1737b", 0, 0]

    db.close()



def test_database_context_manager_closes_connection(tmp_path: Path) -> None:
    path = str(tmp_path / "ctx.db")
    with Database(path) as db:
        assert db.get_unit(1) == ["1737b", 0, 0]

    import sqlite3
    import pytest

    with pytest.raises(sqlite3.ProgrammingError):
        db.get_unit(1)


def test_init_dummy_configures_first_unit(tmp_path: Path, monkeypatch) -> None:
    db = Database(str(tmp_path / "dummy.db"))
    monkeypatch.setattr(
        "custom_components.becker.pybecker.database.randrange",
        lambda *args: 23,
    )

    db.init_dummy()

    assert db.get_unit(1) == ["1737b", 23, 1]
    db.close()


def test_get_rowid_add_remove_unit(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "units.db"))

    assert db.get_rowid_from_unit("fffff") == -1
    db.add_unit(["fffff", 9, 1])
    assert db.get_rowid_from_unit("fffff") > 0
    db.remove_unit("fffff")
    assert db.get_rowid_from_unit("fffff") == -1

    db.close()


def test_set_unit_by_code_and_row_index(tmp_path: Path, monkeypatch) -> None:
    db = Database(str(tmp_path / "set.db"))
    monkeypatch.setattr(
        "custom_components.becker.pybecker.database.time.time",
        lambda: 123456,
    )

    db.set_unit(["1737b", 12, 1])
    assert db.get_unit(1) == ["1737b", 12, 1]

    db.set_unit(["2", 22, 1])
    assert db.get_unit(2) == ["1737c", 22, 1]
    db.close()


def test_set_unit_test_mode_rolls_back(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "rollback.db"))
    before = db.get_unit(1)

    db.set_unit(["1737b", 99, 1], test=True)

    assert db.get_unit(1) == before
    db.close()



def test_migrate_legacy_num_file(tmp_path: Path, monkeypatch) -> None:
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    legacy_file = legacy_dir / "centronic-stick.num"
    legacy_file.write_text("37")

    monkeypatch.setattr(
        "custom_components.becker.pybecker.database.FILE_PATH",
        str(legacy_dir),
    )

    db = Database(str(tmp_path / "migrate.db"))

    assert db.get_unit(1) == ["1737b", 37, 1]
    assert not legacy_file.exists()
    db.close()


def test_import_units_rolls_back_invalid_value(tmp_path: Path) -> None:
    import pytest

    db = Database(str(tmp_path / "invalid-import.db"))
    before = db.export_units()

    with pytest.raises((TypeError, ValueError)):
        db.import_units(
            [{"code": "1737b", "increment": "not-an-int", "configured": 1}]
        )

    assert db.export_units() == before
    db.close()


def test_get_unit_returns_none_for_unknown_row(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "missing-row.db"))

    assert db.get_unit(999) is None

    db.close()



def test_migrate_rolls_back_on_os_error(tmp_path: Path, monkeypatch) -> None:
    db = Database(str(tmp_path / "migrate-error.db"))
    monkeypatch.setattr(
        "custom_components.becker.pybecker.database.os.path.isfile",
        lambda path: True,
    )
    monkeypatch.setattr(
        "builtins.open",
        MagicMock(side_effect=OSError("broken legacy file")),
    )

    db.migrate()

    # The real SQLite connection remains usable after the rollback path.
    assert db.get_unit(1) == ["1737b", 0, 0]
    db.close()


def test_init_dummy_rolls_back_on_database_error(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "dummy-error.db"))
    real_conn = db.conn
    fake_conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = sqlite3.OperationalError("boom")
    fake_conn.cursor.return_value = cursor
    db.conn = fake_conn

    db.init_dummy()

    fake_conn.rollback.assert_called_once()
    db.conn = real_conn
    db.close()


def test_output_handles_never_and_previously_executed_units(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    import sqlite3

    db = Database(str(tmp_path / "output.db"))
    db.set_unit(["1737b", 10, 1])
    db.conn.execute(
        "UPDATE unit SET executed = ? WHERE code = ?",
        (1234567890, "1737b"),
    )
    db.conn.commit()
    caplog.set_level(logging.INFO)

    db.output()

    assert "1737b" in caplog.text
    assert "(unknown)" in caplog.text
    db.close()
