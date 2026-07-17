"""Caido client bootstrap.

The Caido CLI runs as an in-container sidecar listening on
``127.0.0.1:48080`` *inside* the sandbox. We grab a guest token by
``session.exec()``-ing curl from inside the container, then construct
a host-side :class:`caido_sdk_client.Client` against the runtime's
exposed-port URL for all subsequent SDK calls.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING

from caido_sdk_client import Client, TokenAuthOptions
from caido_sdk_client.types import CreateProjectOptions


if TYPE_CHECKING:
    from agents.sandbox.session import BaseSandboxSession


logger = logging.getLogger(__name__)


_LOGIN_AS_GUEST_BODY = (
    '{"query":"mutation LoginAsGuest { loginAsGuest { token { accessToken } } }"}'
)


async def _login_as_guest(
    session: BaseSandboxSession,
    *,
    container_url: str,
    attempts: int = 10,
) -> str:
    """``session.exec`` curl to fetch a guest token; retry until ready.

    Caido's GraphQL listener may not be up the instant the container
    starts. The retry loop also doubles as the Caido readiness probe —
    no separate TCP healthcheck needed.
    """
    last_err: str | None = None
    for i in range(1, attempts + 1):
        result = await session.exec(
            "curl",
            "-fsS",
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "-d",
            _LOGIN_AS_GUEST_BODY,
            f"{container_url}/graphql",
            timeout=15,
        )
        if result.ok():
            try:
                payload = json.loads(result.stdout)
                token = (
                    payload.get("data", {})
                    .get("loginAsGuest", {})
                    .get("token", {})
                    .get("accessToken")
                )
                if token:
                    return str(token)
                last_err = f"loginAsGuest returned no token: {payload}"
            except json.JSONDecodeError as exc:
                last_err = f"unparseable response: {exc}: {result.stdout!r}"
        else:
            stderr = result.stderr.decode("utf-8", errors="replace")[:200]
            last_err = f"curl exit {result.exit_code}: {stderr}"
        logger.debug("loginAsGuest attempt %d/%d failed: %s", i, attempts, last_err)
        await asyncio.sleep(min(2.0 * i, 8.0))

    raise RuntimeError(f"loginAsGuest failed after {attempts} attempts: {last_err}")


async def _aclose_quietly(client: Client) -> None:
    """Best-effort close of a client whose setup failed; never raises."""
    with contextlib.suppress(Exception):
        await client.aclose()


async def _connect_client(
    session: BaseSandboxSession,
    *,
    host_url: str,
    container_url: str,
) -> Client:
    access_token = await _login_as_guest(session, container_url=container_url)
    client = Client(host_url, auth=TokenAuthOptions(token=access_token))
    await client.connect()
    return client


async def bootstrap_caido(
    session: BaseSandboxSession,
    *,
    host_url: str,
    container_url: str,
) -> tuple[Client, str]:
    """Connect to the in-container Caido sidecar and select a fresh project.

    Returns the connected client and the id of the temporary project it
    selected. The project id lets :func:`reconnect_caido` rebuild a dead
    transport while staying on the *same* project (and its captured traffic)
    instead of creating a new empty one.
    """
    logger.info("Bootstrapping Caido client (host=%s, container=%s)", host_url, container_url)

    client = await _connect_client(session, host_url=host_url, container_url=container_url)
    try:
        project = await client.project.create(
            CreateProjectOptions(name="sandbox", temporary=True),
        )
        await client.project.select(project.id)
    except BaseException:
        # Don't leak the connected transport if project setup fails.
        await _aclose_quietly(client)
        raise
    logger.info("Caido project selected: %s", project.id)
    return client, str(project.id)


async def reconnect_caido(
    session: BaseSandboxSession,
    *,
    host_url: str,
    container_url: str,
    project_id: str,
) -> Client:
    """Rebuild a Caido client after its transport died, keeping the project.

    Re-authenticates, reconnects, and re-selects the existing project so the
    caller keeps access to the traffic captured before the disconnect.
    """
    logger.info("Reconnecting Caido client (host=%s, project=%s)", host_url, project_id)
    client = await _connect_client(session, host_url=host_url, container_url=container_url)
    try:
        await client.project.select(project_id)
    except BaseException:
        # A missing/unavailable project must not leave the freshly-connected
        # transport dangling — otherwise every retry leaks another one.
        await _aclose_quietly(client)
        raise
    return client
