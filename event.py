"""Event platform: surface received Becker remote presses as HA events."""

import codecs

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import availability_signal_for_entry, signal_for_entry
from .const import COMMANDS, DOMAIN

EVENT_TYPES = [
    "up",
    "up_intermediate",
    "down",
    "down_intermediate",
    "halt",
    "release",
    "unknown",
]


def decode_event_type(command: bytes, argument: bytes) -> str:
    """Map a received command/argument nibble pair to an event type.

    Tries the exact command+argument byte first (which distinguishes the
    intermediate positions), then command+"0" (argument nibble not
    meaningful), then falls back to "unknown" so nothing is dropped.
    """
    reverse = {code: name for name, code in COMMANDS.items()}
    return reverse.get(command + argument) or reverse.get(command + b"0") or "unknown"


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the remote-events entity for the gateway."""
    async_add_entities([BeckerRemoteEvent(entry.runtime_data, entry.entry_id)])


class BeckerRemoteEvent(EventEntity):
    """Surface RF packets received from physical Becker remotes."""

    _attr_has_entity_name = True
    _attr_translation_key = "remote"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = EVENT_TYPES

    def __init__(self, becker, entry_id: str) -> None:
        """Init the remote-events entity."""
        self._becker = becker
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_remote"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})

    @property
    def available(self):
        """Return whether the Becker communicator is currently available."""
        return self._becker.communicator.is_available()

    async def async_added_to_hass(self) -> None:
        """Subscribe to received packets on the gateway's dispatcher signal."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_for_entry(self._entry_id), self._handle_packet
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                availability_signal_for_entry(self._entry_id),
                self._handle_availability,
            )
        )

    @callback
    def _handle_availability(self) -> None:
        """Refresh state after a communicator availability change."""
        self.schedule_update_ha_state()

    @callback
    def _handle_packet(self, packet) -> None:
        """Fire an event for a received remote packet (runs on the loop)."""
        unit_id = codecs.decode(packet.group("unit_id"), "ascii")
        channel = codecs.decode(packet.group("channel"), "ascii")
        command_code = codecs.decode(
            packet.group("command") + packet.group("argument"), "ascii"
        )
        event_type = decode_event_type(
            packet.group("command"), packet.group("argument")
        )
        self._trigger_event(
            event_type,
            {"unit_id": unit_id, "channel": channel, "command_code": command_code},
        )
        self.schedule_update_ha_state()
