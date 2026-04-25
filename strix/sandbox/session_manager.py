"""Per-scan sandbox session lifecycle.

One session per scan, reused across every agent in that scan's tree.

The bundle returned by :func:`create_or_reuse` is what the per-agent
context dict reads from in ``run_config_factory.make_agent_context`` —
``client``, ``session``, and ``caido_host_port``.

Cache strategy: a module-level dict keyed by ``scan_id``. The same scan
issuing multiple ``create_or_reuse`` calls (e.g., resume after a crash
on the host side) gets the same bundle back. ``cleanup`` is best-effort
— a leaked container is preferable to a stuck cleanup that prevents the
next scan from starting.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import docker
from agents.sandbox.entries import LocalDir
from agents.sandbox.manifest import Environment, Manifest
from agents.sandbox.sandboxes.docker import DockerSandboxClientOptions

from strix.runtime.strix_docker_client import StrixDockerSandboxClient


if TYPE_CHECKING:
    from pathlib import Path


logger = logging.getLogger(__name__)


# In-container Caido sidecar port (matches the image's caido-cli bind).
_CONTAINER_CAIDO_PORT = 48080


# Per-scan session cache. Module-level so a scan that bounces through
# multiple host-side processes (e.g., re-imports the module) doesn't
# spin up a second container — though in practice we expect one
# Strix process per scan.
_SESSION_CACHE: dict[str, dict[str, Any]] = {}


async def create_or_reuse(
    scan_id: str,
    *,
    image: str,
    sources_path: Path,
) -> dict[str, Any]:
    """Return the existing bundle for ``scan_id`` or create a new one.

    Args:
        scan_id: Caller-provided scan identifier (used as cache key).
        image: Docker image tag (e.g. ``"strix-sandbox:0.1.13"``).
        sources_path: Host directory mounted into the container's
            ``/workspace/sources`` so the agent can read user code.

    Returns the bundle dict containing ``client``, ``session``, and
    ``caido_host_port``.
    """
    cached = _SESSION_CACHE.get(scan_id)
    if cached is not None:
        logger.info("Reusing existing sandbox session for scan %s", scan_id)
        return cached

    # Caido runs as an in-container sidecar; HTTP(S) traffic from any
    # process started via ``docker exec`` (the SDK's Shell tool, etc.)
    # picks up these env vars automatically.
    caido_proxy_url = f"http://127.0.0.1:{_CONTAINER_CAIDO_PORT}"
    manifest = Manifest(
        entries={"sources": LocalDir(src=sources_path)},
        environment=Environment(
            value={
                "PYTHONUNBUFFERED": "1",
                "HOST_GATEWAY": "host.docker.internal",
                "http_proxy": caido_proxy_url,
                "https_proxy": caido_proxy_url,
                "ALL_PROXY": caido_proxy_url,
            },
        ),
    )

    client = StrixDockerSandboxClient(docker.from_env())
    options = DockerSandboxClientOptions(
        image=image,
        exposed_ports=(_CONTAINER_CAIDO_PORT,),
    )

    logger.info("Creating sandbox session for scan %s (image=%s)", scan_id, image)
    session = await client.create(options=options, manifest=manifest)

    caido_endpoint = await session._resolve_exposed_port(_CONTAINER_CAIDO_PORT)

    bundle = {
        "client": client,
        "session": session,
        "caido_host_port": caido_endpoint.port,
    }
    _SESSION_CACHE[scan_id] = bundle
    return bundle


async def cleanup(scan_id: str) -> None:
    """Tear down ``scan_id``'s container and drop its cache entry.

    Best-effort: any error during ``client.delete`` is logged and
    swallowed. We never want a cleanup failure to prevent the next
    scan from starting; the worst case is a stranded container that
    Docker's normal reaping will catch on next ``docker prune``.
    """
    bundle = _SESSION_CACHE.pop(scan_id, None)
    if bundle is None:
        logger.debug("cleanup(%s): no cached session", scan_id)
        return

    caido_client = bundle.get("caido_client")
    if caido_client is not None:
        try:
            await caido_client.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("cleanup(%s): caido_client.aclose() raised", scan_id, exc_info=True)

    try:
        await bundle["client"].delete(bundle["session"])
        logger.info("Cleaned up sandbox session for scan %s", scan_id)
    except Exception:
        logger.exception(
            "cleanup(%s): client.delete raised; container may need manual reaping",
            scan_id,
        )


def cached_scan_ids() -> list[str]:
    """Snapshot of currently-cached scan ids. Used by the TUI / CLI."""
    return list(_SESSION_CACHE.keys())


def _reset_cache_for_tests() -> None:
    """Test helper — clears the module cache between unit tests."""
    _SESSION_CACHE.clear()
