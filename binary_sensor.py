"""Binary sensor platform for Becker connectivity."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import availability_signal_for_entry
from .const import DOMAIN

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Becker connection binary sensor."""
    async_add_entities([BeckerConnectionBinarySensor(entry.runtime_data, entry.entry_id)])


class BeckerConnectionBinarySensor(BinarySensorEntity):
    """Expose the Centronic stick connection state."""

    _attr_has_entity_name = True
    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, becker, entry_id: str) -> None:
        """Initialize the connection sensor."""
        self._becker = becker
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_connection"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})

    @property
    def is_on(self) -> bool:
        """Return whether the Becker communicator is available."""
        return self._becker.communicator.is_available()

    async def async_added_to_hass(self) -> None:
        """Subscribe to communicator availability changes."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                availability_signal_for_entry(self._entry_id),
                self._handle_availability,
            )
        )

    def _handle_availability(self) -> None:
        """Refresh state after a communicator availability change."""
        self.schedule_update_ha_state()
