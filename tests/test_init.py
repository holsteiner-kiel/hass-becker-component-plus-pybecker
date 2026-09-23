"""Tests for Becker integration setup helpers."""

from pathlib import Path

import pytest

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady, ServiceValidationError

from custom_components.becker.pybecker.becker_helper import BeckerConnectionError

from custom_components.becker import (
    _availability_callback,
    _clear_setup_repairs,
    _create_repair_issue,
    _dispatch_packet_on_loop,
    _get_becker,
    _packet_callback,
    _remove_stale_cover_devices,
    _repair_issue_id,
    _resolve_db_path,
    _update_listener,
    async_setup_entry,
    async_unload_entry,
    availability_signal_for_entry,
    signal_for_entry,
)


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



def test_signal_helpers_are_entry_scoped() -> None:
    assert signal_for_entry("abc") == "becker_receive_message_abc"
    assert availability_signal_for_entry("abc") == "becker_availability_abc"
    assert _repair_issue_id("problem", "abc") == "problem_abc"


def test_get_becker_rejects_no_loaded_entries() -> None:
    hass = MagicMock()
    hass.config_entries.async_loaded_entries.return_value = []

    with pytest.raises(ServiceValidationError) as exc:
        _get_becker(hass)

    assert exc.value.translation_domain == "becker"
    assert exc.value.translation_key == "no_loaded_entries"


def test_availability_callback_marshals_to_loop() -> None:
    hass = MagicMock()

    _availability_callback(hass, "entry-1")

    hass.loop.call_soon_threadsafe.assert_called_once()


def test_packet_callback_marshals_to_loop() -> None:
    hass = MagicMock()
    packet = MagicMock()

    _packet_callback(hass, "entry-1", packet)

    hass.loop.call_soon_threadsafe.assert_called_once()


def test_dispatch_packet_fires_dispatcher_and_bus_event() -> None:
    hass = MagicMock()
    packet = MagicMock()
    groups = {
        "unit_id": b"1737B",
        "channel": b"1",
        "command": b"2",
    }
    packet.group.side_effect = lambda name: groups[name]

    with patch("custom_components.becker.dispatcher_send") as dispatcher:
        _dispatch_packet_on_loop(hass, "entry-1", packet)

    dispatcher.assert_called_once_with(hass, signal_for_entry("entry-1"), packet)
    hass.bus.fire.assert_called_once()
    event_name, data = hass.bus.fire.call_args.args
    assert event_name == "becker_remote_packet_received"
    assert data == {"unit": "1737B", "channel": "1", "command": "up"}


def test_dispatch_packet_omits_unknown_command_name() -> None:
    hass = MagicMock()
    packet = MagicMock()
    groups = {
        "unit_id": b"1737B",
        "channel": b"1",
        "command": b"F",
    }
    packet.group.side_effect = lambda name: groups[name]

    with patch("custom_components.becker.dispatcher_send"):
        _dispatch_packet_on_loop(hass, "entry-1", packet)

    _, data = hass.bus.fire.call_args.args
    assert data == {"unit": "1737B", "channel": "1"}


def test_repair_helpers_create_and_clear_issues() -> None:
    hass = MagicMock()
    entry = SimpleNamespace(entry_id="entry-1", title="Stick")

    with (
        patch("custom_components.becker.ir.async_create_issue") as create_issue,
        patch("custom_components.becker.ir.async_delete_issue") as delete_issue,
    ):
        _create_repair_issue(hass, entry, "connection_unavailable", "connection_unavailable")
        _clear_setup_repairs(hass, entry)

    create_issue.assert_called_once()
    assert delete_issue.call_count == 2


def test_resolve_db_path_accepts_existing_absolute_file(tmp_path: Path) -> None:
    db = tmp_path / "existing.db"
    db.write_bytes(b"x")

    assert _resolve_db_path(str(tmp_path), str(db)) == str(db)


def test_resolve_db_path_uses_default_filename_when_none(tmp_path: Path) -> None:
    assert _resolve_db_path(str(tmp_path), None) == str(tmp_path / "centronic-stick.db")


def test_resolve_db_path_moves_legacy_file(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    config = tmp_path / "config"
    legacy.mkdir()
    config.mkdir()
    source = legacy / "centronic-stick.db"
    source.write_bytes(b"db")

    with patch("custom_components.becker.FILE_PATH", str(legacy)):
        result = _resolve_db_path(str(config), "centronic-stick.db")

    assert result == str(config / "centronic-stick.db")
    assert (config / "centronic-stick.db").read_bytes() == b"db"
    assert not source.exists()



def test_get_becker_returns_matching_entry() -> None:
    first = SimpleNamespace(entry_id="one", runtime_data=object())
    second = SimpleNamespace(entry_id="two", runtime_data=object())
    hass = MagicMock()
    hass.config_entries.async_loaded_entries.return_value = [first, second]

    assert _get_becker(hass, "two") is second.runtime_data


def test_get_becker_rejects_unknown_entry_id() -> None:
    entry = SimpleNamespace(entry_id="one", runtime_data=object())
    hass = MagicMock()
    hass.config_entries.async_loaded_entries.return_value = [entry]

    with pytest.raises(ServiceValidationError) as exc:
        _get_becker(hass, "missing")

    assert exc.value.translation_key == "entry_not_loaded"
    assert exc.value.translation_placeholders == {"entry_id": "missing"}


def test_get_becker_requires_entry_id_when_multiple_loaded() -> None:
    entries = [
        SimpleNamespace(entry_id="one", runtime_data=object()),
        SimpleNamespace(entry_id="two", runtime_data=object()),
    ]
    hass = MagicMock()
    hass.config_entries.async_loaded_entries.return_value = entries

    with pytest.raises(ServiceValidationError) as exc:
        _get_becker(hass)

    assert exc.value.translation_key == "entry_id_required"


def test_get_becker_returns_only_loaded_entry() -> None:
    runtime = object()
    entry = SimpleNamespace(entry_id="one", runtime_data=runtime)
    hass = MagicMock()
    hass.config_entries.async_loaded_entries.return_value = [entry]

    assert _get_becker(hass) is runtime


async def test_async_unload_entry_closes_runtime_after_success() -> None:
    runtime = MagicMock()
    entry = SimpleNamespace(runtime_data=runtime)
    hass = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.async_add_executor_job = AsyncMock()

    assert await async_unload_entry(hass, entry) is True

    hass.async_add_executor_job.assert_awaited_once_with(runtime.close)


async def test_async_unload_entry_keeps_runtime_when_unload_fails() -> None:
    runtime = MagicMock()
    entry = SimpleNamespace(runtime_data=runtime)
    hass = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
    hass.async_add_executor_job = AsyncMock()

    assert await async_unload_entry(hass, entry) is False

    hass.async_add_executor_job.assert_not_awaited()


async def test_update_listener_schedules_reload() -> None:
    hass = MagicMock()
    entry = SimpleNamespace(entry_id="entry-1")

    await _update_listener(hass, entry)

    hass.config_entries.async_schedule_reload.assert_called_once_with("entry-1")



def test_resolve_db_path_returns_existing_config_file(tmp_path: Path) -> None:
    db = tmp_path / "centronic-stick.db"
    db.write_bytes(b"db")

    assert _resolve_db_path(str(tmp_path), "centronic-stick.db") == str(db)


async def test_setup_entry_reports_invalid_database_path() -> None:
    hass = MagicMock()
    hass.config.config_dir = "/config"
    hass.async_add_executor_job = AsyncMock(side_effect=ValueError("bad path"))
    entry = SimpleNamespace(
        data={},
        entry_id="entry-1",
        title="Stick",
    )

    with patch("custom_components.becker._create_repair_issue") as create_issue:
        with pytest.raises(ConfigEntryError, match="Invalid Becker database path"):
            await async_setup_entry(hass, entry)

    create_issue.assert_called_once()


async def test_setup_entry_reports_connection_failure() -> None:
    hass = MagicMock()
    hass.config.config_dir = "/config"
    hass.async_add_executor_job = AsyncMock(
        side_effect=["/config/centronic-stick.db", BeckerConnectionError("offline")]
    )
    entry = SimpleNamespace(
        data={"device": "/dev/ttyUSB0"},
        options={},
        entry_id="entry-1",
        title="Stick",
    )

    with patch("custom_components.becker._create_repair_issue") as create_issue:
        with pytest.raises(ConfigEntryNotReady, match="Could not connect to Becker stick"):
            await async_setup_entry(hass, entry)

    create_issue.assert_called_once()

def test_remove_stale_cover_devices_keeps_root_and_current_cover() -> None:
    hass = MagicMock()
    registry = MagicMock()
    current = SimpleNamespace(
        id="current",
        identifiers={("becker", "entry-1_1")},
    )
    stale = SimpleNamespace(
        id="stale",
        identifiers={("becker", "entry-1_2")},
    )
    root = SimpleNamespace(
        id="root",
        identifiers={("becker", "entry-1")},
    )
    foreign = SimpleNamespace(
        id="foreign",
        identifiers={("other", "entry-1_3")},
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        subentries={
            "sub-1": SimpleNamespace(
                subentry_type="cover",
                data={"channel": "1"},
            )
        },
    )

    with (
        patch("custom_components.becker.dr.async_get", return_value=registry),
        patch(
            "custom_components.becker.dr.async_entries_for_config_entry",
            return_value=[root, current, stale, foreign],
        ),
    ):
        _remove_stale_cover_devices(hass, entry)

    registry.async_remove_device.assert_called_once_with("stale")


def test_remove_stale_cover_devices_removes_all_deleted_covers() -> None:
    hass = MagicMock()
    registry = MagicMock()
    stale = SimpleNamespace(
        id="stale",
        identifiers={("becker", "entry-1_4")},
    )
    entry = SimpleNamespace(entry_id="entry-1", subentries={})

    with (
        patch("custom_components.becker.dr.async_get", return_value=registry),
        patch(
            "custom_components.becker.dr.async_entries_for_config_entry",
            return_value=[stale],
        ),
    ):
        _remove_stale_cover_devices(hass, entry)

    registry.async_remove_device.assert_called_once_with("stale")

