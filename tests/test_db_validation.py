"""Tests for Becker database transfer validation."""

import json
import sqlite3
from pathlib import Path

import pytest

from custom_components.becker.db_transfer import (
    StateFormatError,
    is_valid_becker_db,
    parse_state_json,
)
from custom_components.becker.pybecker.database import Database


def test_state_import_rejects_duplicate_unit_codes() -> None:
    raw = json.dumps(
        {
            "version": 1,
            "units": [
                {"code": "1737b", "increment": 10, "configured": 1},
                {"code": "1737b", "increment": 11, "configured": 1},
            ],
        }
    )

    with pytest.raises(StateFormatError):
        parse_state_json(raw)


@pytest.mark.parametrize("increment", [-1, 1 << 63])
def test_state_import_rejects_increment_outside_sqlite_range(
    increment: int,
) -> None:
    raw = json.dumps(
        {
            "version": 1,
            "units": [
                {"code": "1737b", "increment": increment, "configured": 1},
            ],
        }
    )

    with pytest.raises(StateFormatError):
        parse_state_json(raw)


def test_database_validation_rejects_wrong_schema(tmp_path: Path) -> None:
    path = tmp_path / "bad.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE unit (code TEXT)")
    con.execute("INSERT INTO unit VALUES ('1737b')")
    con.commit()
    con.close()

    assert is_valid_becker_db(path) is False


def test_database_validation_rejects_unknown_unit_code(tmp_path: Path) -> None:
    path = tmp_path / "bad-unit.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE unit (code NVARCHAR(5), increment INTEGER(4), configured BIT, executed INTEGER)"
    )
    con.execute("INSERT INTO unit VALUES ('fffff', 10, 1, 0)")
    con.commit()
    con.close()

    assert is_valid_becker_db(path) is False


def test_database_validation_rejects_increment_overflow(tmp_path: Path) -> None:
    path = tmp_path / "overflow.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE unit (code NVARCHAR(5), increment INTEGER(4), configured BIT, executed INTEGER)"
    )
    con.execute("INSERT INTO unit VALUES ('1737b', 9223372036854775807, 1, 0)")
    con.execute("UPDATE unit SET increment = increment + 1")
    con.commit()
    con.close()

    assert is_valid_becker_db(path) is False


def test_database_validation_rejects_incomplete_unit_set(tmp_path: Path) -> None:
    path = tmp_path / "partial.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE unit (code NVARCHAR(5), increment INTEGER(4), configured BIT, executed INTEGER)"
    )
    con.execute("INSERT INTO unit VALUES ('1737b', 10, 1, 0)")
    con.commit()
    con.close()

    assert is_valid_becker_db(path) is False


def test_state_import_rejects_unknown_version() -> None:
    raw = json.dumps(
        {
            "version": 999,
            "units": [
                {"code": "1737b", "increment": 10, "configured": 1},
            ],
        }
    )

    with pytest.raises(StateFormatError):
        parse_state_json(raw)


def test_database_validation_accepts_generated_database(tmp_path: Path) -> None:
    path = tmp_path / "valid.db"
    db = Database(str(path))
    db.conn.close()

    assert is_valid_becker_db(path) is True


def test_import_units_rolls_back_when_unknown_code_is_present(
    tmp_path: Path,
) -> None:
    path = tmp_path / "atomic.db"
    db = Database(str(path))
    before = db.get_unit(1)

    with pytest.raises(sqlite3.IntegrityError):
        db.import_units(
            [
                {"code": "1737b", "increment": 123, "configured": 1},
                {"code": "fffff", "increment": 456, "configured": 1},
            ]
        )

    assert db.get_unit(1) == before
    db.conn.close()
