"""Caido client bootstrap.

The Caido CLI runs as an in-container sidecar listening on
``127.0.0.1:48080`` *inside* the sandbox. We grab a guest token by
``session.exec()``-ing curl from inside the container, then construct
a host-side :class:`caido_sdk_client.Client` against the runtime's
exposed-port URL for all subsequent SDK calls.

Running the auth dance through ``session.exec`` keeps this module
runtime-agnostic — Docker / Daytona / K8s sessions all implement
``exec`` even when their port-exposure semantics differ.
"""

from __future__ import annotations

import asyncio
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


async def bootstrap_caido(
    session: BaseSandboxSession,
    *,
    host_url: str,
    container_url: str,
) -> Client:
    """Connect to the in-container Caido sidecar and select a fresh project.

    Args:
        session: Bound sandbox session — used for ``exec`` to call into
            the in-container Caido API for the guest-login dance.
        host_url: Host-reachable URL for Caido's GraphQL endpoint
            (e.g. ``http://127.0.0.1:{exposed_port}``). Used by the
            host-side :class:`Client` for all post-bootstrap calls.
        container_url: In-container URL for Caido's GraphQL endpoint
            (e.g. ``http://127.0.0.1:48080``). Used by the in-sandbox
            curl for the guest-login dance.

    Returns:
        A connected :class:`caido_sdk_client.Client` with a temporary
        ``"sandbox"`` project selected.
    """
    logger.info("Bootstrapping Caido client (host=%s, container=%s)", host_url, container_url)

    access_token = await _login_as_guest(session, container_url=container_url)

    client = Client(host_url, auth=TokenAuthOptions(token=access_token))
    await client.connect()

    project = await client.project.create(
        CreateProjectOptions(name="sandbox", temporary=True),
    )
    await client.project.select(project.id)
    logger.info("Caido project selected: %s", project.id)
    return client
