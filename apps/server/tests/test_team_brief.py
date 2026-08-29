"""CEO team_brief parse + cap (opening consensus; no note wall)."""

from agentcore.runtime.delegate.team_brief import MAX_TEAM_BRIEF_CHARS, parse_team_brief


def test_parse_team_brief_trims_and_caps(monkeypatch):
    from agentcore.runtime import context_cap
    from tests.conftest import LogSpy

    brief, err = parse_team_brief("  跨波共识\n第二行  ")
    assert err is None and brief == "跨波共识\n第二行"

    spy = LogSpy()
    monkeypatch.setattr(context_cap, "logger", spy)
    long_text = "字" * (MAX_TEAM_BRIEF_CHARS + 50)
    capped, err = parse_team_brief(long_text, execution_id="exec-brief")
    assert err is None and capped is not None and len(capped) <= MAX_TEAM_BRIEF_CHARS
    fields = spy.get("delegate.context_capped")
    assert fields["site"] == "team_brief"
    assert fields["original_chars"] == MAX_TEAM_BRIEF_CHARS + 50
    assert fields["final_chars"] == len(capped)
    assert fields["execution_id"] == "exec-brief"


def test_parse_team_brief_under_cap_does_not_log(monkeypatch):
    from agentcore.runtime import context_cap
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(context_cap, "logger", spy)
    brief, err = parse_team_brief("短共识")
    assert err is None and brief == "短共识"
    assert not any(name == "delegate.context_capped" for name, _ in spy.events)


def test_parse_team_brief_rejects_non_string():
    assert parse_team_brief(42)[1] is not None
    assert parse_team_brief("   ")[1] is not None


def test_parse_team_brief_none_is_ok():
    brief, err = parse_team_brief(None)
    assert brief is None and err is None
