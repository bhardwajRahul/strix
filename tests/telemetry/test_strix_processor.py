"""Phase 1 smoke tests for StrixTracingProcessor."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from strix.telemetry.strix_processor import StrixTracingProcessor


@dataclass
class _FakeSpanData:
    """Minimal stand-in for an SDK SpanData class."""

    name: str = "FunctionSpanData"  # used by class .__name__
    payload: dict[str, Any] = field(default_factory=dict)

    def export(self) -> dict[str, Any]:
        return dict(self.payload)


# Concrete SpanData subclasses so the processor's ``_span_kind`` heuristic
# (drop ``SpanData`` suffix, lowercase) produces stable event_types.
class FunctionSpanData(_FakeSpanData):
    pass


class GenerationSpanData(_FakeSpanData):
    pass


class AgentSpanData(_FakeSpanData):
    pass


@dataclass
class _FakeSpan:
    span_id: str
    trace_id: str
    span_data: _FakeSpanData


@dataclass
class _FakeTrace:
    trace_id: str
    name: str = "test-workflow"
    metadata: dict[str, Any] = field(default_factory=dict)

    def export(self) -> dict[str, Any]:
        return {"name": self.name, "metadata": self.metadata}


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path / "strix_runs" / "test-run"


def test_constructor_creates_run_dir(run_dir: Path) -> None:
    StrixTracingProcessor(run_dir=run_dir)
    assert run_dir.exists()


def test_on_trace_start_writes_run_started(run_dir: Path) -> None:
    p = StrixTracingProcessor(run_dir=run_dir)
    p.on_trace_start(_FakeTrace(trace_id="t-1", metadata={"scan_id": "abc"}))

    events = _read_events(p.events_path)
    assert events == [
        {
            "event_type": "run.started",
            "trace_id": "t-1",
            "metadata": {"name": "test-workflow", "metadata": {"scan_id": "abc"}},
        }
    ]


def test_on_trace_end_writes_run_completed(run_dir: Path) -> None:
    p = StrixTracingProcessor(run_dir=run_dir)
    p.on_trace_end(_FakeTrace(trace_id="t-1"))

    events = _read_events(p.events_path)
    assert events == [{"event_type": "run.completed", "trace_id": "t-1"}]


def test_span_start_and_end_emit_typed_events(run_dir: Path) -> None:
    """``GenerationSpanData`` → ``generation.started`` / ``generation.completed``."""
    p = StrixTracingProcessor(run_dir=run_dir)
    span = _FakeSpan(
        span_id="s-1",
        trace_id="t-1",
        span_data=GenerationSpanData(payload={"model": "gpt-foo"}),
    )

    p.on_span_start(span)
    p.on_span_end(span)

    events = _read_events(p.events_path)
    assert [e["event_type"] for e in events] == [
        "generation.started",
        "generation.completed",
    ]
    assert events[0]["span_id"] == "s-1"
    assert events[0]["data"] == {"model": "gpt-foo"}


def test_concurrent_writes_yield_valid_jsonl(run_dir: Path) -> None:
    """C7 (AUDIT_R2): per-path lock prevents JSONL corruption under contention."""
    p = StrixTracingProcessor(run_dir=run_dir)

    def writer(idx: int) -> None:
        for i in range(50):
            p._emit({"event_type": "synthetic", "writer": idx, "i": i})

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 10 threads x 50 events = 500 lines; all valid JSON.
    lines = p.events_path.read_text().splitlines()
    assert len(lines) == 500
    for line in lines:
        json.loads(line)  # raises on corrupt line


def test_emit_swallows_oserror_and_logs(
    run_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """C16 (AUDIT_R3): a write failure must NOT propagate."""
    p = StrixTracingProcessor(run_dir=run_dir)
    # Make events_path point to a directory so open(..., "a") raises.
    p.events_path = run_dir

    with caplog.at_level("ERROR", logger="strix.telemetry.strix_processor"):
        p._emit({"event_type": "boom"})

    assert any("Failed to append" in rec.message for rec in caplog.records)


def test_span_export_failure_does_not_propagate(run_dir: Path) -> None:
    """If span_data.export raises, we still emit an event with data=None."""
    p = StrixTracingProcessor(run_dir=run_dir)

    class _BoomSpanData:
        def export(self) -> dict[str, Any]:
            raise RuntimeError("nope")

    # Reuse the lowercase rule: class name has no "SpanData" suffix → "boomspandata"
    # would not be ideal; use a properly-named subclass.
    class FunctionSpanDataBroken(_BoomSpanData):
        pass

    span = _FakeSpan(span_id="s-1", trace_id="t-1", span_data=FunctionSpanDataBroken())
    p.on_span_end(span)

    events = _read_events(p.events_path)
    assert len(events) == 1
    assert events[0]["data"] is None


def test_pii_scrubbed_via_sanitizer(run_dir: Path) -> None:
    """Sanitizer is invoked on every emit before write."""
    seen: list[Any] = []

    class _StubSanitizer:
        def sanitize(self, data: Any, key_hint: str | None = None) -> Any:
            seen.append(data)
            # Replace any "secret" string with [REDACTED].
            if isinstance(data, dict):
                clean = {k: "[REDACTED]" if k == "api_key" else v for k, v in data.items()}
                if "metadata" in clean and isinstance(clean["metadata"], dict):
                    md = dict(clean["metadata"])
                    md.pop("api_key", None)
                    clean["metadata"] = md
                return clean
            return data

    p = StrixTracingProcessor(run_dir=run_dir, sanitizer=_StubSanitizer())
    p._emit({"event_type": "test", "api_key": "sk-very-secret"})

    events = _read_events(p.events_path)
    assert events[0]["api_key"] == "[REDACTED]"
    assert seen and seen[0]["api_key"] == "sk-very-secret"


def test_force_flush_and_shutdown_are_noops(run_dir: Path) -> None:
    p = StrixTracingProcessor(run_dir=run_dir)
    # Should not raise.
    p.force_flush()
    p.shutdown()
