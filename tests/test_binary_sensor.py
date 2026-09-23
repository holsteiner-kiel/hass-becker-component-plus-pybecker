"""Tests for the Becker connectivity binary sensor."""

from unittest.mock import MagicMock

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.becker import availability_signal_for_entry
from custom_components.becker.const import DOMAIN


async def setup_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Set up the Becker integration."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_connectivity_sensor_tracks_communicator(
    hass: HomeAssistant,
    mock_becker: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Connectivity sensor follows communicator availability changes."""
    mock_becker.communicator.is_available.return_value = True
    await setup_integration(hass, mock_config_entry)

    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id(
        "binary_sensor",
        DOMAIN,
        f"{mock_config_entry.entry_id}_connection",
    )
    assert entity_id is not None
    assert hass.states.get(entity_id).state == STATE_ON

    mock_becker.communicator.is_available.return_value = False
    async_dispatcher_send(
        hass,
        availability_signal_for_entry(mock_config_entry.entry_id),
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_OFF

    mock_becker.communicator.is_available.return_value = True
    async_dispatcher_send(
        hass,
        availability_signal_for_entry(mock_config_entry.entry_id),
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON
