"""sandboxd control-socket contract.

Newline JSON RPC on a Unix socket. The API process never execs ``runsc`` or ``ip``.

- ``health``: shape ``net`` only (desk/net can start).
- ``run``: ``detach`` after ``runsc run -d`` (long-lived desk guest).
- ``exec``: ``runsc exec`` into a running guest — ``wait`` (JSONL) or ``stdio``
  (browser driver). Does not delete the guest.
- ``netns_setup`` / ``netns_teardown``: packaging allowlist family only.
- ``preview_register`` / ``preview_unregister``: in-process HTTP/WS preview
  reverse-proxy registry (``conversation_id`` + ``process_id``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Shape = Literal["net"]
NetFamily = Literal["package"]
RunMode = Literal["wait", "stdio", "detach"]

DEFAULT_SOCKET_PATH = "/run/agentcore/sandboxd.sock"
SOCKET_ENV = "SANDBOXD_SOCKET"

CONTAINER_ID_PREFIX = "agentcore-"

# Control methods (one JSON request → one JSON response, except ``run`` / ``exec``).
METHOD_PING = "ping"
METHOD_HEALTH = "health"
METHOD_NETNS_SETUP = "netns_setup"
METHOD_NETNS_TEARDOWN = "netns_teardown"
METHOD_RUN = "run"
METHOD_EXEC = "exec"
METHOD_DELETE = "delete"
METHOD_KILL = "kill"
METHOD_PREVIEW_REGISTER = "preview_register"
METHOD_PREVIEW_UNREGISTER = "preview_unregister"


@dataclass(frozen=True, slots=True)
class NetnsInfo:
    family: NetFamily
    slot: int
    name: str
    path: str
    host_ip: str
    sbx_ip: str
