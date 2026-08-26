"""Product catalog identity: @platform / @byok handles vs mentions."""

from agentcore.llm.model_ref import format_model_ref, parse_model_input


def test_format_platform_and_byok():
    assert format_model_ref("platform", "glm-5.2") == "@platform/glm-5.2"
    assert (
        format_model_ref("byok", "openai/gpt-4o", "prov-1")
        == "@byok/prov-1/openai/gpt-4o"
    )


def test_format_incomplete_empty():
    assert format_model_ref("byok", "m", None) == ""
    assert format_model_ref("platform", "") == ""
    assert format_model_ref("", "m") == ""


def test_parse_platform_ref_roundtrip():
    p = parse_model_input("@platform/openai/gpt-4o")
    assert p.kind == "ref"
    assert p.origin == "platform"
    assert p.model == "openai/gpt-4o"
    assert p.provider_id == ""
    assert format_model_ref(p.origin, p.model, p.provider_id) == "@platform/openai/gpt-4o"


def test_parse_byok_ref_model_may_contain_slash():
    p = parse_model_input("@byok/p1/openai/gpt-4o")
    assert p.kind == "ref"
    assert p.origin == "byok"
    assert p.provider_id == "p1"
    assert p.model == "openai/gpt-4o"


def test_parse_prefix_case_insensitive():
    p = parse_model_input("@PLATFORM/flash")
    assert p.kind == "ref" and p.origin == "platform" and p.model == "flash"


def test_parse_mention_and_empty():
    assert parse_model_input("").kind == "empty"
    m = parse_model_input("平台 Flash")
    assert m.kind == "mention" and m.model == "平台 Flash"
    # Unprefixed router key is a mention, not a handle.
    r = parse_model_input("platform/glm-5.2")
    assert r.kind == "mention" and r.model == "platform/glm-5.2"


def test_parse_bad_refs():
    assert parse_model_input("@platform/").kind == "bad_ref"
    assert parse_model_input("@byok/p1").kind == "bad_ref"
    assert parse_model_input("@foo/bar").kind == "bad_ref"
