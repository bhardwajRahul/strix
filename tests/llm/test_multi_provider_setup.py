"""Smoke tests for the multi-provider setup."""

from __future__ import annotations

import pytest
from agents.exceptions import UserError

from strix.llm.anthropic_cache_wrapper import AnthropicCachingLitellmModel
from strix.llm.multi_provider_setup import (
    _AnthropicCachingProvider,
    build_multi_provider,
)


def test_anthropic_provider_wraps_in_caching_model() -> None:
    provider = _AnthropicCachingProvider()
    model = provider.get_model("claude-sonnet-4-6")
    assert isinstance(model, AnthropicCachingLitellmModel)
    # The provider re-prefixes with ``anthropic/`` so litellm routes correctly.
    assert model.model == "anthropic/claude-sonnet-4-6"
    assert model._is_anthropic() is True


def test_anthropic_provider_preserves_existing_anthropic_prefix() -> None:
    """If the alias already carries ``anthropic/``, don't double-prefix."""
    provider = _AnthropicCachingProvider()
    model = provider.get_model("anthropic/claude-3-5-sonnet-20241022")
    assert model.model == "anthropic/claude-3-5-sonnet-20241022"


def test_anthropic_provider_empty_name_raises() -> None:
    provider = _AnthropicCachingProvider()
    with pytest.raises(UserError, match="non-empty"):
        provider.get_model(None)


def test_build_multi_provider_routes_anthropic_through_caching_wrapper() -> None:
    """The configured MultiProvider should hit our caching wrapper for the
    ``anthropic/`` prefix."""
    mp = build_multi_provider()
    model = mp.get_model("anthropic/claude-sonnet-4-6")
    assert isinstance(model, AnthropicCachingLitellmModel)
