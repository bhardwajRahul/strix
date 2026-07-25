"""Bound oversized tool results before they enter agent history.

A single verbose tool result (a recursive ``find``, a noisy scanner, a full
page dump) can otherwise pin the whole conversation near the model's context
limit for the rest of the scan. This keeps a head + tail slice of the output
and drops the middle, mirroring how the shell capability truncates its own
output — the agent still sees the start and end plus how much was removed.
"""

from __future__ import annotations


_TRUNCATION_NOTICE = "[... {lines} lines ({bytes} bytes) truncated ...]"


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _take_prefix(text: str, max_bytes: int) -> str:
    budget = 0
    out: list[str] = []
    for char in text:
        size = len(char.encode("utf-8"))
        if budget + size > max_bytes:
            break
        out.append(char)
        budget += size
    return "".join(out)


def _take_suffix(text: str, max_bytes: int) -> str:
    budget = 0
    out: list[str] = []
    for char in reversed(text):
        size = len(char.encode("utf-8"))
        if budget + size > max_bytes:
            break
        out.append(char)
        budget += size
    out.reverse()
    return "".join(out)


def bound_text(text: str, *, max_lines: int, max_bytes: int) -> str:
    """Return ``text`` unchanged when small, else a head+tail preview.

    Truncation happens on whichever limit is hit first (line count or UTF-8
    byte size). The removed middle is replaced with a notice recording how
    many lines and bytes were dropped so the agent knows output was elided.
    """
    lines = text.split("\n")
    total_bytes = _byte_len(text)
    if len(lines) <= max_lines and total_bytes <= max_bytes:
        return text

    head_lines = max(1, max_lines // 2)
    tail_lines = max_lines - head_lines
    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[len(lines) - tail_lines :]) if tail_lines > 0 else ""

    # Enforce the byte budget even when the line count alone was fine.
    half_bytes = max(1, max_bytes // 2)
    if _byte_len(head) > half_bytes:
        head = _take_prefix(head, half_bytes)
    if tail and _byte_len(tail) > half_bytes:
        tail = _take_suffix(tail, half_bytes)

    # Count kept lines from the final slices: the byte pass above may have
    # dropped whole lines from head/tail, so deriving this from the original
    # head_lines/tail_lines would undercount what was actually removed.
    kept_lines = len(head.split("\n")) + (len(tail.split("\n")) if tail else 0)
    dropped_lines = max(0, len(lines) - kept_lines)
    dropped_bytes = max(0, total_bytes - _byte_len(head) - _byte_len(tail))
    notice = _TRUNCATION_NOTICE.format(lines=dropped_lines, bytes=dropped_bytes)
    return f"{head}\n\n{notice}\n\n{tail}" if tail else f"{head}\n\n{notice}"
