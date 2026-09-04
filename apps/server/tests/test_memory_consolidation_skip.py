"""Unit pins: abnormal-turn skip gate + preference-prompt hardening."""

from agentcore.memory.consolidation import abnormal_turn_skip_reason
from agentcore.memory.episodic import _EPISODIC_SYSTEM
from agentcore.memory.semantic import _SEMANTIC_SYSTEM_PROMPT


def test_skip_cancelled_and_incomplete():
    assert (
        abnormal_turn_skip_reason(
            usage={"status": "incomplete", "finish_reason": "cancelled", "incomplete": True},
            content="半成品",
            has_assistant=True,
        )
        == "incomplete"
    )
    assert (
        abnormal_turn_skip_reason(
            usage={"status": "incomplete", "finish_reason": "cancelled"},
            content="半成品",
            has_assistant=True,
        )
        == "status:incomplete"
    )
    assert (
        abnormal_turn_skip_reason(
            usage={"status": "complete", "finish_reason": "cancelled"},
            content="半成品",
            has_assistant=True,
        )
        == "finish_reason:cancelled"
    )


def test_skip_running_empty_and_no_assistant():
    assert (
        abnormal_turn_skip_reason(
            usage={"status": "running"},
            content="",
            has_assistant=True,
        )
        == "status:running"
    )
    assert (
        abnormal_turn_skip_reason(
            usage={"status": "complete", "finish_reason": "end_turn"},
            content="   ",
            has_assistant=True,
        )
        == "empty_assistant"
    )
    assert (
        abnormal_turn_skip_reason(usage=None, content=None, has_assistant=False)
        == "no_assistant"
    )


def test_end_turn_with_content_is_eligible():
    assert (
        abnormal_turn_skip_reason(
            usage={"status": "complete", "finish_reason": "end_turn"},
            content="调研结论如下。",
            has_assistant=True,
        )
        is None
    )


def test_episodic_prompt_forbids_inferring_preference_from_task_genre():
    text = _EPISODIC_SYSTEM
    assert "explicit statements" in text or "explicit" in text.lower()
    assert "must NOT" in text or "Do NOT infer" in text
    assert "禁止从本场任务题材、体裁、一次性诉求形状推断沟通偏好" in text


def test_episodic_verified_facts_only_when_folder_bound():
    from agentcore.memory.episodic import episodic_system_prompt

    folder = episodic_system_prompt(allow_verified_facts=True)
    naked = episodic_system_prompt(allow_verified_facts=False)
    assert "## 本场证实的项目事实" in folder
    assert "Folder-bound" in folder
    assert "## 本场证实的项目事实" not in naked
    assert "no bound" in naked.lower() or "Folder-bound: no" in naked


def test_semantic_prompt_tightens_preferences_promotion():
    text = _SEMANTIC_SYSTEM_PROMPT
    assert "Preference promotion rule" in text
    assert "explicit user" in text.lower() or "explicit" in text.lower()
    assert "NEVER promote task topics" in text or "never promote" in text.lower()
    assert "禁止从本场任务题材、体裁、一次性诉求形状推断沟通偏好" in text


def test_semantic_prompt_domain_split_keeps_genre_out_of_preferences():
    """题材/领域偏好不得留在偏好.md；主题不是本场巩固的出口。"""
    text = _SEMANTIC_SYSTEM_PROMPT
    assert "Domain split" in text
    assert "communication style" in text.lower() or "工作习惯" in text
    assert "must NOT stay in 偏好.md" in text or "不得" in text
    assert "not this pass" in text
    assert '"ops"' not in text


def test_semantic_prompt_cross_topic_bar_keeps_one_shot_out_of_profile():
    text = _SEMANTIC_SYSTEM_PROMPT
    assert "Cross-topic rule" in text
    assert "UNRELATED topic" in text
    assert "One-shot lookups" in text
    assert "AppData" in text
    assert "must not fill 画像 from a one-off ask" in text


def test_semantic_user_prompt_no_folder_does_not_park_host_paths_in_profile():
    from agentcore.memory.episode_store import EpisodeRecord
    from agentcore.memory.semantic import SemanticConsolidateInput, _render_semantic_prompt

    text = _render_semantic_prompt(
        SemanticConsolidateInput(
            user_id="u1",
            episodes=[
                EpisodeRecord(
                    id="e1",
                    conversation_id="c1",
                    summary="查了直播伴侣日志",
                    created_at="2026-09-04T00:00:00+00:00",
                )
            ],
        )
    )
    assert "No current folder" in text
    assert "host/shell" in text
    assert "global 画像" in text
