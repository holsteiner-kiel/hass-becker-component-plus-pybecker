"""Tests for the becker pair button."""

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.button import (
    DOMAIN as BUTTON_DOMAIN,
    SERVICE_PRESS,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

ENTITY_ID = "button.kitchen_pair"


async def setup_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Set up the becker integration."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("mock_config_entry_with_cover")
async def test_pair_button_sends_train(
    hass: HomeAssistant,
    mock_becker: MagicMock,
    mock_config_entry_with_cover: MockConfigEntry,
) -> None:
    """Pressing the pair button sends the pairing signal on the cover channel."""
    await setup_integration(hass, mock_config_entry_with_cover)

    assert hass.states.get(ENTITY_ID) is not None

    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )

    mock_becker.pair.assert_awaited_once_with("1")



@pytest.mark.usefixtures("mock_config_entry_with_cover")
async def test_pair_button_unavailable_when_communicator_is_down(
    hass: HomeAssistant,
    mock_becker: MagicMock,
    mock_config_entry_with_cover: MockConfigEntry,
) -> None:
    """Pair button reflects communicator availability."""
    mock_becker.communicator.is_available.return_value = False
    await setup_integration(hass, mock_config_entry_with_cover)

    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE



def test_pair_button_availability_handler_schedules_update(
    mock_becker: MagicMock,
) -> None:
    from custom_components.becker.button import BeckerPairButton

    button = BeckerPairButton(mock_becker, "entry-1", "1", "Kitchen")
    button.schedule_update_ha_state = MagicMock()

    button._handle_availability()

    button.schedule_update_ha_state.assert_called_once()


def test_pair_button_available_reflects_communicator(
    mock_becker: MagicMock,
) -> None:
    from custom_components.becker.button import BeckerPairButton

    button = BeckerPairButton(mock_becker, "entry-1", "1", "Kitchen")
    mock_becker.communicator.is_available.return_value = True
    assert button.available is True

    mock_becker.communicator.is_available.return_value = False
    assert button.available is False
