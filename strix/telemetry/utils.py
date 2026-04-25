import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from scrubadub import Scrubber
from scrubadub.detectors import RegexDetector
from scrubadub.filth import Filth


logger = logging.getLogger(__name__)

_REDACTED = "[REDACTED]"
_SCREENSHOT_OMITTED = "[SCREENSHOT_OMITTED]"
_SCREENSHOT_KEY_PATTERN = re.compile(r"screenshot", re.IGNORECASE)
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(api[_-]?key|token|secret|password|authorization|cookie|session|credential|private[_-]?key)",
    re.IGNORECASE,
)
_SENSITIVE_TOKEN_PATTERN = re.compile(
    r"(?i)\b("
    r"bearer\s+[a-z0-9._-]+|"
    r"sk-[a-z0-9_-]{8,}|"
    r"gh[pousr]_[a-z0-9_-]{12,}|"
    r"xox[baprs]-[a-z0-9-]{12,}"
    r")\b"
)
_SCRUBADUB_PLACEHOLDER_PATTERN = re.compile(r"\{\{[^}]+\}\}")
_EVENTS_FILE_LOCKS_LOCK = threading.Lock()
_EVENTS_FILE_LOCKS: dict[str, threading.Lock] = {}


class _SecretFilth(Filth):  # type: ignore[misc]
    type = "secret"


class _SecretTokenDetector(RegexDetector):  # type: ignore[misc]
    name = "strix_secret_token_detector"
    filth_cls = _SecretFilth
    regex = _SENSITIVE_TOKEN_PATTERN


class TelemetrySanitizer:
    def __init__(self) -> None:
        self._scrubber = Scrubber(detector_list=[_SecretTokenDetector])

    def sanitize(self, data: Any, key_hint: str | None = None) -> Any:  # noqa: PLR0911
        if data is None:
            return None

        if isinstance(data, dict):
            sanitized: dict[str, Any] = {}
            for key, value in data.items():
                key_str = str(key)
                if _SCREENSHOT_KEY_PATTERN.search(key_str):
                    sanitized[key_str] = _SCREENSHOT_OMITTED
                elif _SENSITIVE_KEY_PATTERN.search(key_str):
                    sanitized[key_str] = _REDACTED
                else:
                    sanitized[key_str] = self.sanitize(value, key_hint=key_str)
            return sanitized

        if isinstance(data, list):
            return [self.sanitize(item, key_hint=key_hint) for item in data]

        if isinstance(data, tuple):
            return [self.sanitize(item, key_hint=key_hint) for item in data]

        if isinstance(data, str):
            if key_hint and _SENSITIVE_KEY_PATTERN.search(key_hint):
                return _REDACTED

            cleaned = self._scrubber.clean(data)
            return _SCRUBADUB_PLACEHOLDER_PATTERN.sub(_REDACTED, cleaned)

        if isinstance(data, int | float | bool):
            return data

        return str(data)


def get_events_write_lock(output_path: Path) -> threading.Lock:
    path_key = str(output_path.resolve(strict=False))
    with _EVENTS_FILE_LOCKS_LOCK:
        lock = _EVENTS_FILE_LOCKS.get(path_key)
        if lock is None:
            lock = threading.Lock()
            _EVENTS_FILE_LOCKS[path_key] = lock
        return lock


def reset_events_write_locks() -> None:
    with _EVENTS_FILE_LOCKS_LOCK:
        _EVENTS_FILE_LOCKS.clear()


def append_jsonl_record(output_path: Path, record: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with get_events_write_lock(output_path), output_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
