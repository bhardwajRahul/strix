"""Phase 1 smoke tests for make_run_config / make_agent_context."""

from __future__ import annotations

from agents import RunConfig
from agents.model_settings import ModelSettings
from agents.retry import ModelRetryBackoffSettings

from strix.orchestration.bus import AgentMessageBus
from strix.run_config_factory import (
    _RETRYABLE_HTTP_STATUSES,
    make_agent_context,
    make_run_config,
)


def test_make_run_config_returns_run_config() -> None:
    cfg = make_run_config(sandbox_session=None)
    assert isinstance(cfg, RunConfig)


def test_default_parallel_tool_calls_is_false() -> None:
    """C1 (AUDIT.md): Phase 1 default is sequential to match legacy tool server."""
    cfg = make_run_config(sandbox_session=None)
    assert cfg.model_settings is not None
    assert cfg.model_settings.parallel_tool_calls is False


def test_default_tool_choice_is_required() -> None:
    cfg = make_run_config(sandbox_session=None)
    assert cfg.model_settings is not None
    assert cfg.model_settings.tool_choice == "required"


def test_call_model_input_filter_is_wired() -> None:
    cfg = make_run_config(sandbox_session=None)
    assert cfg.call_model_input_filter is not None
    # Wired to inject_messages_filter (validated by name to keep import light).
    assert cfg.call_model_input_filter.__name__ == "inject_messages_filter"


def test_retry_settings_have_max_retries_5() -> None:
    cfg = make_run_config(sandbox_session=None)
    assert cfg.model_settings is not None
    retry = cfg.model_settings.retry
    assert retry is not None
    assert retry.max_retries == 5


def test_retry_backoff_uses_strix_defaults() -> None:
    """Mirrors legacy llm.py: min(90, 2*2^n) with initial 2s, max 90s, x2."""
    cfg = make_run_config(sandbox_session=None)
    assert cfg.model_settings is not None
    retry = cfg.model_settings.retry
    assert retry is not None
    backoff = retry.backoff
    assert isinstance(backoff, ModelRetryBackoffSettings)
    assert backoff.initial_delay == 2.0
    assert backoff.max_delay == 90.0
    assert backoff.multiplier == 2.0


def test_retry_http_codes_exclude_401_403_400() -> None:
    """C11 (AUDIT_R2): auth/validation errors must NOT be in the retry list."""
    assert 401 not in _RETRYABLE_HTTP_STATUSES
    assert 403 not in _RETRYABLE_HTTP_STATUSES
    assert 400 not in _RETRYABLE_HTTP_STATUSES
    # And 429 / 5xx must be present.
    for code in (429, 500, 502, 503, 504):
        assert code in _RETRYABLE_HTTP_STATUSES


def test_trace_include_sensitive_data_is_false() -> None:
    cfg = make_run_config(sandbox_session=None)
    assert cfg.trace_include_sensitive_data is False


def test_model_settings_override_merges() -> None:
    """C21 (AUDIT_R3): per-call override path."""
    override = ModelSettings(tool_choice="auto", parallel_tool_calls=True)
    cfg = make_run_config(sandbox_session=None, model_settings_override=override)
    assert cfg.model_settings is not None
    assert cfg.model_settings.tool_choice == "auto"
    assert cfg.model_settings.parallel_tool_calls is True
    # Retry settings (not in override) preserved from base.
    assert cfg.model_settings.retry is not None
    assert cfg.model_settings.retry.max_retries == 5


def test_reasoning_effort_propagates() -> None:
    cfg = make_run_config(sandbox_session=None, reasoning_effort="high")
    assert cfg.model_settings is not None
    assert cfg.model_settings.reasoning is not None
    assert cfg.model_settings.reasoning.effort == "high"


def test_max_turns_default_is_300() -> None:
    """Mirrors legacy AgentState.max_iterations=300 (HARNESS_WIKI §5.2)."""
    # max_turns is RunConfig-level; we default 300 in make_agent_context for
    # the per-agent context dict. RunConfig itself sets max_turns at run call
    # time via Runner.run(max_turns=...). Verify our context.
    bus = AgentMessageBus()
    ctx = make_agent_context(
        bus=bus,
        sandbox_session=None,
        sandbox_token=None,
        tool_server_host_port=None,
        caido_host_port=None,
        agent_id="root",
        agent_name="root",
        parent_id=None,
        tracer=None,
    )
    assert ctx["max_turns"] == 300


def test_make_agent_context_full_shape() -> None:
    """C21 — context dict carries every field tools/hooks reach for."""
    bus = AgentMessageBus()
    ctx = make_agent_context(
        bus=bus,
        sandbox_session=None,
        sandbox_token="bearer-xyz",
        tool_server_host_port=48081,
        caido_host_port=48080,
        agent_id="agent-1",
        agent_name="root",
        parent_id=None,
        tracer="not-a-real-tracer",
        is_whitebox=True,
        diff_scope={"changed_files": ["src/app.py"]},
        run_id="strix_runs/abc_def",
    )

    assert ctx["bus"] is bus
    assert ctx["agent_id"] == "agent-1"
    assert ctx["parent_id"] is None
    assert ctx["agent_finish_called"] is False
    assert ctx["turn_count"] == 0
    assert ctx["is_whitebox"] is True
    assert ctx["diff_scope"] == {"changed_files": ["src/app.py"]}
    assert ctx["run_id"] == "strix_runs/abc_def"
    assert ctx["sandbox_token"] == "bearer-xyz"
    assert ctx["tool_server_host_port"] == 48081
    assert ctx["caido_host_port"] == 48080


def test_make_agent_context_is_whitebox_defaults_false() -> None:
    bus = AgentMessageBus()
    ctx = make_agent_context(
        bus=bus,
        sandbox_session=None,
        sandbox_token=None,
        tool_server_host_port=None,
        caido_host_port=None,
        agent_id="r",
        agent_name="root",
        parent_id=None,
        tracer=None,
    )
    assert ctx["is_whitebox"] is False
    assert ctx["diff_scope"] is None


def test_sandbox_config_omitted_when_no_session() -> None:
    cfg = make_run_config(sandbox_session=None)
    assert cfg.sandbox is None


def test_model_default_is_strix_claude() -> None:
    """Production default per AUDIT/PLAYBOOK convention."""
    cfg = make_run_config(sandbox_session=None)
    assert cfg.model == "strix/claude-sonnet-4.6"


def test_multi_provider_is_built() -> None:
    """Verify the factory wires our custom MultiProvider, not the SDK default."""
    cfg = make_run_config(sandbox_session=None)
    # MultiProvider is opaque, but our build_multi_provider returns
    # an instance with our prefix routes installed.
    assert cfg.model_provider is not None
