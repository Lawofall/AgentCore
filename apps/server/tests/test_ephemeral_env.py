"""Ephemeral execute env: parse, wire redaction, output scrub."""

from __future__ import annotations

import pytest

from agentcore.core.ephemeral_env import (
    EnvParseError,
    parse_ephemeral_env,
    redact_tool_arguments_for_wire,
    scrub_env_values,
)
from agentcore.core.secrets import REDACTED
from agentcore.runtime.events.chat import tool_use_start


def test_parse_omitted_and_empty():
    assert parse_ephemeral_env(None) is None
    assert parse_ephemeral_env({}) is None


def test_parse_keeps_api_key():
    assert parse_ephemeral_env({"AGNES_API_KEY": "sk-not-a-real-key-value"}) == {
        "AGNES_API_KEY": "sk-not-a-real-key-value"
    }


def test_parse_rejects_path_and_linker():
    with pytest.raises(EnvParseError, match="PATH"):
        parse_ephemeral_env({"PATH": "/evil"})
    with pytest.raises(EnvParseError, match="LD_PRELOAD"):
        parse_ephemeral_env({"LD_PRELOAD": "x"})
    with pytest.raises(EnvParseError, match="AGENTCORE"):
        parse_ephemeral_env({"AGENTCORE_EXTERNAL_X": "/tmp"})


def test_parse_rejects_non_string_values():
    with pytest.raises(EnvParseError, match="字符串"):
        parse_ephemeral_env({"FOO": 1})


def test_wire_redacts_env_values_keeps_keys():
    out = redact_tool_arguments_for_wire(
        {"code": "print(1)", "env": {"AGNES_API_KEY": "sk-abcdEFGH1234567890"}}
    )
    assert out["env"] == {"AGNES_API_KEY": REDACTED}
    assert out["code"] == "print(1)"


def test_tool_use_start_does_not_ship_env_values():
    event = tool_use_start(
        "c1",
        "code_execute",
        {"code": "print(1)", "env": {"TOKEN": "opaque-secret-value-here"}},
    )
    assert event.payload["arguments"]["env"] == {"TOKEN": REDACTED}


def test_scrub_replaces_this_call_values_and_known_shapes():
    text = "got opaque-secret-value-here and sk-abcdEFGH1234567890"
    out = scrub_env_values(text, {"TOKEN": "opaque-secret-value-here"})
    assert "opaque-secret-value-here" not in out
    assert "sk-abcdEFGH1234567890" not in out
    assert REDACTED in out
