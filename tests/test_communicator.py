"""Tests for Becker communicator queue behavior."""

import queue
import threading
from types import SimpleNamespace

import pytest

from custom_components.becker.pybecker.becker_helper import (
    BeckerCommunicator,
    BeckerConnectionError,
)


def _communicator_with_queue(maxsize: int, retries: int = 0) -> BeckerCommunicator:
    communicator = BeckerCommunicator.__new__(BeckerCommunicator)
    threading.Thread.__init__(communicator, daemon=True)
    communicator._queue_size = maxsize
    communicator._retry_max = retries
    communicator._retry_delay = 0.01
    communicator._write_queue = queue.Queue(maxsize=maxsize)
    communicator._stop_flag = threading.Event()
    communicator.is_alive = lambda: True
    return communicator


def test_full_queue_raises_without_stopping_communicator() -> None:
    communicator = _communicator_with_queue(1)
    communicator._write_queue.put(b"existing")

    with pytest.raises(BeckerConnectionError):
        communicator.send(b"new")

    assert not communicator._stop_flag.is_set()
    assert communicator._write_queue.qsize() == 1


def test_command_is_queued_when_capacity_is_available() -> None:
    communicator = _communicator_with_queue(2)
    communicator.send(b"packet")

    assert communicator._write_queue.get_nowait() == b"packet"


def test_diagnostics_report_runtime_status_without_device_address() -> None:
    communicator = _communicator_with_queue(10, retries=2)
    communicator._retry_delay = 0.25
    communicator._write_queue.put(b"packet")
    communicator._connection = SimpleNamespace(
        is_open=True,
        is_serial=False,
        device="socket://192.168.1.10:5000",
    )

    diagnostics = communicator.diagnostics()

    assert diagnostics == {
        "thread_alive": True,
        "connection_open": True,
        "transport": "network",
        "queue_depth": 1,
        "queue_capacity": 10,
        "queue_percent": 10.0,
        "retry_max": 2,
        "retry_delay": 0.25,
        "stopping": False,
    }
    assert "192.168.1.10" not in repr(diagnostics)
