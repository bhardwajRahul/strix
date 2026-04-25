"""Standalone Jinja-based system-prompt renderer for SDK agents.

The legacy ``LLM._load_system_prompt`` couples prompt rendering to the
LLM client class. The SDK migration owns the model client through
``MultiProvider`` instead, so we extract the rendering logic into a
plain function that the SDK agent factory can call without pulling in
the legacy ``LLM`` instance.

Reuses the existing Jinja template at
``strix/agents/StrixAgent/system_prompt.jinja`` (508 lines, expanding
into the multi-section prompt with skills, tools, scan modes, etc.) so
behavior parity is preserved verbatim — only the call site changes.

References:
    - HARNESS_WIKI.md §4.1 (system prompt assembly)
    - PLAYBOOK.md §4 (per-tool migration contracts)
"""

from __future__ import annotations

import logging
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from strix.skills import load_skills
from strix.tools import get_tools_prompt
from strix.utils.resource_paths import get_strix_resource_path


logger = logging.getLogger(__name__)


# Hard-coded to the StrixAgent template since it's the only agent type
# under the SDK migration. The legacy harness supported multiple agent
# names but in practice only StrixAgent ships.
_AGENT_NAME = "StrixAgent"


def _resolve_skills(
    *,
    requested: list[str] | None,
    scan_mode: str = "deep",
    is_whitebox: bool = False,
) -> list[str]:
    """Build the deduped, ordered skills list for the prompt render.

    Mirrors :py:meth:`LLM._get_skills_to_load` exactly so the rendered
    prompt is byte-identical to the legacy path:

    1. Whatever the caller asked for, in order.
    2. ``scan_modes/<mode>`` (always).
    3. Whitebox-specific skills if applicable.
    """
    ordered: list[str] = list(requested or [])
    ordered.append(f"scan_modes/{scan_mode}")
    if is_whitebox:
        ordered.append("coordination/source_aware_whitebox")
        ordered.append("custom/source_aware_sast")

    deduped: list[str] = []
    seen: set[str] = set()
    for skill in ordered:
        if skill and skill not in seen:
            deduped.append(skill)
            seen.add(skill)
    return deduped


def render_system_prompt(
    *,
    skills: list[str] | None = None,
    scan_mode: str = "deep",
    is_whitebox: bool = False,
    interactive: bool = False,
    system_prompt_context: dict[str, Any] | None = None,
) -> str:
    """Render the StrixAgent system prompt.

    Args:
        skills: Skills the caller wants preloaded into the prompt
            context (the agent can also load more at runtime via the
            ``load_skill`` tool).
        scan_mode: ``"deep" | "fast" | ...``. Maps to ``scan_modes/<mode>``
            skill.
        is_whitebox: When True, the source-aware whitebox skill stack
            is loaded too.
        interactive: When True, the prompt renders the interactive-mode
            communication rules block.
        system_prompt_context: Free-form dict that the template's
            ``system_prompt_context`` variable receives — used today for
            the scan-scope authorization block from
            :py:meth:`StrixAgent._build_system_scope_context`.

    Returns the rendered prompt string. If anything goes wrong (template
    missing, render failure), returns an empty string and logs — same
    fail-soft posture as the legacy method, because a missing prompt is
    survivable but a hard failure during agent construction is not.
    """
    try:
        prompt_dir = get_strix_resource_path("agents", _AGENT_NAME)
        skills_dir = get_strix_resource_path("skills")
        env = Environment(
            loader=FileSystemLoader([prompt_dir, skills_dir]),
            autoescape=select_autoescape(
                enabled_extensions=(),
                default_for_string=False,
            ),
        )

        skills_to_load = _resolve_skills(
            requested=skills,
            scan_mode=scan_mode,
            is_whitebox=is_whitebox,
        )
        skill_content = load_skills(skills_to_load)
        env.globals["get_skill"] = lambda name: skill_content.get(name, "")

        rendered = env.get_template("system_prompt.jinja").render(
            get_tools_prompt=get_tools_prompt,
            loaded_skill_names=list(skill_content.keys()),
            interactive=interactive,
            system_prompt_context=system_prompt_context or {},
            **skill_content,
        )
    except Exception:
        logger.exception("render_system_prompt failed; returning empty prompt")
        return ""
    else:
        return str(rendered)
