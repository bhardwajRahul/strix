"""Bound oversized tool results before they enter agent history.

Keeps a head + tail slice and drops the middle, replacing it with a notice of
how much was removed.
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

    Truncates on whichever limit is hit first (line count or UTF-8 byte size).
    ``max_bytes`` bounds the entire joined result, notice and separators
    included.
    """
    lines = text.split("\n")
    total_bytes = _byte_len(text)
    if len(lines) <= max_lines and total_bytes <= max_bytes:
        return text

    # Reserve notice + separator bytes up front; ``+ 4`` covers the two "\n\n".
    notice_overhead = _byte_len(_TRUNCATION_NOTICE.format(lines=len(lines), bytes=total_bytes)) + 4
    byte_budget = max(2, max_bytes - notice_overhead)

    head_lines = max(1, max_lines // 2)
    tail_lines = max_lines - head_lines
    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[len(lines) - tail_lines :]) if tail_lines > 0 else ""

    half_bytes = max(1, byte_budget // 2)
    if _byte_len(head) > half_bytes:
        head = _take_prefix(head, half_bytes)
    if tail and _byte_len(tail) > half_bytes:
        tail = _take_suffix(tail, half_bytes)

    # Count from the final slices; the byte pass may have dropped whole lines.
    kept_lines = len(head.split("\n")) + (len(tail.split("\n")) if tail else 0)
    dropped_lines = max(0, len(lines) - kept_lines)
    dropped_bytes = max(0, total_bytes - _byte_len(head) - _byte_len(tail))
    notice = _TRUNCATION_NOTICE.format(lines=dropped_lines, bytes=dropped_bytes)
    return f"{head}\n\n{notice}\n\n{tail}" if tail else f"{head}\n\n{notice}"
