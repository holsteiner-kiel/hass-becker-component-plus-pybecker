"""Tests for Becker service target selection."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.becker import _get_becker, async_setup


def _hass_with_entries(*entries):
    hass = MagicMock()
    hass.config_entries.async_loaded_entries.return_value = list(entries)
    return hass


def test_service_uses_single_loaded_entry_without_selector() -> None:
    becker = MagicMock()
    entry = SimpleNamespace(entry_id="entry-1", runtime_data=becker)

    assert _get_becker(_hass_with_entries(entry)) is becker


def test_service_selects_requested_entry() -> None:
    first = SimpleNamespace(entry_id="entry-1", runtime_data=MagicMock())
    second_becker = MagicMock()
    second = SimpleNamespace(entry_id="entry-2", runtime_data=second_becker)

    assert _get_becker(_hass_with_entries(first, second), "entry-2") is second_becker


def test_service_requires_selector_with_multiple_entries() -> None:
    first = SimpleNamespace(entry_id="entry-1", runtime_data=MagicMock())
    second = SimpleNamespace(entry_id="entry-2", runtime_data=MagicMock())

    with pytest.raises(ServiceValidationError, match="specify entry_id"):
        _get_becker(_hass_with_entries(first, second))


def test_service_rejects_unknown_entry() -> None:
    entry = SimpleNamespace(entry_id="entry-1", runtime_data=MagicMock())

    with pytest.raises(ServiceValidationError, match="is not loaded"):
        _get_becker(_hass_with_entries(entry), "missing")



@pytest.mark.asyncio
async def test_async_setup_registers_services_and_http_view() -> None:
    hass = MagicMock()
    hass.services.async_register = MagicMock()
    hass.http.register_view = MagicMock()

    assert await async_setup(hass, {}) is True

    assert hass.services.async_register.call_count == 2
    hass.http.register_view.assert_called_once()


@pytest.mark.asyncio
async def test_registered_pair_service_routes_to_selected_entry() -> None:
    hass = MagicMock()
    pair = AsyncMock()
    entry = SimpleNamespace(entry_id="entry-1", runtime_data=SimpleNamespace(pair=pair))
    hass.config_entries.async_loaded_entries.return_value = [entry]
    handlers = {}

    def register(domain, service, handler, schema):
        handlers[service] = handler

    hass.services.async_register.side_effect = register
    hass.http.register_view = MagicMock()

    await async_setup(hass, {})

    call = SimpleNamespace(data={"entry_id": "entry-1", "channel": 3, "unit": 2})
    await handlers["pair"](call)

    pair.assert_awaited_once_with("2:3")


@pytest.mark.asyncio
async def test_registered_log_units_service_reads_units() -> None:
    hass = MagicMock()
    list_units = AsyncMock(return_value=[["1737b", 42, 1]])
    entry = SimpleNamespace(entry_id="entry-1", runtime_data=SimpleNamespace(list_units=list_units))
    hass.config_entries.async_loaded_entries.return_value = [entry]
    handlers = {}

    def register(domain, service, handler, schema):
        handlers[service] = handler

    hass.services.async_register.side_effect = register
    hass.http.register_view = MagicMock()

    await async_setup(hass, {})

    call = SimpleNamespace(data={"entry_id": "entry-1"})
    await handlers["log_units"](call)

    list_units.assert_awaited_once()
