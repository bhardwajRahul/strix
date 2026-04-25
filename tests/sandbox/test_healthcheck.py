"""Phase 4 tests for the sandbox port readiness probes.

The two helpers (``wait_for_http_ready`` and ``wait_for_tcp_ready``)
gate session bring-up, so a regression here would mean every fresh
scan hits a connection-refused on its first tool call. Tests cover:

- Happy path returns when the probe succeeds.
- Polling continues across transient failures.
- Timeout raises ``SandboxNotReadyError`` with a useful last-error.
- Real ``asyncio.open_connection`` against a local listener verifies
  the TCP probe end-to-end (no mocking — the helper is small enough
  that a real socket is the cheaper test).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from strix.sandbox.healthcheck import (
    SandboxNotReadyError,
    wait_for_http_ready,
    wait_for_tcp_ready,
)


# --- HTTP probe ----------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_http_ready_returns_immediately_on_2xx() -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("strix.sandbox.healthcheck.httpx.AsyncClient", return_value=client):
        await wait_for_http_ready("http://localhost:9999/health", timeout=1)

    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_wait_for_http_ready_polls_through_connect_errors() -> None:
    """Two connect errors followed by a 200 — the helper should keep going."""
    response_ok = MagicMock(spec=httpx.Response)
    response_ok.status_code = 200

    side_effects: list[Any] = [
        httpx.ConnectError("conn refused"),
        httpx.ConnectError("conn refused"),
        response_ok,
    ]

    client = AsyncMock()
    client.get = AsyncMock(side_effect=side_effects)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("strix.sandbox.healthcheck.httpx.AsyncClient", return_value=client):
        await wait_for_http_ready(
            "http://localhost:9999/health",
            timeout=5,
            poll_interval=0.01,
        )
    assert client.get.await_count == 3


@pytest.mark.asyncio
async def test_wait_for_http_ready_raises_after_timeout() -> None:
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("nope"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "strix.sandbox.healthcheck.httpx.AsyncClient",
            return_value=client,
        ),
        pytest.raises(SandboxNotReadyError) as exc_info,
    ):
        await wait_for_http_ready(
            "http://localhost:9999/health",
            timeout=0.3,
            poll_interval=0.05,
        )

    err = str(exc_info.value)
    assert "http://localhost:9999/health" in err
    assert "ConnectError" in err


@pytest.mark.asyncio
async def test_wait_for_http_ready_treats_5xx_as_not_ready() -> None:
    response_500 = MagicMock(spec=httpx.Response)
    response_500.status_code = 500
    response_ok = MagicMock(spec=httpx.Response)
    response_ok.status_code = 200

    client = AsyncMock()
    client.get = AsyncMock(side_effect=[response_500, response_ok])
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("strix.sandbox.healthcheck.httpx.AsyncClient", return_value=client):
        await wait_for_http_ready(
            "http://localhost:9999/health",
            timeout=2,
            poll_interval=0.01,
        )
    assert client.get.await_count == 2


# --- TCP probe -----------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_tcp_ready_against_real_listener() -> None:
    """Spin up a local TCP echo server and verify the probe connects."""

    async def _server_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # Drain any bytes the test sends, then close.
        await reader.read(0)
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()

    server = await asyncio.start_server(_server_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        await wait_for_tcp_ready("127.0.0.1", port, timeout=2, poll_interval=0.05)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_wait_for_tcp_ready_raises_when_port_closed() -> None:
    async def _no_handler(
        _reader: asyncio.StreamReader,
        _writer: asyncio.StreamWriter,
    ) -> None:
        return

    # Bind and immediately close to claim a definitely-unused port number.
    server = await asyncio.start_server(_no_handler, "127.0.0.1", 0)
    closed_port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()

    with pytest.raises(SandboxNotReadyError) as exc_info:
        await wait_for_tcp_ready(
            "127.0.0.1",
            closed_port,
            timeout=0.3,
            poll_interval=0.05,
        )

    err = str(exc_info.value)
    assert f"127.0.0.1:{closed_port}" in err
