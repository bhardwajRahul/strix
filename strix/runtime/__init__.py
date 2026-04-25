"""Strix runtime package.

- :class:`strix.runtime.strix_docker_client.StrixDockerSandboxClient` —
  host-side ``DockerSandboxClient`` subclass that injects
  ``NET_ADMIN`` / ``NET_RAW`` capabilities and ``host.docker.internal``
  extra-hosts, used by the per-scan session manager
  (:mod:`strix.sandbox.session_manager`).
"""
