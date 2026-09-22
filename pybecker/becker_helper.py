"""Helper for Becker centronic USB Stick."""
import logging
import re
import time
import threading
import queue
from typing import Any, Callable, Tuple
import os
import sys
import serial
import serial.tools.list_ports

STX = b'\x02'
ETX = b'\x03'

CODE_PREFIX = "0000000002010B"  # 0-23 (24 chars)
CODE_SUFFIX = "000000"
CODE_21 = "021"
CODE_REMOTE = "01"  # centronic remote control used "02" while contralControl seem to use "01"

DEFAULT_DEVICE_NAME = '/dev/serial/by-id/usb-BECKER-ANTRIEBE_GmbH_CDC_RS232_v125_Centronic-if00'

MESSAGE = re.compile(
      STX
    + CODE_PREFIX.encode()
    + rb'[0-9A-F]{4,4}'
    + CODE_SUFFIX.encode()
    + rb'(?P<unit_id>[0-9A-F]{5,5})'
    + rb'[0-9A-F]{6,6}'
    + rb'(?P<channel>[0-9A-F]{1,1})'
    + rb'00'
    + rb'(?P<command>[0-9A-F]{1,1})'
    + rb'(?P<argument>[0-9A-F]{1,1})'
    + rb'[0-9A-F]{2,2}'
    + ETX, re.I
)

COMMANDS = {b'0': 'RELEASE', b'1': 'HALT', b'2': 'UP', b'4': 'DOWN', b'8': 'TRAIN'}
COMMUNICATION_TIMEOUT = 0.3

_LOGGER = logging.getLogger(__name__)


def hex2(n):
    """Return word"""
    return '%02X' % (n & 0xFF)


def hex4(n):
    """Return dword"""
    return '%04X' % (n & 0xFFFF)


def checksum(code):
    """Calculate checksum"""
    code_length = len(code)
    if code_length != 40:
        _LOGGER.error("The code must be 40 characters long (without <STX>, <ETX> and checksum)")
        return
    code_sum = 0
    i = 0
    while i < code_length:
        hex_code = code[i] + code[i + 1]
        code_sum += int(hex_code, 16)
        i += 2
    return '%s%s' % (code.upper(), hex2(0x03 - code_sum))


def generate_code(channel, unit, cmd_code, with_checksum=True):
    """Generate code"""
    unit_id = unit[0]  # contains the unit code in hex (5 chars)
    unit_inc = unit[1]  # contains the next increment (required to convert into hex4)

    if channel == 0:
        # channel 0 may be used for wall mounted sender (primary used as master sender)
        code = CODE_PREFIX + hex4(unit_inc) + CODE_SUFFIX + unit_id + CODE_21 + "00" + hex2(channel) + '00' + hex2(
            cmd_code)
    else:
        code = CODE_PREFIX + hex4(unit_inc) + CODE_SUFFIX + unit_id + CODE_21 + CODE_REMOTE + hex2(channel) + '00' \
               + hex2(cmd_code)
    return checksum(code) if with_checksum else code

def finalize_code(code):
    """Add frame"""
    return b"".join([STX, code.encode(), ETX])


class BeckerConnectionError(Exception):
    """Error class for Becker centronic USB Stick."""
    pass


class BeckerConnection():
    """
    Connection class for Becker centronic USB Stick.
    """
    # Minimum seconds between full teardown/rebuild attempts of the
    # underlying pyserial object once it gets stuck failing to open.
    RECONNECT_REBUILD_INTERVAL = 10

    def __init__(self, device: str, strict: bool = False) -> None:
        """Initialize connection.

        strict=True is intended for setup validation and raises immediately
        when the device cannot be opened. Runtime connections keep the
        resilient reconnect behavior.
        """
        self._device, self._is_serial = self._validate_device(device)
        self._connection = self._build_serial()
        self._last_rebuild_attempt = 0.0
        self._open(strict=strict)

    def _build_serial(self):
        """Create a fresh pyserial Serial object for the configured device."""
        try:
            return serial.serial_for_url(
                self.device,
                baudrate=115200,
                timeout=0,
                do_not_open=True
            )
        except serial.SerialException as err:
            raise BeckerConnectionError(
                "Error when trying to establish connection using {}.".format(self.device)
            ) from err

    @property
    def is_serial(self) -> bool:
        """Return if device is serial port."""
        return self._is_serial

    @property
    def device(self) -> str:
        """Return device name."""
        return self._device

    @property
    def is_open(self) -> bool:
        """Return whether the underlying transport is currently open."""
        return bool(self._connection.is_open)

    def write(self, packet: bytes) -> None:
        """Write data."""
        self._open()
        try:
            self._connection.write(packet)
        except Exception:
            # Re-connect on error (covers SerialException and raw OSError on unplug)
            _LOGGER.debug("Write failed. Try to close and re-open connection to %s", self.device)
            try:
                self._connection.close()
            except Exception:
                pass
            self._open()
            self._connection.write(packet)

    def read(self) -> bytes:
        """Read data."""
        packet = bytes()
        self._open()
        try:
            packet = self._connection.read(1024)
        except Exception:
            # Re-connect on error (covers SerialException and raw OSError on unplug)
            _LOGGER.debug("Read failed. Try to close and re-open connection to %s", self.device)
            try:
                self._connection.close()
            except Exception:
                pass
        return packet

    def _open(self, strict: bool = False) -> None:
        """Open the connection, optionally failing fast for setup validation."""
        if self._connection.is_open:
            return

        _LOGGER.debug("Try to open connection.")
        try:
            self._connection.open()
        except Exception as err:  # pylint: disable=broad-except
            if strict:
                raise BeckerConnectionError(
                    f"Could not open Becker connection to {self.device}: {err}"
                ) from err

            if self.is_serial:
                _LOGGER.warning(
                    "Establish connection to %s failed, will retry: %s",
                    self.device,
                    err,
                )
                self._maybe_rebuild()
            else:
                _LOGGER.warning(
                    "Establish connection to %s failed, will retry: %s",
                    self.device,
                    err,
                )

    def _maybe_rebuild(self) -> None:
        """Periodically tear down and recreate the underlying pyserial object.

        A stale pyserial Serial instance can end up wedged after the
        OS-level device path disappears and reappears (e.g. the USB stick
        was power-cycled). Simply retrying .open() on the same object can
        fail to recover even once the device is back, because the object
        may be holding onto stale internal file-descriptor state. Rebuilding
        it from scratch periodically works around this without requiring a
        full Home Assistant restart.
        """
        now = time.time()
        if now - self._last_rebuild_attempt < self.RECONNECT_REBUILD_INTERVAL:
            return
        self._last_rebuild_attempt = now
        try:
            self._connection.close()
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            new_connection = self._build_serial()
        except BeckerConnectionError as err:
            _LOGGER.debug("Rebuild of serial connection to %s failed: %s", self.device, err)
            return
        self._connection = new_connection
        _LOGGER.info("Rebuilt serial connection object for %s after repeated failures.", self.device)

    def close(self) -> None:
        """Close connection"""
        if self._connection.is_open:
            self._connection.close()

    @staticmethod
    def _validate_device(device: str) -> Tuple[str, bool]:
        """Validate device name."""
        is_serial = False
        is_socket = True
        if device is None:
            device = DEFAULT_DEVICE_NAME
        if "/dev/" in device:
            if not os.path.exists(device):
                raise BeckerConnectionError("{} is not existing".format(device))
            is_serial = True
            is_socket = False
        elif sys.platform.startswith('win') and 'COM' in device.upper():
            if not device.upper() in [i.device for i in serial.tools.list_ports.comports()]:
                raise BeckerConnectionError("{} is not existing".format(device))
            is_serial = True
            is_socket = False
        elif "/" in device:
            is_socket = False
        if is_socket:
            if ':' in device:
                host, port = device.split(':', 1)
            else:
                host = device
                port = '5000'
            device = f'socket://{host}:{port}'
        return device, is_serial


class BeckerCommunicator(threading.Thread):
    """
    Communicator class for Becker centronic USB Stick.
    """
    def __init__(
        self,
        device: str,
        callback: Callable[[re.Match], Any] = None,
        deamon: bool = True,
        queue_size: int = 100,
        retry_max: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        '''Initialize communicator.'''
        super().__init__(daemon=deamon)
        self._queue_size = queue_size
        self._retry_max = retry_max
        self._retry_delay = retry_delay
        # Setup threading stop event and queue
        self._stop_flag = threading.Event()
        self._force_stop_flag = threading.Event()
        self._write_queue = queue.Queue(maxsize=queue_size)
        # Setup callback
        self._callback = callback
        # Setup interface
        self._connection = BeckerConnection(device=device)
        self._read_buffer = bytes()
        # timeout will be used within thread only
        self._timeout = time.time()

    def run(self) -> None:
        '''Run BeckerCommunicator thread.'''
        _LOGGER.debug('BeckerCommunicator thread started.')
        callback_valid = False if self._callback is None else True    # pylint: disable=simplifiable-if-expression
        pending_packet = None
        while True:
            # Read bytes from serial port
            if callback_valid:
                try:
                    data = self._connection.read()
                except Exception as err:   # pylint: disable=broad-except
                    _LOGGER.warning(
                        "BeckerCommunicator read failed (%s). Will keep retrying without killing the thread.", err
                    )
                    data = bytes()
                if len(data) > 0:
                    self._timeout = time.time() + COMMUNICATION_TIMEOUT
                self._read_buffer += data
                self._parse()

            # Send one pending packet at a time once the protocol timeout has
            # expired. Keep a failed packet pending so a transient USB/network
            # failure cannot silently drop a rolling-code command.
            if self._timeout < time.time():
                if pending_packet is None:
                    try:
                        pending_packet = self._write_queue.get(block=False)
                    except queue.Empty:
                        pass

                if pending_packet is not None:
                    try:
                        self._connection.write(pending_packet)
                    except Exception as err:   # pylint: disable=broad-except
                        _LOGGER.warning(
                            "BeckerCommunicator failed to send packet (%s). "
                            "Keeping it pending for retry after reconnect.",
                            err,
                        )
                    else:
                        self._timeout = time.time() + COMMUNICATION_TIMEOUT
                        self._log(pending_packet, "Sent packet: ")
                        pending_packet = None

            # Sleep for thread switch and wait time between packets
            time.sleep(0.1)
            if self._force_stop_flag.is_set():
                _LOGGER.warning(
                    "Force-stopping BeckerCommunicator with pending RF commands"
                )
                break
            # Ensure all queued and pending packets are sent before stopping.
            if (
                self._stop_flag.is_set()
                and self._write_queue.empty()
                and pending_packet is None
            ):
                break
        _LOGGER.debug('BeckerCommunicator thread stopped.')

    def stop(self) -> None:
        '''Stop BeckerCommunicator thread.'''
        self._stop_flag.set()

    def _parse(self) -> None:
        """Parse received packets and run callback."""
        end = 0
        for data in MESSAGE.finditer(self._read_buffer):
            self._log(data.group(0), "Received packet: ")
            self._callback(data)
            end = data.end()
        self._read_buffer = self._read_buffer[end:]

    def _log(self, packet: bytes, text: str = "") -> None:
        """Log packets."""
        if _LOGGER.getEffectiveLevel() <= logging.DEBUG:
            match = MESSAGE.search(packet)
            if match is not None:
                if match.group('command') in COMMANDS:
                    command = COMMANDS[match.group('command')]
                else:
                    command = match.group('command').decode()
                _LOGGER.debug(
                    "%sunit_id: %s, channel: %s, command: %s, argument: %s, packet: %s",
                    text,
                    match.group('unit_id').decode(),
                    match.group('channel').decode(),
                    command,
                    match.group('argument').decode(),
                    match.group(0),
                )

    def diagnostics(self) -> dict[str, Any]:
        """Return privacy-safe runtime diagnostics."""
        queue_depth = self._write_queue.qsize()
        queue_capacity = self._queue_size
        queue_percent = (
            round(queue_depth / queue_capacity * 100, 1)
            if queue_capacity
            else 0.0
        )
        return {
            "thread_alive": self.is_alive(),
            "connection_open": self._connection.is_open,
            "transport": "serial" if self._connection.is_serial else "network",
            "queue_depth": queue_depth,
            "queue_capacity": queue_capacity,
            "queue_percent": queue_percent,
            "retry_max": self._retry_max,
            "retry_delay": self._retry_delay,
            "stopping": self._stop_flag.is_set(),
        }

    def send(self, packet) -> None:
        """Queue a packet, retrying temporary queue saturation."""
        if not self.is_alive():
            raise BeckerConnectionError(
                "Error BeckerCommunicator thread not alive."
            )

        queue_size = self._write_queue.qsize()
        queue_percent = queue_size / self._queue_size * 100
        if queue_percent >= 80:
            _LOGGER.warning(
                "RF command queue is %.0f%% full (%d/%d)",
                queue_percent,
                queue_size,
                self._queue_size,
            )
        elif queue_percent >= 50:
            _LOGGER.info(
                "RF command queue is %.0f%% full (%d/%d)",
                queue_percent,
                queue_size,
                self._queue_size,
            )

        attempts = self._retry_max + 1
        for attempt in range(attempts):
            try:
                self._write_queue.put(packet, timeout=5)
                if attempt:
                    _LOGGER.info(
                        "RF command queued after %d retry attempt(s)", attempt
                    )
                return
            except queue.Full as err:
                if attempt >= self._retry_max:
                    raise BeckerConnectionError(
                        "RF command queue is full after "
                        f"{self._retry_max} retry attempt(s) "
                        f"({self._queue_size} queued commands)."
                    ) from err
                _LOGGER.warning(
                    "RF command queue full; retrying in %.1fs "
                    "(attempt %d/%d)",
                    self._retry_delay,
                    attempt + 1,
                    self._retry_max,
                )
                time.sleep(self._retry_delay)

    def close(self) -> None:
        """Stop the thread and close the device.

        Give queued commands a short opportunity to drain. If the connection
        is still unavailable, force the daemon thread to exit rather than
        leaving it alive after the integration has been unloaded.
        """
        self.stop()
        self.join(timeout=5)
        if self.is_alive():
            _LOGGER.warning(
                "BeckerCommunicator did not drain within 5 seconds; "
                "forcing shutdown with %d queued command(s)",
                self._write_queue.qsize(),
            )
            self._force_stop_flag.set()
            self.join(timeout=1)
        self._connection.close()
