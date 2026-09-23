"""Focused tests for pybecker helper and transport behavior."""

import logging
import queue
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import serial

from custom_components.becker.pybecker.becker_helper import (
    BeckerCommunicator,
    BeckerConnection,
    BeckerConnectionError,
    COMMANDS,
    MESSAGE,
    checksum,
    finalize_code,
    generate_code,
    hex2,
    hex4,
)


def test_hex_helpers_and_finalize_code() -> None:
    assert hex2(0x123) == "23"
    assert hex4(0x12345) == "2345"
    assert finalize_code("ABC") == b"\x02ABC\x03"


def test_checksum_rejects_wrong_length(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR)

    assert checksum("1234") is None
    assert "40 characters long" in caplog.text


def test_checksum_and_generate_code_are_deterministic() -> None:
    unit = ["1737b", 0x12, 1]

    raw = generate_code(2, unit, 0x20, with_checksum=False)
    framed = generate_code(2, unit, 0x20, with_checksum=True)

    assert len(raw) == 40
    assert raw.startswith("0000000002010B0012")
    assert raw.endswith("020020")
    assert framed.startswith(raw.upper())
    assert len(framed) == 42


def test_generate_code_channel_zero_uses_primary_sender_format() -> None:
    raw = generate_code(0, ["1737b", 1, 1], 0x10, with_checksum=False)

    assert len(raw) == 40
    assert "02100" in raw
    assert raw.endswith("000010")


@pytest.mark.parametrize(
    ("device", "expected", "is_serial"),
    [
        ("192.0.2.10", "socket://192.0.2.10:5000", False),
        ("192.0.2.10:6000", "socket://192.0.2.10:6000", False),
        ("socket://192.0.2.10:5000", "socket://192.0.2.10:5000", False),
    ],
)
def test_validate_network_devices(
    device: str, expected: str, is_serial: bool
) -> None:
    normalized, serial_device = BeckerConnection._validate_device(device)
    assert normalized == expected
    assert serial_device is is_serial


def test_validate_missing_linux_serial_device_raises() -> None:
    with patch(
        "custom_components.becker.pybecker.becker_helper.os.path.exists",
        return_value=False,
    ):
        with pytest.raises(BeckerConnectionError, match="not existing"):
            BeckerConnection._validate_device("/dev/ttyUSB404")


def test_build_serial_wraps_pyserial_error() -> None:
    connection = BeckerConnection.__new__(BeckerConnection)
    connection._device = "socket://127.0.0.1:5000"
    connection._is_serial = False

    with patch(
        "custom_components.becker.pybecker.becker_helper.serial.serial_for_url",
        side_effect=serial.SerialException("boom"),
    ):
        with pytest.raises(BeckerConnectionError, match="establish connection"):
            connection._build_serial()


def test_connection_write_reopens_after_failure() -> None:
    transport = MagicMock()
    transport.is_open = True
    transport.write.side_effect = [OSError("gone"), None]

    connection = BeckerConnection.__new__(BeckerConnection)
    connection._device = "/dev/ttyUSB0"
    connection._is_serial = True
    connection._connection = transport
    connection._last_rebuild_attempt = 0.0

    def reopen(strict: bool = False) -> None:
        transport.is_open = True

    connection._open = MagicMock(side_effect=reopen)

    connection.write(b"packet")

    transport.close.assert_called_once()
    assert connection._open.call_count == 2
    assert transport.write.call_count == 2


def test_connection_read_closes_after_failure() -> None:
    transport = MagicMock()
    transport.is_open = True
    transport.read.side_effect = OSError("gone")

    connection = BeckerConnection.__new__(BeckerConnection)
    connection._device = "/dev/ttyUSB0"
    connection._is_serial = True
    connection._connection = transport
    connection._last_rebuild_attempt = 0.0
    connection._open = MagicMock()

    assert connection.read() == b""
    transport.close.assert_called_once()


def _communicator() -> BeckerCommunicator:
    communicator = BeckerCommunicator.__new__(BeckerCommunicator)
    threading.Thread.__init__(communicator, daemon=True)
    communicator._queue_size = 10
    communicator._retry_max = 1
    communicator._retry_delay = 0
    communicator._write_queue = queue.Queue(maxsize=10)
    communicator._stop_flag = threading.Event()
    communicator._force_stop_flag = threading.Event()
    communicator._availability_callback = None
    communicator._last_available = None
    communicator._read_buffer = b""
    communicator._connection = SimpleNamespace(
        is_open=True,
        is_serial=True,
        close=lambda: None,
    )
    communicator.is_alive = lambda: True
    return communicator


def _packet(command: bytes = b"2", argument: bytes = b"0") -> bytes:
    body = (
        b"0000000002010B"
        b"0001"
        b"000000"
        b"1737B"
        b"000000"
        b"1"
        b"00"
        + command
        + argument
        + b"00"
    )
    packet = b"\x02" + body + b"\x03"
    assert MESSAGE.search(packet) is not None
    return packet


def test_parser_emits_complete_messages_and_keeps_tail() -> None:
    communicator = _communicator()
    callback = MagicMock()
    communicator._callback = callback
    packet = _packet()

    communicator._read_buffer = b"garbage" + packet + b"tail"
    communicator._parse()

    callback.assert_called_once()
    assert callback.call_args.args[0].group(0) == packet
    assert communicator._read_buffer == b"tail"


def test_debug_packet_log_handles_known_and_unknown_commands(
    caplog: pytest.LogCaptureFixture,
) -> None:
    communicator = _communicator()
    caplog.set_level(
        logging.DEBUG,
        logger="custom_components.becker.pybecker.becker_helper",
    )

    communicator._log(_packet(b"2", b"0"), "RX: ")
    communicator._log(_packet(b"F", b"0"), "RX: ")

    assert "command: UP" in caplog.text
    assert "command: F" in caplog.text


def test_send_rejects_dead_communicator() -> None:
    communicator = _communicator()
    communicator.is_alive = lambda: False

    with pytest.raises(BeckerConnectionError, match="not alive"):
        communicator.send(b"packet")


def test_send_logs_queue_pressure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    communicator = _communicator()
    caplog.set_level(
        logging.INFO,
        logger="custom_components.becker.pybecker.becker_helper",
    )

    for _ in range(5):
        communicator._write_queue.put(b"x")
    communicator.send(b"half")
    assert "50% full" in caplog.text

    while communicator._write_queue.qsize() < 8:
        communicator._write_queue.put(b"x")
    communicator.send(b"high")
    assert "80% full" in caplog.text


def test_send_retries_queue_full_then_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    communicator = _communicator()
    communicator._write_queue = MagicMock()
    communicator._write_queue.qsize.return_value = 0
    communicator._write_queue.put.side_effect = [queue.Full(), None]
    caplog.set_level(
        logging.INFO,
        logger="custom_components.becker.pybecker.becker_helper",
    )

    communicator.send(b"packet")

    assert communicator._write_queue.put.call_count == 2
    assert "retrying" in caplog.text
    assert "queued after 1 retry" in caplog.text


def test_close_forces_shutdown_when_thread_does_not_drain() -> None:
    communicator = _communicator()
    connection = MagicMock()
    communicator._connection = connection
    communicator.stop = MagicMock()
    communicator.join = MagicMock()
    communicator.is_alive = MagicMock(side_effect=[True, False])

    communicator.close()

    communicator.stop.assert_called_once()
    assert communicator.join.call_count == 2
    assert communicator._force_stop_flag.is_set()
    connection.close.assert_called_once()


def test_diagnostics_handles_zero_queue_capacity() -> None:
    communicator = _communicator()
    communicator._queue_size = 0
    communicator._write_queue = MagicMock()
    communicator._write_queue.qsize.return_value = 0

    diagnostics = communicator.diagnostics()

    assert diagnostics["queue_percent"] == 0.0
    assert diagnostics["transport"] == "serial"



def test_open_returns_when_transport_is_already_open() -> None:
    connection = BeckerConnection.__new__(BeckerConnection)
    transport = MagicMock()
    transport.is_open = True
    connection._connection = transport

    connection._open()

    transport.open.assert_not_called()


def test_open_runtime_serial_failure_triggers_rebuild() -> None:
    connection = BeckerConnection.__new__(BeckerConnection)
    connection._device = "/dev/ttyUSB0"
    connection._is_serial = True
    connection._last_rebuild_attempt = 0.0
    transport = MagicMock()
    transport.is_open = False
    transport.open.side_effect = OSError("gone")
    connection._connection = transport
    connection._maybe_rebuild = MagicMock()

    connection._open()

    connection._maybe_rebuild.assert_called_once()


def test_open_runtime_network_failure_does_not_rebuild() -> None:
    connection = BeckerConnection.__new__(BeckerConnection)
    connection._device = "socket://host:5000"
    connection._is_serial = False
    connection._last_rebuild_attempt = 0.0
    transport = MagicMock()
    transport.is_open = False
    transport.open.side_effect = OSError("offline")
    connection._connection = transport
    connection._maybe_rebuild = MagicMock()

    connection._open()

    connection._maybe_rebuild.assert_not_called()


def test_maybe_rebuild_is_rate_limited() -> None:
    connection = BeckerConnection.__new__(BeckerConnection)
    connection._device = "/dev/ttyUSB0"
    connection._is_serial = True
    connection._last_rebuild_attempt = 100.0
    connection._connection = MagicMock()

    with patch(
        "custom_components.becker.pybecker.becker_helper.time.time",
        return_value=105.0,
    ):
        connection._build_serial = MagicMock()
        connection._maybe_rebuild()

    connection._build_serial.assert_not_called()


def test_maybe_rebuild_replaces_transport() -> None:
    connection = BeckerConnection.__new__(BeckerConnection)
    connection._device = "/dev/ttyUSB0"
    connection._is_serial = True
    connection._last_rebuild_attempt = 0.0
    old_transport = MagicMock()
    new_transport = MagicMock()
    connection._connection = old_transport
    connection._build_serial = MagicMock(return_value=new_transport)

    with patch(
        "custom_components.becker.pybecker.becker_helper.time.time",
        return_value=100.0,
    ):
        connection._maybe_rebuild()

    old_transport.close.assert_called_once()
    assert connection._connection is new_transport


def test_maybe_rebuild_keeps_old_transport_when_build_fails() -> None:
    connection = BeckerConnection.__new__(BeckerConnection)
    connection._device = "/dev/ttyUSB0"
    connection._is_serial = True
    connection._last_rebuild_attempt = 0.0
    old_transport = MagicMock()
    connection._connection = old_transport
    connection._build_serial = MagicMock(
        side_effect=BeckerConnectionError("nope")
    )

    with patch(
        "custom_components.becker.pybecker.becker_helper.time.time",
        return_value=100.0,
    ):
        connection._maybe_rebuild()

    assert connection._connection is old_transport


def test_close_skips_already_closed_transport() -> None:
    connection = BeckerConnection.__new__(BeckerConnection)
    transport = MagicMock()
    transport.is_open = False
    connection._connection = transport

    connection.close()

    transport.close.assert_not_called()


def test_send_raises_after_all_queue_retries() -> None:
    communicator = _communicator()
    communicator._retry_max = 1
    communicator._write_queue = MagicMock()
    communicator._write_queue.qsize.return_value = 10
    communicator._write_queue.put.side_effect = queue.Full()

    with pytest.raises(BeckerConnectionError, match="queue is full"):
        communicator.send(b"packet")

    assert communicator._write_queue.put.call_count == 2



def test_connection_properties_expose_transport_state() -> None:
    connection = BeckerConnection.__new__(BeckerConnection)
    transport = MagicMock()
    transport.is_open = True
    connection._device = "/dev/ttyUSB0"
    connection._is_serial = True
    connection._connection = transport

    assert connection.device == "/dev/ttyUSB0"
    assert connection.is_serial is True
    assert connection.is_open is True


def test_open_strict_wraps_transport_error() -> None:
    connection = BeckerConnection.__new__(BeckerConnection)
    connection._device = "/dev/ttyUSB0"
    connection._is_serial = True
    transport = MagicMock()
    transport.is_open = False
    transport.open.side_effect = OSError("offline")
    connection._connection = transport

    with pytest.raises(BeckerConnectionError, match="Could not open Becker connection"):
        connection._open(strict=True)


def test_validate_device_none_uses_default() -> None:
    with patch(
        "custom_components.becker.pybecker.becker_helper.os.path.exists",
        return_value=True,
    ):
        normalized, serial_device = BeckerConnection._validate_device(None)

    assert normalized.endswith("Centronic-if00")
    assert serial_device is True


def test_validate_existing_linux_serial_device() -> None:
    with patch(
        "custom_components.becker.pybecker.becker_helper.os.path.exists",
        return_value=True,
    ):
        normalized, serial_device = BeckerConnection._validate_device("/dev/ttyUSB0")

    assert normalized == "/dev/ttyUSB0"
    assert serial_device is True


def test_validate_windows_com_device() -> None:
    port = SimpleNamespace(device="COM4")
    with (
        patch(
            "custom_components.becker.pybecker.becker_helper.sys.platform",
            "win32",
        ),
        patch(
            "custom_components.becker.pybecker.becker_helper.serial.tools.list_ports.comports",
            return_value=[port],
        ),
    ):
        normalized, serial_device = BeckerConnection._validate_device("com4")

    assert normalized == "com4"
    assert serial_device is True


def test_validate_missing_windows_com_device_raises() -> None:
    with (
        patch(
            "custom_components.becker.pybecker.becker_helper.sys.platform",
            "win32",
        ),
        patch(
            "custom_components.becker.pybecker.becker_helper.serial.tools.list_ports.comports",
            return_value=[],
        ),
    ):
        with pytest.raises(BeckerConnectionError, match="not existing"):
            BeckerConnection._validate_device("COM9")


def test_validate_non_socket_path_is_left_unchanged() -> None:
    normalized, serial_device = BeckerConnection._validate_device("relative/path")

    assert normalized == "relative/path"
    assert serial_device is False


def test_maybe_rebuild_tolerates_close_failure() -> None:
    connection = BeckerConnection.__new__(BeckerConnection)
    connection._device = "/dev/ttyUSB0"
    connection._is_serial = True
    connection._last_rebuild_attempt = 0.0
    old_transport = MagicMock()
    old_transport.close.side_effect = OSError("already gone")
    new_transport = MagicMock()
    connection._connection = old_transport
    connection._build_serial = MagicMock(return_value=new_transport)

    with patch(
        "custom_components.becker.pybecker.becker_helper.time.time",
        return_value=100.0,
    ):
        connection._maybe_rebuild()

    assert connection._connection is new_transport


def test_availability_callback_only_fires_on_transition() -> None:
    communicator = _communicator()
    callback = MagicMock()
    communicator._availability_callback = callback
    communicator.is_available = MagicMock(side_effect=[True, True, False, False, True])

    communicator._notify_availability_if_changed()
    communicator._notify_availability_if_changed()
    communicator._notify_availability_if_changed()
    communicator._notify_availability_if_changed()
    communicator._notify_availability_if_changed()

    assert callback.call_count == 3
    assert communicator._last_available is True


def test_diagnostics_reports_network_transport_and_stopping() -> None:
    communicator = _communicator()
    communicator._connection.is_serial = False
    communicator._stop_flag.set()

    diagnostics = communicator.diagnostics()

    assert diagnostics["transport"] == "network"
    assert diagnostics["stopping"] is True


def test_log_ignores_non_message_packet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    communicator = _communicator()
    caplog.set_level(
        logging.DEBUG,
        logger="custom_components.becker.pybecker.becker_helper",
    )

    communicator._log(b"not-a-packet", "RX: ")

    assert "unit_id:" not in caplog.text



def test_communicator_constructor_initializes_runtime_state() -> None:
    connection = MagicMock()

    with (
        patch(
            "custom_components.becker.pybecker.becker_helper.BeckerConnection",
            return_value=connection,
        ) as connection_cls,
        patch(
            "custom_components.becker.pybecker.becker_helper.time.time",
            return_value=123.5,
        ),
    ):
        communicator = BeckerCommunicator(
            "/dev/ttyUSB0",
            callback=MagicMock(),
            availability_callback=MagicMock(),
            deamon=True,
            queue_size=7,
            retry_max=4,
            retry_delay=0.25,
        )

    connection_cls.assert_called_once_with(device="/dev/ttyUSB0")
    assert communicator.daemon is True
    assert communicator._queue_size == 7
    assert communicator._retry_max == 4
    assert communicator._retry_delay == 0.25
    assert communicator._write_queue.maxsize == 7
    assert communicator._connection is connection
    assert communicator._read_buffer == b""
    assert communicator._timeout == 123.5
    assert communicator._stop_flag.is_set() is False
    assert communicator._force_stop_flag.is_set() is False
    assert communicator._last_available is None
