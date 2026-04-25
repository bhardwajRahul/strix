"""Strix multi-agent orchestration on top of OpenAI Agents SDK.

- :class:`AgentMessageBus` — peer-to-peer agent inbox + status + stats.
- :func:`inject_messages_filter` — SDK ``call_model_input_filter`` for
  inbox drain at the top of each LLM turn.
- :class:`StrixOrchestrationHooks` — SDK ``RunHooks`` subclass for
  lifecycle wiring.

Import deeply (``from strix.orchestration.bus import AgentMessageBus``)
so ``import strix.orchestration`` doesn't drag every submodule's deps
in eagerly.
"""
