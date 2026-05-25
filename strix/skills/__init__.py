import logging
import re

from strix.utils.resource_paths import get_strix_resource_path


logger = logging.getLogger(__name__)

_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)

# Categories loaded by the prompt template via explicit slash-form paths
# (``scan_modes/<mode>``, ``coordination/<role>``). They're not
# user-selectable through ``create_agent``'s ``skills`` argument.
_INTERNAL_SKILL_CATEGORIES: frozenset[str] = frozenset({"scan_modes", "coordination"})


def get_all_skill_names() -> set[str]:
    """Return every user-selectable skill name (bare, no category prefix).

    Used by :func:`validate_requested_skills` so ``create_agent`` can
    reject typos / hallucinated names with a useful list, and by callers
    that need to enumerate the catalog.
    """
    skills_dir = get_strix_resource_path("skills")
    if not skills_dir.exists():
        return set()
    names: set[str] = set()
    for category_dir in skills_dir.iterdir():
        if not category_dir.is_dir() or category_dir.name.startswith("__"):
            continue
        if category_dir.name in _INTERNAL_SKILL_CATEGORIES:
            continue
        for file_path in category_dir.glob("*.md"):
            names.add(file_path.stem)
    return names


def validate_requested_skills(skill_list: list[str], max_skills: int = 5) -> str | None:
    """Validate a list of user-passed skill names.

    Returns ``None`` on success, or a model-readable error message
    describing what was wrong (count exceeded, unknown names).
    """
    if len(skill_list) > max_skills:
        return (
            f"Cannot specify more than {max_skills} skills per agent; "
            f"got {len(skill_list)}. Aim for 1-3 related skills per specialist."
        )
    if not skill_list:
        return None
    available = get_all_skill_names()
    invalid = sorted({s for s in skill_list if s not in available})
    if invalid:
        return f"Invalid skill name(s): {invalid}. Available skills: {sorted(available)}"
    return None


def load_skills(skill_names: list[str]) -> dict[str, str]:
    """Load skill markdown bodies (frontmatter stripped) by name.

    Skill files live at ``strix/skills/<category>/<name>.md``. Names
    can be ``"name"`` (any category), ``"category/name"``, or a bare
    file at the skills root. Missing skills are logged and skipped.
    """
    skills_dir = get_strix_resource_path("skills")
    if not skills_dir.exists():
        return {}

    by_category: dict[str, str] = {}
    for category_dir in skills_dir.iterdir():
        if not category_dir.is_dir() or category_dir.name.startswith("__"):
            continue
        for file_path in category_dir.glob("*.md"):
            by_category[file_path.stem] = f"{category_dir.name}/{file_path.stem}.md"

    skill_content: dict[str, str] = {}
    for skill_name in skill_names:
        rel_path: str | None
        if "/" in skill_name:
            rel_path = f"{skill_name}.md"
        elif skill_name in by_category:
            rel_path = by_category[skill_name]
        elif (skills_dir / f"{skill_name}.md").exists():
            rel_path = f"{skill_name}.md"
        else:
            rel_path = None

        if rel_path is None or not (skills_dir / rel_path).exists():
            logger.warning("Skill not found: %s", skill_name)
            continue

        try:
            content = (skills_dir / rel_path).read_text(encoding="utf-8")
        except (OSError, ValueError) as e:
            logger.warning("Failed to load skill %s: %s", skill_name, e)
            continue

        var_name = skill_name.split("/")[-1]
        skill_content[var_name] = _FRONTMATTER_PATTERN.sub("", content).lstrip()
        logger.debug("Loaded skill: %s -> %s", skill_name, var_name)

    logger.debug("load_skills: %d skill(s) resolved", len(skill_content))
    return skill_content
