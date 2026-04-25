"""Helpers for the TUI message renderer.

The model occasionally echoes inter-agent XML envelopes in plain text
despite the system prompt's "don't echo" rule, so :func:`clean_content`
strips them defensively before display.
"""

import re


_HIDDEN_XML_PATTERNS = [
    re.compile(r"<inter_agent_message>.*?</inter_agent_message>", re.DOTALL | re.IGNORECASE),
    re.compile(
        r"<agent_completion_report>.*?</agent_completion_report>",
        re.DOTALL | re.IGNORECASE,
    ),
]
_BLANK_LINE_RUNS = re.compile(r"\n\s*\n")


def clean_content(content: str) -> str:
    if not content:
        return ""
    for pattern in _HIDDEN_XML_PATTERNS:
        content = pattern.sub("", content)
    return _BLANK_LINE_RUNS.sub("\n\n", content).strip()
