"""Strix agent assembly.

- :func:`strix.agents.factory.build_strix_agent` — assemble a root or
  child ``SandboxAgent``.
- :func:`strix.agents.factory.make_child_factory` — closure factory
  passed via context to the multi-agent ``create_agent`` graph tool.
- :func:`strix.agents.prompt.render_system_prompt` — render the Jinja
  system prompt.

Import deeply so ``import strix.agents`` doesn't pull every submodule's
deps in eagerly.
"""
