"""Tests for Becker integration setup helpers."""

from pathlib import Path

import pytest

from custom_components.becker import _resolve_db_path


def test_resolve_db_path_uses_config_directory_for_default_file(tmp_path: Path) -> None:
    path = _resolve_db_path(str(tmp_path), "centronic-stick.db")

    assert path == str(tmp_path / "centronic-stick.db")


def test_resolve_db_path_accepts_existing_parent_directory(tmp_path: Path) -> None:
    database_dir = tmp_path / "becker"
    database_dir.mkdir()
    database_file = database_dir / "state.db"

    path = _resolve_db_path(str(tmp_path), str(database_file))

    assert path == str(database_file)


def test_resolve_db_path_rejects_missing_parent_directory(tmp_path: Path) -> None:
    database_file = tmp_path / "missing" / "state.db"

    with pytest.raises(ValueError, match="does not exist or is not a directory"):
        _resolve_db_path(str(tmp_path), str(database_file))
