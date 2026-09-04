"""Pin sandboxd as an independent compose service (not an in-API setpriv helper).

API stays USER app / no caps. Postgres client-key HOME still belongs on the
image (USER app). sandboxd is uid 0 in its own container and does not talk
to postgres.
"""

from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_DOCKERFILE = _REPO / "apps" / "server" / "Dockerfile"
_ENTRYPOINT = _REPO / "deploy" / "sandboxd-entrypoint.sh"
_COMPOSE = _REPO / "deploy" / "docker-compose.sandbox.yml"
_APP_COMPOSE = _REPO / "deploy" / "docker-compose.app.yml"


def test_dockerfile_creates_app_home_and_sets_home_env():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert "--no-create-home" not in text
    assert "--create-home --home-dir /home/app" in text
    assert "HOME=/home/app" in text
    assert "chmod 0700 /home/app" in text


def test_sandboxd_entrypoint_is_uid0_pid1_no_setpriv():
    text = _ENTRYPOINT.read_text(encoding="utf-8")
    assert "python -m agentcore.tools.sandbox.sandboxd" in text
    assert "setpriv" not in text
    assert "SANDBOXD_PID" not in text
    assert "API_PID" not in text
    assert "mount --bind /run/netns /run/netns" in text
    assert "export HOME=/home/app" not in text


def test_sandbox_compose_isolates_caps_on_sandboxd_not_api():
    text = _COMPOSE.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "privileged:" not in body
    assert "sandboxd-entrypoint.sh" in text
    assert "api-sandbox-entrypoint.sh" not in text
    assert "NET_ADMIN" in text
    assert "SYS_ADMIN" in text
    assert 'user: "0:0"' in text
    assert "sandbox-run:/run/agentcore" in text
    assert "container_name: agentcore-sandboxd" in text
    # Caps belong to sandboxd, not an api overlay.
    api_block = body.split("  api:", 1)[1] if "  api:" in body else ""
    assert "cap_add:" not in api_block
    assert "entrypoint:" not in api_block
    assert "mem_limit:" not in api_block
    sandboxd_block = body.split("  sandboxd:", 1)[1].split("  api:", 1)[0]
    assert "cap_add:" in sandboxd_block
    assert "mem_limit: 4g" in sandboxd_block
    # Preview HTTP: host loopback only; container binds 0.0.0.0 to receive publish.
    assert "127.0.0.1:8787:8787" in sandboxd_block
    assert "0.0.0.0:8787" not in sandboxd_block
    assert "PREVIEW_BIND_HOST" in sandboxd_block
    assert 'PREVIEW_BIND_HOST: "0.0.0.0"' in sandboxd_block


def test_app_compose_api_keeps_unprivileged_command():
    text = _APP_COMPOSE.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "cap_add:" not in body
    assert 'command: ["python", "-m", "agentcore"]' in text
    assert "privileged:" not in body
