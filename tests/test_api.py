"""Tests for the Domestia protocol client."""

import ast
import asyncio
import subprocess
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from custom_components.domestia import api
from custom_components.domestia.api import (
    MAX_BRIGHTNESS,
    MAX_REPLY_LENGTH,
    DomestiaClient,
    DomestiaConnectionError,
    DomestiaProtocolError,
    normalize_brightness,
    pack_command,
    parse_state,
    read_response,
)

API_PATH = Path(api.__file__)


def test_parse_state() -> None:
    """Every relay in the reply is returned with its raw byte, unnormalized."""
    assert parse_state(bytes((0xFF, 0x00, 0x00, 0, 32, 1, 64))) == {
        1: 0,
        2: 32,
        3: 1,
        4: 64,
    }


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"OK", id="OK"),
        pytest.param(bytes((0xFF, 0x00, 0x00)), id="header only"),
        pytest.param(bytes((0x00, 0x00, 0x00, 0x01)), id="wrong first byte"),
    ],
)
def test_parse_state_rejects_malformed(response: bytes) -> None:
    """A reply that is not a get-state reply is an error."""
    with pytest.raises(DomestiaProtocolError):
        parse_state(response)


type Feed = Callable[..., asyncio.StreamReader]


@pytest.fixture
async def feed() -> AsyncGenerator[Feed]:
    """Return a factory for readers fed chunks, pausing between them, then EOF.

    The reader stands in for a controller that splits its reply over TCP
    segments. Pass eof=False for a controller that keeps the connection open.
    """
    feeders: list[asyncio.Task[None]] = []

    def make(*chunks: bytes, eof: bool = True) -> asyncio.StreamReader:
        reader = asyncio.StreamReader()

        async def write() -> None:
            for chunk in chunks:
                reader.feed_data(chunk)
                await asyncio.sleep(0.02)
            if eof:
                reader.feed_eof()

        feeders.append(asyncio.get_running_loop().create_task(write()))
        return reader

    yield make
    await asyncio.gather(*feeders)


@pytest.mark.parametrize(
    ("chunks", "min_length", "expected"),
    [
        pytest.param([b"OK"], 2, b"OK", id="complete reply"),
        pytest.param([b"O", b"K"], 2, b"OK", id="reply split over segments"),
        pytest.param(
            [bytes((0xFF, 0x00)), bytes((0x00, 0, 64)), bytes((64,))],
            6,
            bytes((0xFF, 0x00, 0x00, 0, 64, 64)),
            id="state split over segments",
        ),
        pytest.param(
            [bytes((0xFF, 0x00, 0x00, 1, 2, 3, 4))],
            5,
            bytes((0xFF, 0x00, 0x00, 1, 2, 3, 4)),
            id="reply longer than the minimum",
        ),
        pytest.param(
            [bytes((0xFF, 0x00, 0x00, 1, 2))],
            15,
            bytes((0xFF, 0x00, 0x00, 1, 2)),
            id="controller with fewer relays than configured",
        ),
    ],
)
async def test_read_response(
    feed: Feed, chunks: list[bytes], min_length: int, expected: bytes
) -> None:
    """Reads until the reply is complete, or returns what arrived before EOF."""
    assert await read_response(feed(*chunks), min_length) == expected


async def test_read_response_empty(feed: Feed) -> None:
    """EOF without any bytes is an error."""
    with pytest.raises(DomestiaConnectionError):
        await read_response(feed(), 2)


async def test_read_response_timeout(feed: Feed) -> None:
    """A controller that never answers times out after first_timeout."""
    with pytest.raises(DomestiaConnectionError):
        await read_response(feed(eof=False), 2, first_timeout=0.05)


async def test_read_response_partial_reply_times_out(feed: Feed) -> None:
    """A controller that goes quiet mid-reply returns the bytes so far."""
    reader = feed(bytes((0xFF, 0x00, 0x00, 1, 2)), eof=False)
    assert await read_response(reader, 15, continuation_timeout=0.05) == bytes(
        (0xFF, 0x00, 0x00, 1, 2)
    )


async def test_read_response_waits_for_the_first_byte(feed: Feed) -> None:
    """A min_length of 0 still waits for a reply."""
    assert await read_response(feed(b"OK"), 0) == b"OK"


async def test_read_response_caps_the_buffer(feed: Feed) -> None:
    """A reply never exceeds the buffer, even when more is expected."""
    reader = feed(bytes(200), bytes(100), eof=False)
    response = await read_response(reader, MAX_REPLY_LENGTH + 10)
    assert response == bytes(MAX_REPLY_LENGTH)


@pytest.mark.parametrize(
    ("raw", "dimmable", "expected"),
    [
        pytest.param(1, False, MAX_BRIGHTNESS, id="non-dimmable on normalises 1"),
        pytest.param(0, False, 0, id="non-dimmable off stays 0"),
        pytest.param(1, True, 1, id="dimmable lowest level stays 1"),
        pytest.param(0, True, 0, id="dimmable off stays 0"),
        pytest.param(32, True, 32, id="dimmable mid value is untouched"),
        pytest.param(
            MAX_BRIGHTNESS, True, MAX_BRIGHTNESS, id="dimmable max is untouched"
        ),
    ],
)
def test_normalize_brightness(raw: int, *, dimmable: bool, expected: int) -> None:
    """A non-dimmable relay reporting 1 is on at full brightness."""
    assert normalize_brightness(raw, dimmable=dimmable) == expected


def test_pack_command() -> None:
    """A frame is header, length, command and a wrapping byte-sum checksum."""
    # The checksum wraps: 0x10 + 13 + 0xf8 = 0x115.
    assert pack_command(bytes((0x10, 13, 0xF8))) == bytes(
        (0xFF, 0x00, 0x00, 0x03, 0x10, 13, 0xF8, 0x15)
    )


def test_no_home_assistant_imports() -> None:
    """api.py stays free of Home Assistant so it can become a library."""
    tree = ast.parse(API_PATH.read_text())
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
            assert node.level == 0, "relative imports would load the HA package"
    assert not [m for m in modules if m.split(".")[0] == "homeassistant"]


def test_imports_standalone() -> None:
    """api.py loads on its own without pulling in Home Assistant."""
    script = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('api', {str(API_PATH)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "assert not [m for m in sys.modules if m.startswith('homeassistant')]\n"
    )
    subprocess.run([sys.executable, "-I", "-c", script], check=True)  # noqa: S603  # trusted interpreter and inline script


@dataclass
class FakeController:
    """A TCP server standing in for the controller."""

    port: int
    reply: bytes = b"OK"
    # Requests to leave unanswered before answering again, and how: by keeping
    # the connection open in silence, or by closing it.
    ignore: int = 0
    ignore_by_closing: bool = False
    frames: list[bytes] = field(default_factory=list)
    open_connections: int = 0
    max_open_connections: int = 0

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Record one request frame and answer it."""
        self.open_connections += 1
        self.max_open_connections = max(
            self.max_open_connections, self.open_connections
        )
        header = await reader.readexactly(4)
        body = await reader.readexactly(header[3] + 1)
        self.frames.append(header + body)
        # Linger so overlapping requests would be seen, and leave before
        # replying: the client holds its lock until the reply arrives.
        await asyncio.sleep(0.02)
        self.open_connections -= 1
        if self.ignore:
            self.ignore -= 1
            if not self.ignore_by_closing:
                # Wait in silence until the client gives up and hangs up.
                await reader.read()
            writer.close()
            await writer.wait_closed()
            return
        writer.write(self.reply)
        await writer.drain()
        writer.close()
        await writer.wait_closed()


@pytest.fixture
async def controller(socket_enabled: None) -> AsyncGenerator[FakeController]:
    """Run a fake controller on a free local port."""
    del socket_enabled
    fake = FakeController(port=0)
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    fake.port = server.sockets[0].getsockname()[1]
    async with server:
        yield fake


@pytest.fixture
def client(controller: FakeController) -> DomestiaClient:
    """Return a client for the fake controller."""
    return DomestiaClient("127.0.0.1", controller.port)


@pytest.mark.parametrize(
    ("call", "frame"),
    [
        pytest.param(
            lambda c: c.turn_on(5), bytes((0xFF, 0, 0, 2, 0x0E, 5, 0x13)), id="turn_on"
        ),
        pytest.param(
            lambda c: c.turn_off(5),
            bytes((0xFF, 0, 0, 2, 0x0F, 5, 0x14)),
            id="turn_off",
        ),
        pytest.param(
            lambda c: c.set_brightness(5, 32),
            bytes((0xFF, 0, 0, 3, 0x10, 5, 32, 0x35)),
            id="set_brightness",
        ),
        pytest.param(
            lambda c: c.set_max_brightness(5),
            bytes((0xFF, 0, 0, 3, 0x10, 5, 64, 0x55)),
            id="set_max_brightness",
        ),
    ],
)
async def test_command_frames(
    controller: FakeController,
    client: DomestiaClient,
    call: Callable[[DomestiaClient], Awaitable[None]],
    frame: bytes,
) -> None:
    """Each command sends its frame and accepts an OK reply."""
    await call(client)
    assert controller.frames == [frame]


async def test_get_state(controller: FakeController, client: DomestiaClient) -> None:
    """get_state sends the get-state frame and parses the reply."""
    controller.reply = bytes((0xFF, 0x00, 0x00, 64, 32, 0))
    assert await client.get_state(3) == {1: 64, 2: 32, 3: 0}
    assert controller.frames == [bytes((0xFF, 0, 0, 1, 0x3C, 0x3C))]


async def test_get_state_short_reply(
    controller: FakeController, client: DomestiaClient
) -> None:
    """A controller with fewer relays than requested reports what it has."""
    controller.reply = bytes((0xFF, 0x00, 0x00, 64))
    assert await client.get_state(3) == {1: 64}


async def test_command_rejected(
    controller: FakeController, client: DomestiaClient
) -> None:
    """A reply other than OK is a protocol error."""
    controller.reply = b"NO"
    with pytest.raises(DomestiaProtocolError):
        await client.turn_on(1)
    # A reply, even a wrong one, is not retried.
    assert len(controller.frames) == 1


async def test_get_state_rejected(client: DomestiaClient) -> None:
    """A get-state reply without the state header is a protocol error."""
    with pytest.raises(DomestiaProtocolError):
        await client.get_state(3)


async def test_requests_are_serialized(
    controller: FakeController, client: DomestiaClient
) -> None:
    """Concurrent requests never hold more than one connection open."""
    await asyncio.gather(*(client.turn_on(relay) for relay in range(1, 6)))
    assert len(controller.frames) == 5
    assert controller.max_open_connections == 1


@pytest.mark.usefixtures("socket_enabled")
async def test_connection_refused() -> None:
    """A controller that refuses the connection is a connection error."""
    server = await asyncio.start_server(lambda _r, _w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()

    with pytest.raises(DomestiaConnectionError):
        await DomestiaClient("127.0.0.1", port).turn_on(1)


async def test_connection_reset(client: DomestiaClient) -> None:
    """A socket error during the exchange is a connection error."""
    with (
        patch.object(api, "read_response", side_effect=ConnectionResetError),
        pytest.raises(DomestiaConnectionError),
    ):
        await client.turn_on(1)


@pytest.fixture
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten the reply timeout and the pause between retries."""
    monkeypatch.setattr(api, "REPLY_TIMEOUT", 0.05)
    monkeypatch.setattr(api, "RETRY_DELAY", 0)


@pytest.mark.usefixtures("fast_retries")
@pytest.mark.parametrize("by_closing", [False, True], ids=["silent", "closed"])
async def test_ignored_request_is_retried(
    controller: FakeController, client: DomestiaClient, *, by_closing: bool
) -> None:
    """A request the controller ignores is sent again until it is answered."""
    controller.ignore = api.RETRIES
    controller.ignore_by_closing = by_closing
    await client.turn_on(5)
    assert controller.frames == [bytes((0xFF, 0, 0, 2, 0x0E, 5, 0x13))] * (
        api.RETRIES + 1
    )


@pytest.mark.usefixtures("fast_retries")
async def test_ignored_request_gives_up(
    controller: FakeController, client: DomestiaClient
) -> None:
    """A controller that ignores every attempt is a connection error."""
    controller.ignore = api.RETRIES + 1
    with pytest.raises(DomestiaConnectionError, match="Timed out waiting"):
        await client.get_state(3)
    assert len(controller.frames) == api.RETRIES + 1
