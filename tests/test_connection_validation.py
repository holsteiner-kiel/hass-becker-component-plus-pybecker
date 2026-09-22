"""Tests for strict Becker connection validation."""

from unittest.mock import MagicMock, patch

import pytest
import serial

from custom_components.becker.pybecker.becker_helper import (
    BeckerConnection,
    BeckerConnectionError,
)


def _failing_serial() -> MagicMock:
    connection = MagicMock()
    connection.is_open = False
    connection.open.side_effect = serial.SerialException("connection refused")
    return connection


def test_strict_connection_raises_when_open_fails() -> None:
    connection = _failing_serial()

    with patch(
        "custom_components.becker.pybecker.becker_helper.serial.serial_for_url",
        return_value=connection,
    ):
        with pytest.raises(BeckerConnectionError, match="Could not open"):
            BeckerConnection("127.0.0.1:5999", strict=True)


def test_runtime_connection_keeps_resilient_open_behavior() -> None:
    connection = _failing_serial()

    with patch(
        "custom_components.becker.pybecker.becker_helper.serial.serial_for_url",
        return_value=connection,
    ):
        becker_connection = BeckerConnection("127.0.0.1:5999")

    assert becker_connection.device == "socket://127.0.0.1:5999"
    assert connection.open.call_count == 1


def test_strict_connection_closes_successful_connection() -> None:
    connection = MagicMock()
    connection.is_open = False

    def open_connection() -> None:
        connection.is_open = True

    connection.open.side_effect = open_connection

    with patch(
        "custom_components.becker.pybecker.becker_helper.serial.serial_for_url",
        return_value=connection,
    ):
        becker_connection = BeckerConnection("127.0.0.1:5000", strict=True)
        becker_connection.close()

    connection.close.assert_called_once()
