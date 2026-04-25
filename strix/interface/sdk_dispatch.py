"""STRIX_USE_SDK_HARNESS dispatch — selects legacy vs SDK harness at run-time.

Phase 5b cutover gate. The legacy CLI (``strix.interface.cli``) calls
``StrixAgent(...).execute_scan(scan_config)`` directly. To roll out the
SDK migration safely we want a single env-var-gated branch:

    STRIX_USE_SDK_HARNESS=1  →  await run_strix_scan(...)
    STRIX_USE_SDK_HARNESS=0  →  await StrixAgent(...).execute_scan(...)

This module is a thin adapter: it reads the env var, and when the SDK
path is active, translates the legacy ``scan_config`` + ``args`` pair
into the keyword arguments :func:`run_strix_scan` expects.

Per PLAYBOOK §7.1: the legacy default stays in place until end-to-end
validation against a stable target succeeds; the env flag is the
opt-in. Removal of the legacy branch happens one release after cutover.

References:
    - PLAYBOOK.md §7.1 (cutover strategy)
    - PLAYBOOK.md §7.2 (rollback procedure)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from agents.result import RunResult


logger = logging.getLogger(__name__)


_ENV_FLAG = "STRIX_USE_SDK_HARNESS"


def should_use_sdk_harness() -> bool:
    """Return True iff ``STRIX_USE_SDK_HARNESS`` is truthy in the env.

    Truthy values: ``"1"``, ``"true"``, ``"yes"`` (case-insensitive).
    Anything else — including unset — returns False so the default
    deployed posture stays the legacy harness.
    """
    raw = os.environ.get(_ENV_FLAG, "")
    return raw.strip().lower() in {"1", "true", "yes"}


def _resolve_sandbox_image() -> str:
    """Read the sandbox image tag from Strix config.

    Falls back to ``"strix-sandbox:latest"`` if unset — same behavior
    the legacy ``DockerRuntime`` would surface as a config error.
    """
    from strix.config import Config

    image = Config.get("strix_image")
    if not image:
        logger.warning(
            "strix_image not configured; falling back to strix-sandbox:latest. "
            "Set this in ~/.strix/cli-config.json for production use.",
        )
        return "strix-sandbox:latest"
    return str(image)


def _resolve_sources_path(args: Any) -> Path:
    """Pick the host directory to mount into ``/workspace/sources``.

    - When ``--local-sources`` was passed, use the parent of the first
      source's ``host_path`` (the legacy harness then copies each
      individual source under ``/workspace/<subdir>``; we mount the
      parent and let the agent walk down).
    - Otherwise, use a per-run scratch directory under
      ``$XDG_CACHE_HOME/strix`` (or ``~/.cache/strix``) — the legacy
      flow eventually populates ``/workspace`` via post-create copies,
      which the SDK session manager doesn't replicate yet (Phase 6
      will bring that in).
    """
    local_sources: list[dict[str, str]] | None = getattr(args, "local_sources", None)
    if local_sources:
        first = local_sources[0]
        host_path = first.get("host_path") or first.get("source_path") or first.get("path")
        if host_path:
            return Path(host_path).expanduser().resolve().parent

    cache_root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    run_name = getattr(args, "run_name", "default") or "default"
    sources = Path(cache_root) / "strix" / "sources" / str(run_name)
    sources.mkdir(parents=True, exist_ok=True)
    return sources


async def run_scan_via_sdk(
    *,
    scan_config: dict[str, Any],
    args: Any,
    tracer: Any,
) -> RunResult:
    """Translate legacy CLI args into ``run_strix_scan`` kwargs.

    Args:
        scan_config: The same dict the legacy ``StrixAgent.execute_scan``
            accepts. Forwarded verbatim to ``run_strix_scan``; the
            entry point reads ``targets``, ``user_instructions``,
            ``diff_scope``, ``scan_mode``, ``is_whitebox``, ``skills``
            from it.
        args: argparse Namespace from ``strix.interface.cli``. We read
            ``run_name``, ``local_sources``, ``scan_mode`` from it.
        tracer: Live ``Tracer`` instance — flows through context so
            tools (``create_vulnerability_report``, ``finish_scan``)
            persist into the same on-disk run directory the legacy
            path uses.

    Returns the SDK ``RunResult``. Raises whatever ``run_strix_scan``
    raises (sandbox bring-up failure, LLM error, etc.).
    """
    from strix.sdk_entry import run_strix_scan

    run_name = getattr(args, "run_name", None) or scan_config.get("run_name")
    image = _resolve_sandbox_image()
    sources_path = _resolve_sources_path(args)
    interactive = bool(getattr(args, "interactive", False))

    logger.info(
        "STRIX_USE_SDK_HARNESS active; dispatching scan %s via run_strix_scan "
        "(image=%s, sources=%s)",
        run_name,
        image,
        sources_path,
    )

    return await run_strix_scan(
        scan_config=scan_config,
        scan_id=run_name,
        image=image,
        sources_path=sources_path,
        tracer=tracer,
        interactive=interactive,
    )
