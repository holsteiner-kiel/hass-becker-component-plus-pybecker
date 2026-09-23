"""Regression tests for Becker communicator reconnect behavior."""

import queue
import threading
import time

from custom_components.becker.pybecker.becker_helper import BeckerCommunicator


class _FlakyConnection:
    def __init__(self) -> None:
        self.is_open = True
        self.is_serial = True
        self.read_calls = 0
        self.write_calls = 0
        self.written = []

    def read(self) -> bytes:
        self.read_calls += 1
        if self.read_calls == 1:
            raise OSError("temporary USB read failure")
        return b""

    def write(self, packet: bytes) -> None:
        self.write_calls += 1
        if self.write_calls == 1:
            raise OSError("temporary USB write failure")
        self.written.append(packet)

    def close(self) -> None:
        return None


def _build_communicator(connection: _FlakyConnection) -> BeckerCommunicator:
    communicator = BeckerCommunicator.__new__(BeckerCommunicator)
    threading.Thread.__init__(communicator, daemon=True)
    communicator._stop_flag = threading.Event()
    communicator._force_stop_flag = threading.Event()
    communicator._write_queue = queue.Queue(maxsize=10)
    communicator._callback = lambda packet: None
    communicator._availability_callback = None
    communicator._last_available = None
    communicator._connection = connection
    communicator._read_buffer = bytes()
    communicator._timeout = 0.0
    communicator._queue_size = 10
    communicator._retry_max = 0
    communicator._retry_delay = 0.01
    return communicator


def test_thread_survives_transient_read_error() -> None:
    connection = _FlakyConnection()
    communicator = _build_communicator(connection)

    communicator.start()
    time.sleep(0.25)
    communicator.stop()
    communicator.join(timeout=1)

    assert not communicator.is_alive()
    assert connection.read_calls > 1


def test_failed_write_is_retried_and_not_dropped() -> None:
    connection = _FlakyConnection()
    communicator = _build_communicator(connection)
    communicator._write_queue.put(b"rf-packet")

    communicator.start()
    deadline = time.time() + 2
    while not connection.written and time.time() < deadline:
        time.sleep(0.05)

    communicator.stop()
    communicator.join(timeout=1)

    assert connection.write_calls >= 2
    assert connection.written == [b"rf-packet"]


class _AlwaysFailingConnection(_FlakyConnection):
    def write(self, packet: bytes) -> None:
        self.write_calls += 1
        raise OSError("USB stick remains unavailable")


def test_force_stop_terminates_with_pending_packet() -> None:
    connection = _AlwaysFailingConnection()
    communicator = _build_communicator(connection)
    communicator._write_queue.put(b"rf-packet")

    communicator.start()
    time.sleep(0.25)
    communicator._force_stop_flag.set()
    communicator.join(timeout=1)

    assert not communicator.is_alive()
    assert connection.write_calls >= 1
