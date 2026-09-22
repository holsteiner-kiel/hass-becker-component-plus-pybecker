"""Tests for serialization of Becker commands and live database imports."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from custom_components.becker.config_flow import _swap_live_db
from custom_components.becker.db_transfer import apply_units, read_units
from custom_components.becker.pybecker.becker import Becker
from custom_components.becker.pybecker.database import Database


async def test_send_waits_for_operation_lock() -> None:
    becker = Becker.__new__(Becker)
    becker.operation_lock = asyncio.Lock()
    becker.db = MagicMock()
    becker.db.get_unit.return_value = ["1737b", 10, 1]
    becker.run_codes = AsyncMock()

    await becker.operation_lock.acquire()
    task = asyncio.create_task(becker.send("1", "UP"))
    await asyncio.sleep(0)

    becker.run_codes.assert_not_awaited()

    becker.operation_lock.release()
    await task

    becker.run_codes.assert_awaited_once_with(1, ["1737b", 10, 1], "UP", False)


def test_live_db_swap_reopens_runtime_database(tmp_path: Path) -> None:
    live_path = tmp_path / "live.db"
    replacement_path = tmp_path / "replacement.db"
    backup_path = tmp_path / "backup.db"

    live_db = Database(str(live_path))
    live_db.import_units(
        [{"code": "1737b", "increment": 100, "configured": 1}]
    )

    replacement = Database(str(replacement_path))
    replacement.import_units(
        [{"code": "1737b", "increment": 200, "configured": 1}]
    )
    replacement.conn.close()

    becker = MagicMock()
    becker.db = live_db

    _swap_live_db(becker, live_path, replacement_path, backup_path)

    live_rows = {row["code"]: row for row in becker.db.export_units()}
    backup_rows = {row["code"]: row for row in read_units(str(backup_path))}

    assert live_rows["1737b"]["increment"] == 200
    assert backup_rows["1737b"]["increment"] == 100

    becker.db.conn.close()
