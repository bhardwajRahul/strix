"""Smoke tests for AnthropicCachingLitellmModel."""

from __future__ import annotations

from strix.llm.anthropic_cache_wrapper import AnthropicCachingLitellmModel


def _make(model: str) -> AnthropicCachingLitellmModel:
    # ``LitellmModel.__init__`` only validates that model is a string; we
    # don't need a real API key for in-memory ``_patch`` testing.
    return AnthropicCachingLitellmModel(model=model, api_key="test-key")


def test_is_anthropic_detects_anthropic_prefix() -> None:
    m = _make("anthropic/claude-3-5-sonnet")
    assert m._is_anthropic() is True


def test_is_anthropic_detects_claude_substring() -> None:
    m = _make("openrouter/anthropic-claude-haiku")
    assert m._is_anthropic() is True


def test_is_anthropic_false_for_openai() -> None:
    m = _make("openai/gpt-4o")
    assert m._is_anthropic() is False


def test_is_anthropic_false_for_gemini() -> None:
    m = _make("gemini/gemini-1.5-pro")
    assert m._is_anthropic() is False


def test_patch_anthropic_adds_cache_control_to_system() -> None:
    m = _make("anthropic/claude-3-5-sonnet")
    items: list = [
        {"role": "system", "content": "You are a helpful agent."},
        {"role": "user", "content": "hi"},
    ]
    out = m._patch(items)
    assert out[0]["role"] == "system"
    content = out[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "You are a helpful agent."
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    # Second item passes through unchanged.
    assert out[1] == {"role": "user", "content": "hi"}


def test_patch_non_anthropic_passes_through() -> None:
    m = _make("openai/gpt-4o")
    items: list = [
        {"role": "system", "content": "You are a helpful agent."},
        {"role": "user", "content": "hi"},
    ]
    assert m._patch(items) is items  # exact same list reference, no copy


def test_patch_skips_non_string_system_content() -> None:
    """If system content is already structured (e.g., previously patched),
    don't re-wrap — pass through unchanged."""
    m = _make("anthropic/claude-3-5-sonnet")
    items: list = [
        {"role": "system", "content": [{"type": "text", "text": "x"}]},
        {"role": "user", "content": "hi"},
    ]
    out = m._patch(items)
    assert out[0]["content"] == [{"type": "text", "text": "x"}]


def test_patch_handles_empty_list() -> None:
    m = _make("anthropic/claude-3-5-sonnet")
    assert m._patch([]) == []
