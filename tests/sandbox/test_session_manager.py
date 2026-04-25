"""Phase 4 tests for the per-scan sandbox session manager.

We don't spin up real Docker here — the ``StrixDockerSandboxClient`` is
patched and we assert on the manifest / options / bundle shape. Goals:

- Cache hit: a second ``create_or_reuse(scan_id, ...)`` returns the same
  bundle without calling client.create twice.
- Manifest carries the right env vars (TOOL_SERVER_TOKEN, container ports,
  STRIX_SANDBOX_EXECUTION_TIMEOUT, PYTHONUNBUFFERED).
- The Docker client options request both container ports be exposed.
- Capability is configured with the resolved host ports *before* bind,
  so its healthcheck task probes the right ones.
- Bundle is cached and surfaces in ``cached_scan_ids``.
- ``cleanup`` cancels the healthcheck task and calls ``client.delete``;
  errors during delete are swallowed.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strix.sandbox import session_manager
from strix.sandbox.caido_capability import CaidoCapability


@pytest.fixture(autouse=True)
def _isolate_cache() -> Iterator[None]:
    session_manager._reset_cache_for_tests()
    yield
    session_manager._reset_cache_for_tests()


def _noop_bind(_self: Any, _session: Any) -> None:
    """Stand-in for CaidoCapability.bind that skips the healthcheck task."""


def _make_endpoint(port: int) -> Any:
    ep = MagicMock()
    ep.port = port
    ep.host = "127.0.0.1"
    ep.tls = False
    return ep


def _make_client_and_session(
    *,
    tool_port: int = 12001,
    caido_port: int = 12002,
) -> tuple[Any, Any]:
    """Build a fake DockerSandboxClient and session pair."""
    session = MagicMock()
    session._resolve_exposed_port = AsyncMock(
        side_effect=lambda port: _make_endpoint(
            tool_port if port == 48081 else caido_port,
        ),
    )
    client = MagicMock()
    client.create = AsyncMock(return_value=session)
    client.delete = AsyncMock()
    return client, session


@pytest.mark.asyncio
async def test_create_or_reuse_creates_new_session(tmp_path: Any) -> None:
    client, session = _make_client_and_session()
    # Patch the capability's bind to a no-op so we don't spin up the
    # healthcheck task in unit tests.
    with (
        patch(
            "strix.sandbox.session_manager.StrixDockerSandboxClient",
            return_value=client,
        ),
        patch.object(CaidoCapability, "bind", _noop_bind),
    ):
        bundle = await session_manager.create_or_reuse(
            "scan-1",
            image="strix-sandbox:test",
            sources_path=tmp_path,
        )

    # Bundle shape.
    assert bundle["client"] is client
    assert bundle["session"] is session
    assert bundle["tool_server_host_port"] == 12001
    assert bundle["caido_host_port"] == 12002
    assert isinstance(bundle["bearer"], str) and len(bundle["bearer"]) >= 32
    assert isinstance(bundle["capability"], CaidoCapability)
    # Capability got the resolved host ports BEFORE bind would have run.
    assert bundle["capability"]._tool_server_host_port == 12001
    assert bundle["capability"]._caido_host_port == 12002

    # client.create called exactly once with manifest + exposed ports.
    assert client.create.await_count == 1
    options = client.create.await_args.kwargs["options"]
    assert options.image == "strix-sandbox:test"
    assert set(options.exposed_ports) == {48080, 48081}

    manifest = client.create.await_args.kwargs["manifest"]
    env = manifest.environment.value
    assert env["TOOL_SERVER_TOKEN"] == bundle["bearer"]
    assert env["TOOL_SERVER_PORT"] == "48081"
    assert env["CAIDO_PORT"] == "48080"
    assert env["STRIX_SANDBOX_EXECUTION_TIMEOUT"] == "120"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["HOST_GATEWAY"] == "host.docker.internal"


@pytest.mark.asyncio
async def test_create_or_reuse_returns_cached_bundle(tmp_path: Any) -> None:
    client, _ = _make_client_and_session()
    with (
        patch(
            "strix.sandbox.session_manager.StrixDockerSandboxClient",
            return_value=client,
        ),
        patch.object(CaidoCapability, "bind", _noop_bind),
    ):
        first = await session_manager.create_or_reuse(
            "scan-X",
            image="i",
            sources_path=tmp_path,
        )
        second = await session_manager.create_or_reuse(
            "scan-X",
            image="i",
            sources_path=tmp_path,
        )

    assert first is second
    assert client.create.await_count == 1
    assert "scan-X" in session_manager.cached_scan_ids()


@pytest.mark.asyncio
async def test_create_or_reuse_passes_custom_execution_timeout(tmp_path: Any) -> None:
    client, _ = _make_client_and_session()
    with (
        patch(
            "strix.sandbox.session_manager.StrixDockerSandboxClient",
            return_value=client,
        ),
        patch.object(CaidoCapability, "bind", _noop_bind),
    ):
        await session_manager.create_or_reuse(
            "scan-2",
            image="i",
            sources_path=tmp_path,
            execution_timeout=300,
        )

    manifest = client.create.await_args.kwargs["manifest"]
    assert manifest.environment.value["STRIX_SANDBOX_EXECUTION_TIMEOUT"] == "300"


@pytest.mark.asyncio
async def test_cleanup_calls_delete_and_drops_cache(tmp_path: Any) -> None:
    client, session = _make_client_and_session()
    with (
        patch(
            "strix.sandbox.session_manager.StrixDockerSandboxClient",
            return_value=client,
        ),
        patch.object(CaidoCapability, "bind", _noop_bind),
    ):
        await session_manager.create_or_reuse(
            "scan-3",
            image="i",
            sources_path=tmp_path,
        )
        assert "scan-3" in session_manager.cached_scan_ids()
        await session_manager.cleanup("scan-3")

    client.delete.assert_awaited_once_with(session)
    assert "scan-3" not in session_manager.cached_scan_ids()


@pytest.mark.asyncio
async def test_cleanup_swallows_delete_errors(tmp_path: Any) -> None:
    """A flaky Docker daemon shouldn't prevent cache eviction."""
    client, _ = _make_client_and_session()
    client.delete = AsyncMock(side_effect=RuntimeError("docker daemon went away"))
    with (
        patch(
            "strix.sandbox.session_manager.StrixDockerSandboxClient",
            return_value=client,
        ),
        patch.object(CaidoCapability, "bind", _noop_bind),
    ):
        await session_manager.create_or_reuse(
            "scan-4",
            image="i",
            sources_path=tmp_path,
        )
        await session_manager.cleanup("scan-4")  # must not raise

    assert "scan-4" not in session_manager.cached_scan_ids()


@pytest.mark.asyncio
async def test_cleanup_unknown_scan_is_noop() -> None:
    """No cached entry → cleanup is a quiet no-op."""
    await session_manager.cleanup("never-existed")  # must not raise
