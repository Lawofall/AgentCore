"""sandboxd control-socket contract.

Newline JSON RPC on a Unix socket. The API process never execs ``runsc`` or ``ip``.
Shapes:

- ``code``: rootless ``runsc`` (``--network=none|host``) for ``code_execute``.
- ``net``: non-rootless ``--platform=systrap --network=sandbox --ignore-cgroups``
  for ``browser`` and ``package_install``.

``run`` modes:

- ``wait``: JSON result ``mode=wait``, then JSONL ``stdout`` / ``stderr`` / ``exit``.
- ``stdio``: JSON result ``mode=stdio``, remainder of the connection is raw
  bidirectional bytes spliced to ``runsc`` stdin/stdout (browser driver RPC).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Shape = Literal["code", "net"]
NetFamily = Literal["browser", "package"]
RunMode = Literal["wait", "stdio"]
CodeNetwork = Literal["none", "host"]

DEFAULT_SOCKET_PATH = "/run/agentcore/sandboxd.sock"
SOCKET_ENV = "SANDBOXD_SOCKET"

CONTAINER_ID_PREFIX = "agentcore-"

# Control methods (one JSON request → one JSON response, except ``run``).
METHOD_PING = "ping"
METHOD_HEALTH = "health"
METHOD_NETNS_SETUP = "netns_setup"
METHOD_NETNS_TEARDOWN = "netns_teardown"
METHOD_RUN = "run"
METHOD_DELETE = "delete"
METHOD_KILL = "kill"


@dataclass(frozen=True, slots=True)
class NetnsInfo:
    family: NetFamily
    slot: int
    name: str
    path: str
    host_ip: str
    sbx_ip: str
