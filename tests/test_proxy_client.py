"""Tests for the shared Caido client lifecycle and proxy error handling.

Covers the concurrency/reconnect guarantees of ``caido_api.call_with_client``
(the sandbox-imported path) and ``caido_api.SharedCaidoClient`` (the host-side
holder), plus the actionable HTTPQL errors in ``proxy.tools``.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast

import pytest

from strix.tools.proxy import caido_api, tools
from strix.tools.proxy.caido_api import SharedCaidoClient


if TYPE_CHECKING:
    from collections.abc import Iterator


class _FakeClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    caido_api._CLIENT_CACHE.clear()
    yield
    caido_api._CLIENT_CACHE.clear()


async def test_call_with_client_reuses_cached_client(monkeypatch: pytest.MonkeyPatch) -> None:
    cached = _FakeClient("cached")
    caido_api._CLIENT_CACHE["default"] = cast("Any", cached)

    async def _new() -> Any:
        raise AssertionError("_new_client must not run when a client is cached")

    monkeypatch.setattr(caido_api, "_new_client", _new)

    seen: dict[str, Any] = {}

    async def fn(client: Any) -> str:
        seen["client"] = client
        return "ok"

    assert await caido_api.call_with_client(fn) == "ok"
    assert seen["client"] is cached


async def test_call_with_client_creates_and_caches_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _FakeClient("fresh")

    async def _new() -> Any:
        return created

    monkeypatch.setattr(caido_api, "_new_client", _new)

    seen: dict[str, Any] = {}

    async def fn(client: Any) -> str:
        seen["client"] = client
        return "ok"

    assert await caido_api.call_with_client(fn) == "ok"
    assert seen["client"] is created
    assert caido_api._CLIENT_CACHE["default"] is created


async def test_failed_init_does_not_poison_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _new() -> Any:
        raise ConnectionRefusedError("caido not up yet")

    monkeypatch.setattr(caido_api, "_new_client", _new)

    async def fn(_client: Any) -> str:
        return "unreachable"

    with pytest.raises(ConnectionRefusedError):
        await caido_api.call_with_client(fn)
    assert "default" not in caido_api._CLIENT_CACHE


async def test_call_with_client_reconnects_and_closes_dead_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead = _FakeClient("dead")
    fresh = _FakeClient("fresh")
    caido_api._CLIENT_CACHE["default"] = cast("Any", dead)

    new_calls = {"n": 0}

    async def _new() -> Any:
        new_calls["n"] += 1
        return fresh

    monkeypatch.setattr(caido_api, "_new_client", _new)

    attempts: list[Any] = []

    async def fn(client: Any) -> str:
        attempts.append(client)
        if len(attempts) == 1:
            raise RuntimeError("Transport is already connected")
        return "ok"

    assert await caido_api.call_with_client(fn) == "ok"
    assert attempts == [dead, fresh]
    assert new_calls["n"] == 1
    assert caido_api._CLIENT_CACHE["default"] is fresh
    assert dead.closed is True  # stale transport is not leaked


async def test_call_with_client_non_idempotent_rebuilds_but_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead = _FakeClient("dead")
    fresh = _FakeClient("fresh")
    caido_api._CLIENT_CACHE["default"] = cast("Any", dead)

    async def _new() -> Any:
        return fresh

    monkeypatch.setattr(caido_api, "_new_client", _new)

    calls = {"n": 0}

    async def fn(_client: Any) -> str:
        calls["n"] += 1
        raise RuntimeError("Server disconnected")

    # A mutation must not be auto-retried (it may already have applied), but the
    # dead client is still healed so later calls succeed.
    with pytest.raises(RuntimeError, match="Server disconnected"):
        await caido_api.call_with_client(fn, idempotent=False)
    assert calls["n"] == 1
    assert caido_api._CLIENT_CACHE["default"] is fresh
    assert dead.closed is True


async def test_call_with_client_does_not_retry_application_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = _FakeClient("cached")
    caido_api._CLIENT_CACHE["default"] = cast("Any", cached)

    async def _new() -> Any:
        raise AssertionError("deterministic errors must not trigger a reconnect")

    monkeypatch.setattr(caido_api, "_new_client", _new)

    calls = {"n": 0}

    async def fn(_client: Any) -> str:
        calls["n"] += 1
        raise ValueError("Invalid HTTPQL filter")

    with pytest.raises(ValueError, match="Invalid HTTPQL"):
        await caido_api.call_with_client(fn)
    assert calls["n"] == 1
    assert caido_api._CLIENT_CACHE["default"] is cached


async def test_call_with_client_serializes_concurrent_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caido_api._CLIENT_CACHE["default"] = cast("Any", _FakeClient("shared"))

    async def _new() -> Any:
        raise AssertionError("no reconnect expected")

    monkeypatch.setattr(caido_api, "_new_client", _new)

    state = {"active": 0, "max": 0}

    async def fn(_client: Any) -> str:
        state["active"] += 1
        state["max"] = max(state["max"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return "ok"

    await asyncio.gather(*(caido_api.call_with_client(fn) for _ in range(6)))
    assert state["max"] == 1


async def test_shared_client_reconnects_and_closes_dead_transport() -> None:
    dead = _FakeClient("dead")
    fresh = _FakeClient("fresh")

    async def _reconnect() -> Any:
        return fresh

    holder = SharedCaidoClient(cast("Any", dead), _reconnect)

    attempts: list[Any] = []

    async def fn(client: Any) -> str:
        attempts.append(client)
        if len(attempts) == 1:
            raise RuntimeError("Connector is closed")
        return "ok"

    assert await holder.call(fn) == "ok"
    assert attempts == [dead, fresh]
    assert dead.closed is True


async def test_shared_client_non_idempotent_rebuilds_but_reraises() -> None:
    dead = _FakeClient("dead")
    fresh = _FakeClient("fresh")

    async def _reconnect() -> Any:
        return fresh

    holder = SharedCaidoClient(cast("Any", dead), _reconnect)

    calls = {"n": 0}

    async def fn(_client: Any) -> str:
        calls["n"] += 1
        raise RuntimeError("Server disconnected")

    with pytest.raises(RuntimeError, match="Server disconnected"):
        await holder.call(fn, idempotent=False)
    assert calls["n"] == 1
    assert dead.closed is True
    # The healthy client remains for the next call.
    assert await holder.call(lambda _c: _ok()) == "ok"


async def _ok() -> str:
    return "ok"


async def test_shared_client_serializes_concurrent_calls() -> None:
    async def _reconnect() -> Any:
        raise AssertionError("no reconnect expected")

    holder = SharedCaidoClient(cast("Any", _FakeClient("shared")), _reconnect)

    state = {"active": 0, "max": 0}

    async def fn(_client: Any) -> str:
        state["active"] += 1
        state["max"] = max(state["max"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return "ok"

    await asyncio.gather(*(holder.call(fn) for _ in range(6)))
    assert state["max"] == 1


async def test_shared_client_passes_through_application_errors() -> None:
    async def _reconnect() -> Any:
        raise AssertionError("deterministic errors must not trigger a reconnect")

    holder = SharedCaidoClient(cast("Any", _FakeClient("c")), _reconnect)

    async def fn(_client: Any) -> str:
        raise ValueError("Invalid HTTPQL filter")

    with pytest.raises(ValueError, match="Invalid HTTPQL"):
        await holder.call(fn)


def test_is_connection_error_matches_markers_and_causes() -> None:
    assert caido_api._is_connection_error(RuntimeError("Transport is already connected"))
    assert caido_api._is_connection_error(RuntimeError("Connector is closed"))
    assert caido_api._is_connection_error(RuntimeError("Server disconnected"))
    assert not caido_api._is_connection_error(ValueError("Invalid HTTPQL filter"))

    nested = RuntimeError("wrapper")
    nested.__cause__ = RuntimeError("connection reset by peer")
    assert caido_api._is_connection_error(nested)


class _Ctx:
    def __init__(self, context: Any) -> None:
        self.context = context


def test_ctx_proxy_returns_holder_when_present() -> None:
    async def _reconnect() -> Any:
        raise AssertionError("unused")

    holder = SharedCaidoClient(cast("Any", _FakeClient("c")), _reconnect)
    got = tools._ctx_proxy(cast("Any", _Ctx({"caido_client": holder})))
    assert got is holder


def test_ctx_proxy_returns_none_without_holder() -> None:
    assert tools._ctx_proxy(cast("Any", _Ctx({}))) is None
    assert tools._ctx_proxy(cast("Any", _Ctx(None))) is None
    assert tools._ctx_proxy(cast("Any", _Ctx({"caido_client": object()}))) is None


def test_is_httpql_error_detection() -> None:
    assert tools._is_httpql_error(RuntimeError("HTTPQL parse error at column 4"))
    assert tools._is_httpql_error(RuntimeError("failed to parse filter"))
    assert not tools._is_httpql_error(RuntimeError("Transport is already connected"))


def test_httpql_error_preserves_message_and_query() -> None:
    exc = RuntimeError("HTTPQL parse error: unexpected token at column 12")
    payload = json.loads(tools._httpql_error(exc, 'resp.code.eq:"200"'))
    assert payload["success"] is False
    assert "unexpected token at column 12" in payload["error"]
    assert payload["httpql_filter"] == 'resp.code.eq:"200"'
    assert "AND / OR" in payload["hint"]
