"""Strix runtime — Docker-backed sandbox lifecycle on top of the Agents SDK.

- :class:`strix.runtime.docker_client.StrixDockerSandboxClient` —
  ``DockerSandboxClient`` subclass that injects ``NET_ADMIN`` /
  ``NET_RAW`` capabilities and ``host.docker.internal`` extra-hosts.
- :mod:`.session_manager` — ``create_or_reuse`` / ``cleanup`` keyed
  by scan id; bundles the SDK session with a ready Caido client.
- :mod:`.caido_bootstrap` — runtime-agnostic Caido auth dance via
  ``session.exec``.
"""
