"""Tests for Becker integration diagnostics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.becker.const import (
    CONF_CHANNEL,
    CONF_COMMAND_RETRY_DELAY,
    CONF_COMMAND_RETRY_MAX,
    CONF_CONNECTION_TYPE,
    CONF_QUEUE_SIZE,
    CONNECTION_TYPE_SERIAL,
    SUBENTRY_TYPE_COVER,
)
from custom_components.becker.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_config_entry_diagnostics_are_privacy_safe() -> None:
    """Diagnostics expose useful status without rolling-code or path data."""
    communicator = MagicMock()
    communicator.diagnostics.return_value = {
        "thread_alive": True,
        "connection_open": True,
        "transport": "serial",
        "queue_depth": 1,
        "queue_capacity": 20,
        "queue_percent": 5.0,
        "retry_max": 4,
        "retry_delay": 0.5,
        "stopping": False,
    }

    becker = SimpleNamespace(
        communicator=communicator,
        list_units=AsyncMock(
            return_value=[
                ["1737b", 1234, 1, 0],
                ["1737c", 5678, 0, 0],
            ]
        ),
    )
    entry = SimpleNamespace(
        runtime_data=becker,
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_SERIAL,
            "device": "/dev/serial/by-id/secret-device",
            "filename": "/config/private/centronic-stick.db",
        },
        options={
            CONF_QUEUE_SIZE: 20,
            CONF_COMMAND_RETRY_MAX: 4,
            CONF_COMMAND_RETRY_DELAY: 0.5,
        },
        subentries={
            "one": SimpleNamespace(
                subentry_type=SUBENTRY_TYPE_COVER,
                data={CONF_CHANNEL: "1", "remote_id": "ABCDE:1"},
            ),
            "two": SimpleNamespace(
                subentry_type=SUBENTRY_TYPE_COVER,
                data={CONF_CHANNEL: "2"},
            ),
        },
    )

    diagnostics = await async_get_config_entry_diagnostics(MagicMock(), entry)

    assert diagnostics["configuration"] == {
        "connection_type": CONNECTION_TYPE_SERIAL,
        "queue_size": 20,
        "command_retry_max": 4,
        "command_retry_delay": 0.5,
    }
    assert diagnostics["covers"] == {"count": 2, "channels": ["1", "2"]}
    assert diagnostics["database"] == {
        "unit_count": 2,
        "configured_unit_count": 1,
    }
    assert diagnostics["communication"]["thread_alive"] is True

    rendered = repr(diagnostics)
    assert "secret-device" not in rendered
    assert "centronic-stick.db" not in rendered
    assert "ABCDE:1" not in rendered
    assert "1737b" not in rendered
    assert "1234" not in rendered
