"""Message delivery bridge from TUI input to SDK-backed agents."""

from __future__ import annotations

import asyncio
from typing import Any


def send_user_message_to_agent(
    *,
    coordinator: Any,
    loop: asyncio.AbstractEventLoop | None,
    live_view: Any,
    target_agent_id: str,
    message: str,
) -> bool:
    """Record a local user message and enqueue it into the target SDK session."""
    live_view.record_user_message(target_agent_id, message)

    if loop is None or loop.is_closed():
        return False

    asyncio.run_coroutine_threadsafe(
        coordinator.send(
            target_agent_id,
            {"from": "user", "content": message, "type": "instruction"},
        ),
        loop,
    )
    return True
