"""Diagnostics support for the Becker integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from . import BeckerConfigEntry
_LOGGER = logging.getLogger(__name__)

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

    try:
        units = await becker.list_units()
    except Exception as err:  # Diagnostics must remain available during failures.
        _LOGGER.debug("Could not read Becker database diagnostics", exc_info=True)
        database = {
            "available": False,
            "error_type": type(err).__name__,
        }
    else:
        database = {
            "available": True,
            "unit_count": len(units),
            "configured_unit_count": sum(
                1 for unit in units if len(unit) > 2 and int(unit[2]) == 1
            ),
        }

    try:
        communication = {
            "available": True,
            **becker.communicator.diagnostics(),
        }
    except Exception as err:  # Preserve diagnostics even for partial runtime failure.
        _LOGGER.debug("Could not read Becker communicator diagnostics", exc_info=True)
        communication = {
            "available": False,
            "error_type": type(err).__name__,
        }

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
        "database": database,
        "communication": communication,
    }
