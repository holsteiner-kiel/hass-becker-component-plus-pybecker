"""Tests for Becker service target selection."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.becker import _get_becker


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
