"""Ephemeral process env for ``code_execute`` (in-process secrets, not workspace).

Desktop IPC re-filters the same denylist (``pickUserExecEnv``). Keep the two lists
aligned: no PATH / linker / interpreter hijack via env.
"""

from __future__ import annotations

import re
from typing import Any

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_KEYS = 32
_MAX_KEY_LEN = 128
_MAX_VALUE_LEN = 8192
_MAX_TOTAL = 32768
_MIN_SCRUB_LEN = 8

_DENIED_KEYS = frozenset(
    {
        "PATH",
        "PATHEXT",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONEXECUTABLE",
        "NODE_OPTIONS",
        "NODE_DEBUG",
        "BASH_ENV",
        "ENV",
        "IFS",
        "SHELLOPTS",
        "PERL5OPT",
        "PERL5LIB",
        "RUBYOPT",
        "RUBYLIB",
        "WINDIR",
        "COMSPEC",
        "SYSTEMROOT",
        "PSMODULEPATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FORCE_FLAT_NAMESPACE",
    }
)


class EnvParseError(ValueError):
    """Present but unusable ``env`` object (contract failure, not a sandbox crash)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _denied(key: str) -> bool:
    upper = key.upper()
    return upper in _DENIED_KEYS or upper.startswith(
        ("LD_", "DYLD_", "AGENTCORE_")
    )


def parse_ephemeral_env(raw: Any) -> dict[str, str] | None:
    """Return a sanitized mapping, ``None`` if omitted/empty, or raise ``EnvParseError``."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise EnvParseError("env 必须是字符串键值对象")
    if not raw:
        return None
    if len(raw) > _MAX_KEYS:
        raise EnvParseError(f"env 最多 {_MAX_KEYS} 项")
    out: dict[str, str] = {}
    total = 0
    for key, value in raw.items():
        if not isinstance(key, str) or not _KEY_RE.match(key) or len(key) > _MAX_KEY_LEN:
            raise EnvParseError("env 含有不合法的键名")
        if _denied(key):
            raise EnvParseError(f"env 不允许覆盖 {key}")
        if not isinstance(value, str):
            raise EnvParseError(f"env[{key}] 必须是字符串")
        if len(value) > _MAX_VALUE_LEN:
            raise EnvParseError(f"env[{key}] 过长")
        total += len(value)
        if total > _MAX_TOTAL:
            raise EnvParseError("env 总值过长")
        out[key] = value
    return out


def scrub_env_values(text: str, env: dict[str, str] | None) -> str:
    """Strip this call's env values then known secret shapes from tool output / SSE chunks."""
    from agentcore.core.secrets import REDACTED, redact_secrets

    if not text:
        return text
    if env:
        for value in sorted(
            (item for item in env.values() if len(item) >= _MIN_SCRUB_LEN),
            key=len,
            reverse=True,
        ):
            text = text.replace(value, REDACTED)
    return redact_secrets(text)


def redact_tool_arguments_for_wire(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Copy args for SSE / journal / approval: env values gone, known shapes in strings gone."""
    from agentcore.core.secrets import REDACTED, redact_secrets

    if not isinstance(arguments, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in arguments.items():
        if key == "env" and isinstance(value, dict):
            out[key] = {str(env_key): REDACTED for env_key in value}
        elif isinstance(value, str):
            out[key] = redact_secrets(value)
        else:
            out[key] = value
    return out
