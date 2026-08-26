"""Package-registry egress chokepoint (install path only).

Allowlist-only (hosts from ``ALLOWED_NPM_REGISTRIES`` + egress-only
``ALLOWED_NPM_HOSTS`` CDN; CDN ≠ pin registry), not browser SSRF deny-private.
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
