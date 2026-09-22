"""Tests for the becker cover entity."""

from datetime import timedelta
import logging

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    DOMAIN as COVER_DOMAIN,
    SERVICE_OPEN_COVER,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant

ENTITY_ID = "cover.kitchen"


async def setup_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Set up the becker integration."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("mock_becker")
async def test_position_updates_during_travel(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_config_entry_with_timed_cover: MockConfigEntry,
) -> None:
    """The reported position changes during travel, not only at the end."""
    await setup_integration(hass, mock_config_entry_with_timed_cover)

    # Cover starts closed (0 %).
    assert hass.states.get(ENTITY_ID).attributes[ATTR_CURRENT_POSITION] == 0

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_OPEN_COVER,
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )

    positions = []
    for _ in range(5):
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        positions.append(hass.states.get(ENTITY_ID).attributes[ATTR_CURRENT_POSITION])

    # Over a 10 s travel, each 1 s tick should report a higher position.
    assert positions == [10, 20, 30, 40, 50]


@pytest.mark.usefixtures("mock_becker")
async def test_movement_debug_log_uses_cover_name_without_tick_noise(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    mock_config_entry_with_timed_cover: MockConfigEntry,
) -> None:
    """Movement logs use the configured name and omit internal refresh noise."""
    caplog.set_level(logging.DEBUG, logger="custom_components.becker.cover")
    await setup_integration(hass, mock_config_entry_with_timed_cover)
    caplog.clear()

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_OPEN_COVER,
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )

    messages = [record.getMessage() for record in caplog.records]
    assert "[Kitchen] Moving: 0 -> 100 (10.0 s)" in messages
    assert not any(message.startswith("None ") for message in messages)
    assert not any("update ha-state" in message for message in messages)


@pytest.mark.usefixtures("mock_becker")
async def test_numeric_value_template_sets_position(
    hass: HomeAssistant,
    mock_config_entry_with_template_cover: MockConfigEntry,
) -> None:
    """A numeric value template sets the exact position, not just open/closed."""
    hass.states.async_set("sensor.becker_pos", "42")
    await setup_integration(hass, mock_config_entry_with_template_cover)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).attributes[ATTR_CURRENT_POSITION] == 42
