"""Unit tests for TurnProfiles (llm/profiles.py)."""

from agentcore.llm.profiles import PROFILES, TurnProfiles, default_turn_profiles, get_profile


def test_turn_profiles_for_turn_uses_credentials_model():
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.profiles import DEEPSEEK_V4_FLASH, turn_profiles_for_turn

    profiles = turn_profiles_for_turn(
        None,
        LLMCredentials(
            api_key="sk",
            base_url="https://api.deepseek.com",
            default_model=DEEPSEEK_V4_FLASH,
        ),
    )
    assert profiles.model_for("chat") == DEEPSEEK_V4_FLASH


def test_default_turn_profiles_uses_platform_model(monkeypatch):
    monkeypatch.setattr("agentcore.config.settings.platform_model", "gpt-5")
    ps = default_turn_profiles()
    assert ps.model == "gpt-5"
    assert ps.get("chat").temperature == PROFILES["chat"].temperature


def test_turn_profiles_agent_uses_single_worker_profile():
    ps = default_turn_profiles(model="test-model")
    assert ps.agent().max_rounds == PROFILES["agent"].max_rounds == 56


def test_turn_profiles_model_overrides():
    ps = TurnProfiles(model="base", model_overrides={"chat": "pro-model"})
    assert ps.model_for("chat") == "pro-model"
    assert ps.model_for("memory") == "base"


def test_turn_profiles_route_model_for_cross_provider_agent():
    ps = TurnProfiles(
        model="ceo",
        model_overrides={"agent": "worker"},
        agent_provider_id="prov-w",
    )
    assert ps.model_for("agent") == "worker"
    assert ps.route_model_for("agent") == "prov-w/worker"
    assert ps.route_model_for("chat") == "ceo"
    # Same provider as turn → no prefix.
    assert ps.route_model_for("agent", turn_provider_id="prov-w") == "worker"


def test_turn_profiles_route_model_for_platform_agent():
    from agentcore.llm.profiles import PLATFORM_PROVIDER_SENTINEL

    ps = TurnProfiles(
        model="ceo",
        model_overrides={"agent": "flash"},
        agent_provider_id=PLATFORM_PROVIDER_SENTINEL,
    )
    assert ps.route_model_for("agent", turn_provider_id="prov-w") == (
        f"{PLATFORM_PROVIDER_SENTINEL}/flash"
    )


def test_get_profile_falls_back_to_chat():
    assert get_profile("unknown").name == "chat"
    assert get_profile("unknown").thinking is True
    assert PROFILES["chat"].thinking is True
    assert PROFILES["agent"].thinking is True
