"""Diagnostics support for the Becker integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import BeckerConfigEntry
from .const import (
    CONF_CHANNEL,
    CONF_COMMAND_RETRY_DELAY,
    CONF_COMMAND_RETRY_MAX,
    CONF_CONNECTION_TYPE,
    CONF_QUEUE_SIZE,
    DEFAULT_COMMAND_RETRY_DELAY,
    DEFAULT_COMMAND_RETRY_MAX,
    DEFAULT_QUEUE_SIZE,
    SUBENTRY_TYPE_COVER,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BeckerConfigEntry
) -> dict[str, Any]:
    """Return privacy-safe diagnostics for a Becker config entry."""
    becker = entry.runtime_data
    units = await becker.list_units()

    channels = sorted(
        str(subentry.data[CONF_CHANNEL])
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_COVER
    )

    return {
        "configuration": {
            "connection_type": entry.data.get(CONF_CONNECTION_TYPE),
            "queue_size": entry.options.get(CONF_QUEUE_SIZE, DEFAULT_QUEUE_SIZE),
            "command_retry_max": entry.options.get(
                CONF_COMMAND_RETRY_MAX, DEFAULT_COMMAND_RETRY_MAX
            ),
            "command_retry_delay": entry.options.get(
                CONF_COMMAND_RETRY_DELAY, DEFAULT_COMMAND_RETRY_DELAY
            ),
        },
        "covers": {
            "count": len(channels),
            "channels": channels,
        },
        "database": {
            "unit_count": len(units),
            "configured_unit_count": sum(
                1 for unit in units if len(unit) > 2 and int(unit[2]) == 1
            ),
        },
        "communication": becker.communicator.diagnostics(),
    }
