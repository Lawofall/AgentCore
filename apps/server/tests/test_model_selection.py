"""P2-A: model_selection strategy entry — model × request-params pairing."""

from __future__ import annotations

from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.model_selection import (
    SelectedCall,
    build_selected_request,
    select_call,
    select_for_scenario,
    select_turn_model,
    turn_profiles_for_turn,
)
from agentcore.llm.profiles import PLATFORM_PROVIDER_SENTINEL, TurnProfiles, get_profile
from agentcore.llm.provider.protocol import LLMMessage


def test_select_call_pairs_model_with_scenario_params():
    selected = select_call("title", "glm-5.2")
    assert selected.model == "glm-5.2"
    assert selected.profile.name == "title"
    assert selected.profile.thinking is False
    assert selected.profile.max_rounds == 1


def test_chat_and_agent_profiles_enable_thinking():
    assert get_profile("chat").thinking is True
    assert get_profile("agent").thinking is True
    req = build_selected_request(
        select_call("chat", "deepseek-v4-flash"),
        [LLMMessage(role="user", content="hi")],
    )
    assert req.thinking is True
    assert req.scenario == "chat"


def test_select_for_scenario_uses_worker_route_prefix():
    turn = TurnProfiles(
        model="ceo-pro",
        model_overrides={"agent": "worker-flash"},
        agent_provider_id="p2",
    )
    selected = select_for_scenario(turn, "agent", turn_provider_id="p1")
    assert selected.model == "p2/worker-flash"
    assert selected.profile.name == "agent"

    chat = select_for_scenario(turn, "chat")
    assert chat.model == "ceo-pro"
    assert chat.profile.name == "chat"


def test_select_for_scenario_platform_worker_sentinel():
    turn = TurnProfiles(
        model="byok-main",
        model_overrides={"agent": "deepseek-v4-flash"},
        agent_provider_id=PLATFORM_PROVIDER_SENTINEL,
    )
    selected = select_for_scenario(turn, "agent", turn_provider_id="p1")
    assert selected.model == f"{PLATFORM_PROVIDER_SENTINEL}/deepseek-v4-flash"


def test_build_selected_request_packs_without_extra_strategy():
    selected = SelectedCall(model="m1", profile=get_profile("compaction"))
    req = build_selected_request(
        selected,
        [LLMMessage(role="user", content="hi")],
        stream=False,
    )
    assert req.model == "m1"
    assert req.temperature == selected.profile.temperature
    assert req.thinking is False
    assert req.stream is False
    assert req.scenario == "compaction"


def test_select_turn_model_priority():
    creds = LLMCredentials(
        api_key="k", base_url="http://x", default_model="account-model", source="user"
    )
    assert select_turn_model(creds, conversation_model="picked") == "picked"
    assert select_turn_model(creds, conversation_model="   ") == "account-model"
    assert select_turn_model(None)  # falls back to platform / flash


def test_turn_profiles_for_turn_uses_credentials_model():
    creds = LLMCredentials(
        api_key="k", base_url="http://x", default_model="user-model", source="user"
    )
    profiles = turn_profiles_for_turn(None, creds)
    assert profiles.model == "user-model"
