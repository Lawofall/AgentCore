"""Unix sandboxd client: missing socket is fail-closed."""

import pytest

from agentcore.tools.sandbox.sandboxd.client import UnixSandboxdClient
from agentcore.tools.sandbox.sandboxd.errors import SandboxdUnavailable


async def test_unix_client_missing_socket_is_unavailable(tmp_path):
    client = UnixSandboxdClient(str(tmp_path / "no-such.sock"))
    with pytest.raises(SandboxdUnavailable):
        await client.ping()
