"""Serialization and validation helpers for shutter-database import/export.

Pure logic with no Home Assistant imports so it can be unit-tested directly.
"""

import json
import sqlite3
from pathlib import Path

from .pybecker.database import Database

STATE_VERSION = 1
KNOWN_UNIT_CODES = ("1737b", "1737c", "1737d", "1737e", "1737f")
MAX_INCREMENT = (1 << 63) - 1
REQUIRED_UNIT_COLUMNS = ("code", "increment", "configured", "executed")


class StateJSONError(Exception):
    """Raised when the uploaded text is not valid JSON."""


class StateFormatError(Exception):
    """Raised when the JSON does not match the expected state format."""


def build_state(units: list[dict], exported_at: str) -> dict:
    """Build the export document from unit rows."""
    return {"version": STATE_VERSION, "exported_at": exported_at, "units": units}


def dump_state_json(units: list[dict], exported_at: str) -> str:
    """Serialize unit rows to a pretty JSON string."""
    return json.dumps(build_state(units, exported_at), indent=2)


def parse_state_json(raw: str | bytes) -> list[dict]:
    """Parse and validate uploaded JSON, returning normalized unit rows.

    Raises StateJSONError for non-JSON input and StateFormatError when the
    structure, unit codes, or values are invalid.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError) as err:
        raise StateJSONError from err

    if (
        not isinstance(data, dict)
        or data.get("version") != STATE_VERSION
        or not isinstance(data.get("units"), list)
    ):
        raise StateFormatError
    if not data["units"]:
        raise StateFormatError

    rows: list[dict] = []
    seen_codes: set[str] = set()
    for item in data["units"]:
        if not isinstance(item, dict) or item.get("code") not in KNOWN_UNIT_CODES:
            raise StateFormatError
        try:
            increment = int(item["increment"])
            configured = int(item["configured"])
        except (KeyError, ValueError, TypeError) as err:
            raise StateFormatError from err
        code = item["code"]
        if code in seen_codes:
            raise StateFormatError
        if not 0 <= increment <= MAX_INCREMENT or configured not in (0, 1):
            raise StateFormatError
        seen_codes.add(code)
        rows.append(
            {"code": code, "increment": increment, "configured": configured}
        )
    return rows


def read_units(db_path: str) -> list[dict]:
    """Open the database at db_path and return all unit rows."""
    db = Database(db_path)
    try:
        return db.export_units()
    finally:
        db.conn.close()


def apply_units(db_path: str, rows: list[dict]) -> None:
    """Open the database at db_path and apply the given unit rows."""
    db = Database(db_path)
    try:
        db.import_units(rows)
    finally:
        db.conn.close()


def is_valid_becker_db(path: Path) -> bool:
    """Return True if path contains a structurally valid Becker database."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        table = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='unit'"
        ).fetchone()
        if table is None:
            return False

        columns = tuple(
            row[1] for row in con.execute("PRAGMA table_info(unit)").fetchall()
        )
        if columns != REQUIRED_UNIT_COLUMNS:
            return False

        rows = con.execute(
            "SELECT code, increment, configured FROM unit"
        ).fetchall()
        if not rows:
            return False

        seen_codes: set[str] = set()
        for code, increment, configured in rows:
            if code not in KNOWN_UNIT_CODES or code in seen_codes:
                return False
            if not isinstance(increment, int) or not 0 <= increment <= MAX_INCREMENT:
                return False
            if configured not in (0, 1):
                return False
            seen_codes.add(code)
        return seen_codes == set(KNOWN_UNIT_CODES)
    except (sqlite3.DatabaseError, TypeError, ValueError):
        return False
    finally:
        con.close()


def consistent_copy(src_path: Path, dst_path: Path) -> None:
    """Write a transactionally consistent copy of the db via the backup API."""
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
