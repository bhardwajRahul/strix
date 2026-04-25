"""Phase 4 tests for CaidoCapability.

The capability has three observable behaviors that need parity with the
PLAYBOOK contract:

1. ``process_manifest`` injects http_proxy / https_proxy / ALL_PROXY env
   vars into the manifest's ``Environment.value`` dict.
2. ``tools()`` returns the seven Caido SDK function tools we wrapped in
   Phase 2.5 — same instances, in the documented order.
3. ``bind`` schedules an aggregated healthcheck task; the orchestration
   hook later awaits it on first agent start.

The healthcheck task itself is exercised by the healthcheck unit tests;
here we only verify the wiring (task created, name set, points at the
right ports).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from agents.sandbox.entries import LocalDir
from agents.sandbox.manifest import Environment, Manifest

from strix.sandbox.caido_capability import CaidoCapability


def test_capability_type_and_default_state() -> None:
    cap = CaidoCapability()
    assert cap.type == "caido"
    assert cap._healthcheck_task is None
    assert cap._tool_server_host_port is None
    assert cap._caido_host_port is None


def test_process_manifest_injects_proxy_env_vars(tmp_path: object) -> None:
    """Existing env vars must be preserved; proxy keys are added."""
    cap = CaidoCapability()
    manifest = Manifest(
        environment=Environment(
            value={"PYTHONUNBUFFERED": "1", "TOOL_SERVER_TOKEN": "abc"},
        ),
    )
    out = cap.process_manifest(manifest)
    env = out.environment.value
    # Pre-existing entries preserved.
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["TOOL_SERVER_TOKEN"] == "abc"
    # Proxy entries injected, all pointing at the in-container Caido port.
    assert env["http_proxy"] == "http://127.0.0.1:48080"
    assert env["https_proxy"] == "http://127.0.0.1:48080"
    assert env["ALL_PROXY"] == "http://127.0.0.1:48080"


def test_process_manifest_handles_missing_environment() -> None:
    """A manifest without env entries should still get the proxy block."""
    cap = CaidoCapability()
    # ``LocalDir`` requires a real path on disk; use a temp one to satisfy
    # the validator without actually mounting anything.
    manifest = Manifest(entries={"src": LocalDir(src="/tmp")})
    out = cap.process_manifest(manifest)
    env = out.environment.value
    assert env["http_proxy"] == "http://127.0.0.1:48080"


def test_tools_returns_seven_caido_tools_in_order() -> None:
    cap = CaidoCapability()
    names = [t.name for t in cap.tools()]
    assert names == [
        "list_requests",
        "view_request",
        "send_request",
        "repeat_request",
        "scope_rules",
        "list_sitemap",
        "view_sitemap_entry",
    ]


def test_tools_returns_a_fresh_list_per_call() -> None:
    """SDK convention — caller may mutate the returned list."""
    cap = CaidoCapability()
    a = cap.tools()
    b = cap.tools()
    assert a == b
    assert a is not b


@pytest.mark.asyncio
async def test_instructions_mentions_caido_and_tools() -> None:
    cap = CaidoCapability()
    out = await cap.instructions(Manifest())
    assert out is not None
    assert "<caido_proxy>" in out
    # Every tool name appears verbatim so the model knows what's available.
    for name in (
        "list_requests",
        "view_request",
        "send_request",
        "repeat_request",
        "scope_rules",
        "list_sitemap",
        "view_sitemap_entry",
    ):
        assert name in out


def test_configure_host_ports_stores_both() -> None:
    cap = CaidoCapability()
    cap.configure_host_ports(tool_server_host_port=12345, caido_host_port=12346)
    assert cap._tool_server_host_port == 12345
    assert cap._caido_host_port == 12346


@pytest.mark.asyncio
async def test_bind_without_configured_ports_skips_healthcheck() -> None:
    """If the session manager forgets to configure ports, bind shouldn't
    schedule a probe against ``None`` — it should warn and no-op.
    """
    cap = CaidoCapability()
    fake_session = MagicMock()
    cap.bind(fake_session)
    assert cap._healthcheck_task is None
    assert cap.session is fake_session


@pytest.mark.asyncio
async def test_bind_schedules_healthcheck_task_when_ports_configured() -> None:
    """The hook chain (StrixOrchestrationHooks.on_agent_start) awaits this
    task — it must exist as an asyncio.Task with a useful name.
    """
    cap = CaidoCapability()
    cap.configure_host_ports(tool_server_host_port=54321, caido_host_port=54322)
    fake_session = MagicMock()

    # Patch the actual probes so we don't try to connect for real.
    async def _fake_probe(*args: object, **kwargs: object) -> None:
        return None

    with (
        patch(
            "strix.sandbox.caido_capability.wait_for_http_ready",
            side_effect=_fake_probe,
        ),
        patch(
            "strix.sandbox.caido_capability.wait_for_tcp_ready",
            side_effect=_fake_probe,
        ),
    ):
        cap.bind(fake_session)
        assert cap._healthcheck_task is not None
        assert isinstance(cap._healthcheck_task, asyncio.Task)
        assert "caido-healthcheck-54321" in cap._healthcheck_task.get_name()
        await cap._healthcheck_task  # must complete without error
