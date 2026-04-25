"""Phase 5b tests for the STRIX_USE_SDK_HARNESS dispatch.

Covers the env-flag reader, source-path resolution, sandbox image
lookup, and the adapter that translates legacy CLI args into
``run_strix_scan`` kwargs.

We never call ``run_strix_scan`` for real — that requires a live
Docker daemon + LLM. The tests patch it and verify the kwargs handoff.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strix.interface.sdk_dispatch import (
    _resolve_sandbox_image,
    _resolve_sources_path,
    run_scan_via_sdk,
    should_use_sdk_harness,
)


# --- env flag reader ----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("YES", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("", False),
        ("anything-else", False),
    ],
)
def test_should_use_sdk_harness_parses_env(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("STRIX_USE_SDK_HARNESS", value)
    assert should_use_sdk_harness() is expected


def test_should_use_sdk_harness_defaults_false_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STRIX_USE_SDK_HARNESS", raising=False)
    assert should_use_sdk_harness() is False


# --- image lookup -------------------------------------------------------


def test_resolve_sandbox_image_uses_config_value() -> None:
    with patch(
        "strix.config.Config.get",
        return_value="strix-sandbox:0.1.13",
    ):
        assert _resolve_sandbox_image() == "strix-sandbox:0.1.13"


def test_resolve_sandbox_image_falls_back_when_unset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        patch("strix.config.Config.get", return_value=None),
        caplog.at_level(logging.WARNING, logger="strix.interface.sdk_dispatch"),
    ):
        out = _resolve_sandbox_image()
    assert out == "strix-sandbox:latest"
    assert any("strix_image not configured" in r.message for r in caplog.records)


# --- sources path -------------------------------------------------------


def test_resolve_sources_path_uses_local_sources_parent(tmp_path: Path) -> None:
    """When --local-sources is given, mount that path's parent so the
    agent can walk down into the actual source directory tree."""
    src_dir = tmp_path / "my-project"
    src_dir.mkdir()
    args = SimpleNamespace(
        local_sources=[{"host_path": str(src_dir)}],
        run_name="run-1",
    )
    assert _resolve_sources_path(args) == tmp_path


def test_resolve_sources_path_handles_alternative_keys(tmp_path: Path) -> None:
    """Some legacy paths use 'source_path' or 'path' instead of
    'host_path' — we accept all three."""
    src_dir = tmp_path / "alt"
    src_dir.mkdir()
    args = SimpleNamespace(
        local_sources=[{"path": str(src_dir)}],
        run_name="run-2",
    )
    assert _resolve_sources_path(args) == tmp_path


def test_resolve_sources_path_creates_scratch_dir_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    args = SimpleNamespace(local_sources=None, run_name="scan-x")
    out = _resolve_sources_path(args)
    assert out == tmp_path / "strix" / "sources" / "scan-x"
    assert out.exists()
    assert out.is_dir()


# --- adapter -----------------------------------------------------------


@pytest.mark.asyncio
async def test_run_scan_via_sdk_translates_args_to_kwargs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify every kwarg the entry point reads is forwarded correctly."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    scan_config = {"targets": [], "scan_mode": "deep"}
    args = SimpleNamespace(
        run_name="scan-42",
        local_sources=None,
        interactive=True,
    )
    fake_tracer = MagicMock(name="tracer")

    fake_run = AsyncMock(return_value=MagicMock(name="run_result"))
    with (
        patch("strix.config.Config.get", return_value="strix-sandbox:test"),
        patch("strix.sdk_entry.run_strix_scan", new=fake_run),
    ):
        await run_scan_via_sdk(scan_config=scan_config, args=args, tracer=fake_tracer)

    fake_run.assert_awaited_once()
    assert fake_run.await_args is not None
    kwargs = fake_run.await_args.kwargs
    assert kwargs["scan_config"] is scan_config
    assert kwargs["scan_id"] == "scan-42"
    assert kwargs["image"] == "strix-sandbox:test"
    assert kwargs["sources_path"] == tmp_path / "strix" / "sources" / "scan-42"
    assert kwargs["tracer"] is fake_tracer
    assert kwargs["interactive"] is True


@pytest.mark.asyncio
async def test_run_scan_via_sdk_falls_back_to_scan_config_run_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If args has no run_name, scan_config['run_name'] should be used."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    scan_config = {"targets": [], "run_name": "from-config"}
    args = SimpleNamespace(local_sources=None)

    fake_run = AsyncMock(return_value=MagicMock())
    with (
        patch("strix.config.Config.get", return_value="img:1"),
        patch("strix.sdk_entry.run_strix_scan", new=fake_run),
    ):
        await run_scan_via_sdk(scan_config=scan_config, args=args, tracer=None)

    assert fake_run.await_args is not None
    assert fake_run.await_args.kwargs["scan_id"] == "from-config"


@pytest.mark.asyncio
async def test_run_scan_via_sdk_propagates_run_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure inside run_strix_scan should bubble up to the caller —
    the legacy CLI relies on raised exceptions for the SDK path."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    scan_config: dict[str, Any] = {"targets": []}
    args = SimpleNamespace(run_name="r", local_sources=None)

    fake_run = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch("strix.config.Config.get", return_value="img"),
        patch("strix.sdk_entry.run_strix_scan", new=fake_run),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await run_scan_via_sdk(scan_config=scan_config, args=args, tracer=None)
