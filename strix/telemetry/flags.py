from strix.config import Config


_DISABLED_VALUES = {"0", "false", "no", "off"}


def _is_enabled(raw_value: str | None, default: str = "1") -> bool:
    value = (raw_value if raw_value is not None else default).strip().lower()
    return value not in _DISABLED_VALUES


def is_telemetry_enabled() -> bool:
    """Master gate — controls JSONL event emission and posthog."""
    return _is_enabled(Config.get("strix_telemetry"), default="1")


def is_posthog_enabled() -> bool:
    explicit = Config.get("strix_posthog_telemetry")
    if explicit is not None:
        return _is_enabled(explicit)
    return is_telemetry_enabled()
