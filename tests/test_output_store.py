"""Tests for per-tool-output bounding."""

from __future__ import annotations

import re

from strix.tools.output_store import bound_text


def test_small_output_passes_through_unchanged() -> None:
    text = "line 1\nline 2\nline 3"
    assert bound_text(text, max_lines=100, max_bytes=10_000) == text


def test_line_limit_keeps_head_and_tail() -> None:
    text = "\n".join(str(i) for i in range(1000))
    bounded = bound_text(text, max_lines=10, max_bytes=1_000_000)

    assert bounded.startswith("0\n1\n2\n3\n4")
    assert bounded.rstrip().endswith("999")
    assert "truncated" in bounded
    # Head + tail only, far fewer than the original 1000 lines.
    assert len(bounded.splitlines()) < 30


def test_byte_limit_enforced_on_single_long_line() -> None:
    text = "x" * 100_000
    bounded = bound_text(text, max_lines=2_000, max_bytes=1_000)

    assert "truncated" in bounded
    # The whole joined result (head + tail + notice + separators) honours the
    # configured maximum, not just the head/tail slices.
    assert len(bounded.encode("utf-8")) <= 1_000


def test_multibyte_characters_not_split() -> None:
    text = "😀" * 50_000
    bounded = bound_text(text, max_lines=2_000, max_bytes=1_000)

    # Must remain valid UTF-8 (no broken surrogate halves from a mid-char cut).
    assert bounded == bounded.encode("utf-8").decode("utf-8")
    assert "truncated" in bounded


def test_notice_reports_dropped_counts() -> None:
    text = "\n".join("y" * 10 for _ in range(500))
    bounded = bound_text(text, max_lines=10, max_bytes=1_000_000)

    assert "lines" in bounded
    assert "bytes" in bounded


def test_dropped_line_count_accounts_for_byte_trimming() -> None:
    # A tight byte budget forces the byte pass to drop whole lines from the
    # head/tail slices; the notice must count those, not just the middle.
    text = "\n".join(f"line-{i}" for i in range(200))
    bounded = bound_text(text, max_lines=20, max_bytes=40)

    match = re.search(r"\[\.\.\. (\d+) lines", bounded)
    assert match is not None, bounded
    dropped = int(match.group(1))
    kept = [ln for ln in bounded.splitlines() if ln and "truncated" not in ln]
    assert dropped == 200 - len(kept)
    # The naive middle-only count (max_lines split evenly) would under-report.
    assert dropped > 200 - 20
