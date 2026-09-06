"""Cloud-desk egress session (netns + SSRF proxy + package cache dir).

The proxy policy is :func:`core.net.resolve_ssrf_dial_target` (same as
``download_url``). Registry host lists in ``hosts`` pin the *install tool*,
not the network chokepoint.
"""

from __future__ import annotations

from agentcore.tools.sandbox.egress.hosts import (
    allowed_registry_hosts,
    host_is_allowed_registry,
)
from agentcore.tools.sandbox.egress.ready import (
    EGRESS_UNAVAILABLE_CODE,
    registry_egress_available,
)
from agentcore.tools.sandbox.egress.runtime import (
    PackageEgressSession,
    install_proxy_env,
    open_package_egress,
    package_cache_host_dir,
    resolve_cache_bucket,
)

__all__ = [
    "EGRESS_UNAVAILABLE_CODE",
    "PackageEgressSession",
    "allowed_registry_hosts",
    "host_is_allowed_registry",
    "install_proxy_env",
    "open_package_egress",
    "package_cache_host_dir",
    "registry_egress_available",
    "resolve_cache_bucket",
]
