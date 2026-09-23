"""Tests for non-blocking Becker RF queue writes."""

import asyncio
import threading
from unittest.mock import MagicMock

from custom_components.becker import _packet_callback
from custom_components.becker.pybecker.becker import Becker
from custom_components.becker.pybecker.becker_helper import finalize_code


async def test_write_offloads_blocking_communicator_send() -> None:
    """A blocking communicator.send must not stall the asyncio event loop."""
    becker = Becker.__new__(Becker)
    becker.communicator = MagicMock()

    loop = asyncio.get_running_loop()
    send_started = asyncio.Event()
    release_send = threading.Event()

    def blocking_send(packet: bytes) -> None:
        loop.call_soon_threadsafe(send_started.set)
        release_send.wait(timeout=1)

    becker.communicator.send.side_effect = blocking_send

    task = asyncio.create_task(becker.write(["0000000002010B000000000001737b02101010020FE"]))

    await asyncio.wait_for(send_started.wait(), timeout=0.5)
    assert not task.done()

    # Reaching this point while send() is still blocked proves the event loop
    # remained responsive instead of running communicator.send synchronously.
    await asyncio.sleep(0)

    release_send.set()
    await asyncio.wait_for(task, timeout=0.5)

    becker.communicator.send.assert_called_once_with(
        finalize_code("0000000002010B000000000001737b02101010020FE")
    )


async def test_write_preserves_packet_order() -> None:
    """Multiple packets are still handed to the communicator sequentially."""
    becker = Becker.__new__(Becker)
    becker.communicator = MagicMock()

    codes = [
        "0000000002010B000000000001737b02101010020FE",
        "0000000002010B000100000001737b02101010020FD",
        "0000000002010B000200000001737b02101010020FC",
    ]

    await becker.write(codes)

    assert [call.args[0] for call in becker.communicator.send.call_args_list] == [
        finalize_code(code) for code in codes
    ]



def test_packet_callback_marshals_to_home_assistant_event_loop() -> None:
    """RF receive callback never touches Home Assistant directly from its thread."""
    hass = MagicMock()
    packet = MagicMock()

    _packet_callback(hass, "entry-1", packet)

    hass.loop.call_soon_threadsafe.assert_called_once()
    callback, callback_hass, entry_id, callback_packet = (
        hass.loop.call_soon_threadsafe.call_args.args
    )
    assert callback_hass is hass
    assert entry_id == "entry-1"
    assert callback_packet is packet
    # The actual HA dispatcher/event-bus work is deferred to the loop callback.
    assert callback.__name__ == "_dispatch_packet_on_loop"
