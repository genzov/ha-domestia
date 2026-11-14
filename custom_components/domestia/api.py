"""Client for the Domestia controller's binary TCP protocol.

This module must not import Home Assistant, so it can be extracted to a
standalone library.

Frames are ``0xff 0x00 0x00 <len> <cmd...> <checksum>``, where the checksum is
the byte sum of the command. Commands are acknowledged with ``OK``. A get-state
request is answered with ``0xff``, two status bytes and one byte per relay.
"""

import asyncio
import contextlib
import logging

_LOGGER = logging.getLogger(__name__)

CONTROLLER_PORT = 52001

# Highest brightness the controller accepts and reports (the dim scale runs
# 0-64; a relay turned fully on reads back as 64).
MAX_BRIGHTNESS = 64

# Bounds connecting to the controller and writing a request.
REQUEST_TIMEOUT = 3.0

# Bounds the wait for the first reply bytes. The controller answers within
# about 50 ms, or not at all: twice a minute it accepts connections but ignores
# requests for a few seconds. Waiting longer would not bring the reply.
REPLY_TIMEOUT = 1.0

# A request the controller ignored is sent again on a fresh connection, after a
# pause, this many times. Together the attempts span about 5 s, enough to
# outlast the controller's deaf window. Every request is safe to repeat: they
# read state or set an absolute state.
RETRIES = 2
RETRY_DELAY = 1.0

# Bounds the wait for the rest of a reply after its first bytes arrived.
CONTINUATION_TIMEOUT = 0.25

# Length of the get-state reply header: 0xff and two status bytes.
STATE_HEADER_LENGTH = 3

# Length of the "OK" reply to a command.
COMMAND_REPLY_LENGTH = 2

# Large enough for any get-state reply: the header plus one byte per relay, up
# to relay 255, the highest a one-byte command can address.
MAX_REPLY_LENGTH = STATE_HEADER_LENGTH + 255

CMD_GET_STATE = 0x3C
CMD_TURN_ON = 0x0E
CMD_TURN_OFF = 0x0F
CMD_SET_BRIGHTNESS = 0x10


class DomestiaError(Exception):
    """Base error for controller communication."""


class DomestiaConnectionError(DomestiaError):
    """The controller could not be reached or did not answer."""


class DomestiaProtocolError(DomestiaError):
    """The controller sent a reply that does not fit the request."""


class _NoReplyError(DomestiaConnectionError):
    """The controller accepted the request but sent nothing back."""


def pack_command(cmd: bytes) -> bytes:
    """Pack a command into a frame: header, length, command, checksum."""
    return bytes((0xFF, 0x00, 0x00, len(cmd))) + cmd + bytes((sum(cmd) & 0xFF,))


def parse_state(response: bytes) -> dict[int, int]:
    """Parse a get-state reply into raw brightness by relay (1-based)."""
    if len(response) <= STATE_HEADER_LENGTH or response[0] != 0xFF:  # noqa: PLR2004
        msg = f"Unexpected state response: {response.hex(' ')}"
        raise DomestiaProtocolError(msg)

    return dict(enumerate(response[STATE_HEADER_LENGTH:], start=1))


def normalize_brightness(raw: int, *, dimmable: bool) -> int:
    """Normalize a relay's reported brightness.

    A non-dimmable relay reports 1 when it is on; that means full brightness.
    Dimmable relays use 1 as their lowest dim level, so it is left untouched.
    """
    if raw == 1 and not dimmable:
        return MAX_BRIGHTNESS
    return raw


async def read_response(
    reader: asyncio.StreamReader,
    min_length: int,
    first_timeout: float = REPLY_TIMEOUT,
    continuation_timeout: float = CONTINUATION_TIMEOUT,
) -> bytes:
    """Read a reply of at least min_length bytes.

    A single read may return only part of a reply, so this keeps reading until
    the reply is complete or the buffer is full. The first read waits up to
    first_timeout; once bytes have arrived, each further read waits at most
    continuation_timeout. If the controller goes quiet or closes the connection
    after sending some bytes, those bytes are returned so the caller can judge
    them; a reply shorter than expected, for example from a controller with
    fewer relays than configured, then costs at most continuation_timeout.
    """
    response = bytearray()
    timeout = first_timeout

    while len(response) < max(min_length, 1):
        try:
            chunk = await asyncio.wait_for(
                reader.read(MAX_REPLY_LENGTH - len(response)), timeout
            )
        except TimeoutError as err:
            if response:
                break
            raise _NoReplyError("Timed out waiting for a reply") from err

        if not chunk:  # EOF
            break
        response += chunk
        if len(response) == MAX_REPLY_LENGTH:
            break

        # Whatever arrives next is the rest of an incomplete reply; don't wait
        # the full request timeout for it.
        timeout = continuation_timeout

    if not response:
        raise _NoReplyError("Read 0 bytes")

    return bytes(response)


class DomestiaClient:
    """Talks to a Domestia controller, one TCP connection per request."""

    def __init__(self, host: str, port: int = CONTROLLER_PORT) -> None:
        """Create a client for the controller at host."""
        self.host = host
        self.port = port
        # The controller handles one request at a time.
        self._lock = asyncio.Lock()

    async def get_state(self, highest_relay: int) -> dict[int, int]:
        """Return the raw brightness of every relay the controller reports."""
        response = await self._send(
            bytes((CMD_GET_STATE,)), STATE_HEADER_LENGTH + highest_relay
        )
        return parse_state(response)

    async def turn_on(self, relay: int) -> None:
        """Turn a relay on."""
        await self._command(CMD_TURN_ON, relay)

    async def turn_off(self, relay: int) -> None:
        """Turn a relay off."""
        await self._command(CMD_TURN_OFF, relay)

    async def set_brightness(self, relay: int, brightness: int) -> None:
        """Set a relay's brightness, between 0 and MAX_BRIGHTNESS."""
        await self._command(CMD_SET_BRIGHTNESS, relay, brightness)

    async def set_max_brightness(self, relay: int) -> None:
        """Set a relay to full brightness."""
        await self.set_brightness(relay, MAX_BRIGHTNESS)

    async def _command(self, *cmd: int) -> None:
        response = await self._send(bytes(cmd), COMMAND_REPLY_LENGTH)
        if response != b"OK":
            msg = f"Expected OK, received {response!r}"
            raise DomestiaProtocolError(msg)

    async def _send(self, command: bytes, min_length: int) -> bytes:
        """Send a request and return its reply, retrying if none arrives.

        The lock is held across retries, so a retried request is not overtaken
        by requests queued behind it.
        """
        async with self._lock:
            for attempt in range(1, RETRIES + 1):
                try:
                    return await self._exchange(command, min_length)
                except _NoReplyError as err:
                    _LOGGER.debug(
                        "No reply from %s (attempt %s of %s): %s",
                        self.host,
                        attempt,
                        RETRIES + 1,
                        err,
                    )
                await asyncio.sleep(RETRY_DELAY)
            return await self._exchange(command, min_length)

    async def _exchange(self, command: bytes, min_length: int) -> bytes:
        """Send a request on a fresh connection and return the reply."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), REQUEST_TIMEOUT
            )
        except (OSError, TimeoutError) as err:
            msg = f"Cannot connect to {self.host}:{self.port}: {err}"
            raise DomestiaConnectionError(msg) from err

        try:
            writer.write(pack_command(command))
            # TimeoutError is an OSError, so a stuck write is mapped below.
            await asyncio.wait_for(writer.drain(), REQUEST_TIMEOUT)
            return await read_response(reader, min_length, REPLY_TIMEOUT)
        except OSError as err:
            raise DomestiaConnectionError(str(err)) from err
        finally:
            writer.close()
            # Throwaway per-request connection; a close error is not actionable.
            with contextlib.suppress(OSError):
                await writer.wait_closed()
