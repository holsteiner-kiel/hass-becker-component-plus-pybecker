"""The becker component."""

import codecs
import logging
import os
from functools import partial

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE, CONF_FILENAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.typing import ConfigType

from .const import (
    COMMANDS,
    CONF_CHANNEL,
    CONF_COMMAND_RETRY_DELAY,
    CONF_COMMAND_RETRY_MAX,
    CONF_ENTRY_ID,
    CONF_QUEUE_SIZE,
    CONF_UNIT,
    DEFAULT_COMMAND_RETRY_DELAY,
    DEFAULT_COMMAND_RETRY_MAX,
    DEFAULT_QUEUE_SIZE,
    DOMAIN,
    MANUFACTURER,
    PLATFORMS,
    RECEIVE_MESSAGE,
    REMOTE_PACKET_EVENT,
    SUBENTRY_TYPE_COVER,
)
from .http import BeckerDownloadView
from .pybecker.becker import Becker
from .pybecker.becker_helper import BeckerConnectionError
from .pybecker.database import FILE_PATH, SQL_DB_FILE

_LOGGER = logging.getLogger(__name__)

REPAIR_CONNECTION = "connection_unavailable"
REPAIR_DATABASE = "database_path_invalid"


def _repair_issue_id(kind: str, entry_id: str) -> str:
    """Return the issue id for one config entry."""
    return f"{kind}_{entry_id}"


def _create_repair_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    kind: str,
    translation_key: str,
) -> None:
    """Create an actionable repair issue for a setup problem."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _repair_issue_id(kind, entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=translation_key,
        translation_placeholders={"entry_title": entry.title},
    )


def _clear_setup_repairs(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear setup-related repair issues after a successful setup."""
    ir.async_delete_issue(
        hass, DOMAIN, _repair_issue_id(REPAIR_CONNECTION, entry.entry_id)
    )
    ir.async_delete_issue(
        hass, DOMAIN, _repair_issue_id(REPAIR_DATABASE, entry.entry_id)
    )

type BeckerConfigEntry = ConfigEntry[Becker]

PAIR_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CHANNEL): vol.All(int, vol.Range(min=1, max=7)),
        vol.Optional(CONF_UNIT): vol.All(int, vol.Range(min=1, max=5)),
        vol.Optional(CONF_ENTRY_ID): str,
    }
)

LOG_UNITS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ENTRY_ID): str,
    }
)


def signal_for_entry(entry_id: str) -> str:
    """Return the dispatcher signal carrying received packets of one stick."""
    return f"{DOMAIN}_{RECEIVE_MESSAGE}_{entry_id}"


def availability_signal_for_entry(entry_id: str) -> str:
    """Return the dispatcher signal for communicator availability changes."""
    return f"{DOMAIN}_availability_{entry_id}"


def _resolve_db_path(config_dir: str, filename: str | None) -> str:
    """Resolve the sqlite database path (blocking, run in executor)."""
    if filename is None:
        filename = SQL_DB_FILE
    if os.path.isfile(filename):
        return filename
    file = os.path.basename(filename)
    path = os.path.dirname(filename)
    if path == "":
        # file in HA config folder
        if os.path.isfile(os.path.join(config_dir, file)):
            return os.path.join(config_dir, file)
        # file in pybecker folder (legacy location, move it once)
        if os.path.isfile(os.path.join(FILE_PATH, file)):
            filename = os.path.join(config_dir, file)
            _LOGGER.debug("Move database file to %s", filename)
            os.rename(os.path.join(FILE_PATH, file), filename)
            return filename
        # create a new file in HA config folder
        _LOGGER.warning("Database file %s does not exist. Creating a new file", file)
        return os.path.join(config_dir, file)
    if not os.path.isdir(path):
        raise ValueError(
            f"Database directory {path} does not exist or is not a directory"
        )
    _LOGGER.warning("Database file %s does not exist. Creating a new file", filename)
    return filename


def _availability_callback(hass: HomeAssistant, entry_id: str) -> None:
    """Forward communicator availability changes safely onto the HA event loop."""
    hass.loop.call_soon_threadsafe(
        dispatcher_send,
        hass,
        availability_signal_for_entry(entry_id),
    )


def _dispatch_packet_on_loop(hass: HomeAssistant, entry_id: str, packet) -> None:
    """Forward a received packet from Home Assistant's event-loop thread."""
    _LOGGER.debug("Received packet for dispatcher")
    dispatcher_send(hass, signal_for_entry(entry_id), packet)

    data = {
        "unit": codecs.decode(packet.group("unit_id"), "ascii"),
        "channel": codecs.decode(packet.group("channel"), "ascii"),
    }
    command = packet.group("command") + b"0"
    command_name = [nm for nm, cmd in COMMANDS.items() if cmd == command]
    if command_name:
        data["command"] = command_name[0]
    hass.bus.fire(f"{DOMAIN}_{REMOTE_PACKET_EVENT}", data)


def _packet_callback(hass: HomeAssistant, entry_id: str, packet) -> None:
    """Forward a received RF packet safely from the communicator thread."""
    hass.loop.call_soon_threadsafe(
        _dispatch_packet_on_loop,
        hass,
        entry_id,
        packet,
    )


def _get_becker(hass: HomeAssistant, entry_id: str | None = None) -> Becker:
    """Return the Becker instance selected for a service call."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("No loaded Becker configuration entry found")

    if entry_id is not None:
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry.runtime_data
        raise ServiceValidationError(
            f"Becker configuration entry {entry_id} is not loaded"
        )

    if len(entries) > 1:
        raise ServiceValidationError(
            "Multiple Becker configuration entries are loaded; specify entry_id"
        )

    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the becker services."""

    async def handle_pair(call: ServiceCall) -> None:
        """Pair with a cover receiver."""
        channel = call.data[CONF_CHANNEL]
        unit = call.data.get(CONF_UNIT, 1)
        await _get_becker(hass, call.data.get(CONF_ENTRY_ID)).pair(
            f"{unit}:{channel}"
        )

    async def handle_log_units(call: ServiceCall) -> None:
        """Log all paired units."""
        units = await _get_becker(hass, call.data.get(CONF_ENTRY_ID)).list_units()
        # Apparently the SQLite results are implicitly returned in unit id
        # order. This seems pretty dirty to rely on.
        _LOGGER.info("Configured Becker centronic units:")
        for unit_id, row in enumerate(units, start=1):
            unit_code, increment = row[0:2]
            _LOGGER.info(
                "Unit id %d, unit code %s, increment %d", unit_id, unit_code, increment
            )

    hass.services.async_register(DOMAIN, "pair", handle_pair, PAIR_SCHEMA)
    hass.services.async_register(
        DOMAIN, "log_units", handle_log_units, LOG_UNITS_SCHEMA
    )
    hass.http.register_view(BeckerDownloadView())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BeckerConfigEntry) -> bool:
    """Set up a Becker Centronic stick from a config entry."""
    try:
        filename = await hass.async_add_executor_job(
            _resolve_db_path, hass.config.config_dir, entry.data.get(CONF_FILENAME)
        )
    except ValueError as err:
        _create_repair_issue(
            hass, entry, REPAIR_DATABASE, "database_path_invalid"
        )
        raise ConfigEntryError(
            f"Invalid Becker database path: {err}"
        ) from err
    _LOGGER.debug("Using database file %s", filename)

    try:
        becker = await hass.async_add_executor_job(
            partial(
                Becker,
                device_name=entry.data[CONF_DEVICE],
                init_dummy=False,
                db_filename=filename,
                callback=partial(_packet_callback, hass, entry.entry_id),
                availability_callback=partial(
                    _availability_callback,
                    hass,
                    entry.entry_id,
                ),
                queue_size=entry.options.get(CONF_QUEUE_SIZE, DEFAULT_QUEUE_SIZE),
                retry_max=entry.options.get(
                    CONF_COMMAND_RETRY_MAX, DEFAULT_COMMAND_RETRY_MAX
                ),
                retry_delay=entry.options.get(
                    CONF_COMMAND_RETRY_DELAY, DEFAULT_COMMAND_RETRY_DELAY
                ),
            )
        )
    except BeckerConnectionError as err:
        _create_repair_issue(
            hass, entry, REPAIR_CONNECTION, "connection_unavailable"
        )
        raise ConfigEntryNotReady(
            f"Could not connect to Becker stick on {entry.data[CONF_DEVICE]}: {err}"
        ) from err
    entry.runtime_data = becker
    _clear_setup_repairs(hass, entry)

    # Initialize all units of configured covers in the db file and send a
    # stop command for sync. Sequential on purpose: RF commands must not
    # overlap and pybecker paces them with sleeps.
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_COVER:
            continue
        await becker.init_unconfigured_unit(
            subentry.data[CONF_CHANNEL], name=subentry.title
        )

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        name="Centronic stick",
        model="Centronic USB stick",
    )

    entry.async_on_unload(entry.add_update_listener(_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BeckerConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Stops the communicator thread (blocking join) and closes the db
        await hass.async_add_executor_job(entry.runtime_data.close)
    return unload_ok


async def _update_listener(hass: HomeAssistant, entry: BeckerConfigEntry) -> None:
    """Reload the entry when subentries change."""
    hass.config_entries.async_schedule_reload(entry.entry_id)
