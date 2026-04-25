"""Phase 1 smoke tests for StrixSession (memory compression wrapper)."""

from __future__ import annotations

from typing import Any

import pytest
from agents.memory.session import SessionABC

from strix.llm.strix_session import StrixSession


class _FakeUnderlying(SessionABC):
    """In-memory SessionABC used to drive StrixSession in tests."""

    def __init__(self, items: list[Any] | None = None) -> None:
        self.items: list[Any] = list(items or [])
        self.session_id = "fake-session"

    async def get_items(self, limit: int | None = None) -> list[Any]:
        if limit is None:
            return list(self.items)
        return list(self.items[-limit:])

    async def add_items(self, items: list[Any]) -> None:
        self.items.extend(items)

    async def pop_item(self) -> Any | None:
        return self.items.pop() if self.items else None

    async def clear_session(self) -> None:
        self.items.clear()


class _CompressorOK:
    """Compressor stand-in that compresses by keeping the last item."""

    def __init__(self) -> None:
        self.calls = 0

    def compress_history(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.calls += 1
        return messages[-1:] if len(messages) > 1 else messages


class _CompressorBoom:
    """Compressor stand-in that always raises."""

    def __init__(self) -> None:
        self.calls = 0

    def compress_history(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.calls += 1
        raise RuntimeError("compressor offline")


@pytest.fixture
def items() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    ]


@pytest.mark.asyncio
async def test_get_items_compresses_when_compressor_ok(items: list[dict[str, Any]]) -> None:
    underlying = _FakeUnderlying(items)
    session = StrixSession(underlying, compressor=_CompressorOK())

    out = await session.get_items()
    assert len(out) == 1
    assert out[0]["content"] == "third"


@pytest.mark.asyncio
async def test_get_items_returns_empty_without_calling_compressor() -> None:
    """If underlying has no items, don't even invoke the compressor."""
    underlying = _FakeUnderlying([])
    compressor = _CompressorOK()
    session = StrixSession(underlying, compressor=compressor)

    out = await session.get_items()
    assert out == []
    assert compressor.calls == 0


@pytest.mark.asyncio
async def test_get_items_falls_back_to_uncompressed_on_exception(
    items: list[dict[str, Any]],
) -> None:
    """C10 (AUDIT_R2): compressor failure must not tear down the run."""
    underlying = _FakeUnderlying(items)
    compressor = _CompressorBoom()
    session = StrixSession(underlying, compressor=compressor)

    out = await session.get_items()
    # Uncompressed history returned.
    assert out == items
    # Flag set so subsequent calls skip the compressor.
    assert session.compression_disabled is True


@pytest.mark.asyncio
async def test_compressor_disabled_after_first_failure(items: list[dict[str, Any]]) -> None:
    """Round 3.4 §E2 / W5 — once the compressor fails, skip it forever."""
    underlying = _FakeUnderlying(items)
    compressor = _CompressorBoom()
    session = StrixSession(underlying, compressor=compressor)

    # First call: compressor invoked, raises, flag set.
    await session.get_items()
    assert compressor.calls == 1
    assert session.compression_disabled is True

    # Second + third call: compressor short-circuited.
    await session.get_items()
    await session.get_items()
    assert compressor.calls == 1


@pytest.mark.asyncio
async def test_writes_pass_through(items: list[dict[str, Any]]) -> None:
    underlying = _FakeUnderlying()
    session = StrixSession(underlying, compressor=_CompressorOK())

    await session.add_items(items)
    assert underlying.items == items

    popped = await session.pop_item()
    assert popped == items[-1]

    await session.clear_session()
    assert underlying.items == []


@pytest.mark.asyncio
async def test_session_id_passes_through() -> None:
    underlying = _FakeUnderlying()
    session = StrixSession(underlying, compressor=_CompressorOK())
    assert session.session_id == "fake-session"


@pytest.mark.asyncio
async def test_get_items_respects_limit(items: list[dict[str, Any]]) -> None:
    """``limit`` is forwarded to the underlying session before compression."""
    underlying = _FakeUnderlying(items)
    session = StrixSession(underlying, compressor=_CompressorOK())

    out = await session.get_items(limit=2)
    # Underlying returned last 2 items; compressor kept the last 1.
    assert len(out) == 1
    assert out[0]["content"] == "third"
