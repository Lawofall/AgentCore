"""ask_user option ``action`` normalize + schema advertising."""

import json

import pytest

from agentcore.runtime.events import EventSink
from agentcore.tools.builtin.ask_user.schema import (
    ListArgError,
    normalize_assumptions,
    normalize_options,
    normalize_questions,
    option_label_is_recommended,
)
from agentcore.tools.builtin.ask_user.tool import AskUserTool


def test_normalize_options_preserves_bind_local_folder_action():
    out = normalize_options(
        [
            {"label": "打开本地项目", "action": "open_local_project"},
            {"label": "登记本地项目", "action": "register_local_project"},
            {"label": "绑定本机执行环境", "action": "bind_local_folder"},
            {"label": "继续用云端", "detail": "无法打开本机应用"},
            {"label": "坏动作", "action": "hack_the_planet"},
            {"label": "授权只读目录", "action": "grant_readonly_folder"},
            {"label": "授权整理目录", "action": "grant_organize_folder"},
            {"label": "加入可读写", "action": "grant_attach_folder"},
        ],
        max_options=10,
    )
    assert out[0]["action"] == "open_local_project"
    assert "recommended" not in out[0]
    assert out[1]["action"] == "register_local_project"
    assert out[2]["action"] == "bind_local_folder"
    assert "action" not in out[3]
    assert "detail" not in out[3]  # 普通短问丢掉第二句
    assert "action" not in out[4]  # unknown actions drop
    assert "action" not in out[5]  # grant_readonly_folder dropped
    assert out[6]["action"] == "grant_organize_folder"
    assert out[7]["action"] == "grant_attach_folder"


def test_normalize_options_passthrough_well_known_and_target_name():
    out = normalize_options(
        [
            {
                "label": "授权桌面",
                "action": "grant_organize_folder",
                "well_known": "desktop",
                "target_name": "咨询报告",
            },
            {
                "label": "授权下载",
                "action": "grant_organize_folder",
                "well_known": "Downloads",  # case-insensitive
                "target_name": "foo.zip",
            },
            {
                "label": "坏路径名",
                "action": "grant_organize_folder",
                "well_known": "documents",
                "target_name": "a/b",
            },
            {
                "label": "未知 well_known",
                "action": "grant_organize_folder",
                "well_known": "home",
                "target_name": "ok",
            },
            {
                "label": "非 grant 不透传",
                "action": "bind_local_folder",
                "well_known": "desktop",
                "target_name": "x",
            },
            {
                "label": "反斜杠拒绝",
                "action": "grant_organize_folder",
                "well_known": "desktop",
                "target_name": r"a\b",
            },
            {
                "label": "绝对 path",
                "action": "grant_organize_folder",
                "path": r"D:\新建文件夹\资料",
            },
            {
                "label": "相对 path 丢",
                "action": "grant_organize_folder",
                "path": "relative/folder",
            },
        ],
        max_options=10,
    )
    assert out[0]["well_known"] == "desktop"
    assert out[0]["target_name"] == "咨询报告"
    assert out[1]["well_known"] == "downloads"
    assert out[1]["target_name"] == "foo.zip"
    assert out[2]["well_known"] == "documents"
    assert "target_name" not in out[2]  # path separators rejected
    assert "well_known" not in out[3]
    assert out[3]["target_name"] == "ok"
    assert "well_known" not in out[4]
    assert "target_name" not in out[4]
    assert "target_name" not in out[5]
    assert out[6]["path"] == r"D:\新建文件夹\资料"
    assert "path" not in out[7]


def test_normalize_options_drops_grant_readonly_folder_and_hints():
    out = normalize_options(
        [
            {
                "label": "授权只读目录",
                "action": "grant_readonly_folder",
                "well_known": "desktop",
                "target_name": "咨询报告",
                "path": r"D:\新建文件夹\资料",
            },
        ]
    )
    assert out == [{"label": "授权只读目录"}]


def test_normalize_options_drops_detail_by_default():
    out = normalize_options(
        [
            {"label": "方案 A：先出契约", "detail": "慢但稳"},
            {"label": "方案 B：先一条主路径", "detail": "快但窄"},
        ]
    )
    assert [o["label"] for o in out] == ["方案 A：先出契约", "方案 B：先一条主路径"]
    assert all("detail" not in o for o in out)


def test_normalize_options_keeps_detail_when_flagged():
    out = normalize_options(
        [{"label": "方案甲", "detail": "一行取舍"}],
        keep_detail=True,
    )
    assert out[0]["detail"] == "一行取舍"


def test_normalize_options_drops_detail_but_keeps_grant_hints():
    """通用卡「将整理：…」由前端合成；丢掉第二句不得误删 grant 字段。"""
    out = normalize_options(
        [
            {
                "label": "允许整理桌面",
                "detail": "将整理：桌面上的咨询报告",
                "action": "grant_organize_folder",
                "well_known": "desktop",
                "target_name": "咨询报告",
            }
        ]
    )
    assert "detail" not in out[0]
    assert out[0]["action"] == "grant_organize_folder"
    assert out[0]["well_known"] == "desktop"
    assert out[0]["target_name"] == "咨询报告"


def test_normalize_questions_passthrough_to_checkpoint_shape():
    qs = normalize_questions(
        [
            {
                "prompt": "如何对齐工作区？",
                "kind": "choice",
                "options": [
                    {"label": "绑定本地文件夹", "action": "bind_local_folder"},
                    {"label": "先用云端"},
                ],
            }
        ]
    )
    assert qs[0]["options"][0]["action"] == "bind_local_folder"
    assert "action" not in qs[0]["options"][1]


def test_normalize_questions_promotes_question_level_action_to_default_option_only():
    """Q-level action must not blanket-fill a sibling skip/oral option."""
    qs = normalize_questions(
        [
            {
                "prompt": "如何登记本地项目？",
                "kind": "choice",
                "action": "register_local_project",
                "default": "登记并打开",
                "options": [
                    {"label": "登记并打开"},
                    {"label": "不落盘，只在对话里汇报"},
                ],
            }
        ]
    )
    assert qs[0]["options"][0]["action"] == "register_local_project"
    assert "action" not in qs[0]["options"][1]
    assert "action" not in qs[0]


def test_normalize_questions_promotes_question_level_action_to_first_without_default():
    qs = normalize_questions(
        [
            {
                "prompt": "如何登记本地项目？",
                "kind": "choice",
                "action": "register_local_project",
                "options": [
                    {"label": "登记并打开（推荐）"},
                    {"label": "不落盘，只在对话里汇报"},
                ],
            }
        ]
    )
    assert qs[0]["options"][0]["action"] == "register_local_project"
    assert "action" not in qs[0]["options"][1]


def test_normalize_questions_promotes_question_level_action_to_sole_option():
    qs = normalize_questions(
        [
            {
                "prompt": "登记？",
                "kind": "choice",
                "action": "register_local_project",
                "options": [{"label": "登记为本地项目"}],
            }
        ]
    )
    assert qs[0]["options"][0]["action"] == "register_local_project"


def test_normalize_questions_promotes_question_level_action_to_first_of_ambiguous_multi_option():
    qs = normalize_questions(
        [
            {
                "prompt": "如何登记本地项目？",
                "kind": "choice",
                "action": "register_local_project",
                "options": [
                    {"label": "登记并打开"},
                    {"label": "仅登记"},
                ],
            }
        ]
    )
    assert qs[0]["options"][0]["action"] == "register_local_project"
    assert "action" not in qs[0]["options"][1]


def test_normalize_questions_does_not_overwrite_option_action():
    qs = normalize_questions(
        [
            {
                "prompt": "如何对齐工作区？",
                "kind": "choice",
                "action": "register_local_project",
                "default": "登记新项目",
                "options": [
                    {"label": "打开已有项目", "action": "open_local_project"},
                    {"label": "登记新项目"},
                ],
            }
        ]
    )
    assert qs[0]["options"][0]["action"] == "open_local_project"  # preserved
    assert qs[0]["options"][1]["action"] == "register_local_project"  # promoted via default


def test_normalize_questions_drops_unknown_question_level_action():
    qs = normalize_questions(
        [
            {
                "prompt": "选一项？",
                "kind": "choice",
                "action": "hack_the_planet",
                "options": [
                    {"label": "A"},
                    {"label": "B"},
                ],
            }
        ]
    )
    assert "action" not in qs[0]["options"][0]
    assert "action" not in qs[0]["options"][1]
    assert "action" not in qs[0]


def test_normalize_questions_text_ignores_question_level_action():
    qs = normalize_questions(
        [
            {
                "prompt": "补充说明？",
                "kind": "text",
                "action": "register_local_project",
            }
        ]
    )
    assert qs[0]["kind"] == "text"
    assert qs[0]["options"] == []
    assert "action" not in qs[0]


def test_normalize_questions_accepts_json_encoded_array_string():
    """Model double-encoding: questions arrives as a JSON array string, not a list."""
    payload = [
        {
            "prompt": "篇幅？",
            "kind": "choice",
            "options": [{"label": "短"}, {"label": "中"}, {"label": "长"}],
        },
        {"prompt": "受众？", "kind": "text"},
    ]
    qs = normalize_questions(json.dumps(payload, ensure_ascii=False))
    assert len(qs) == 2
    assert qs[0]["prompt"] == "篇幅？"
    assert len(qs[0]["options"]) == 3
    assert qs[1]["kind"] == "text"


def test_normalize_options_accepts_json_encoded_array_string():
    opts = normalize_options(json.dumps([{"label": "A"}, {"label": "B"}], ensure_ascii=False))
    assert [o["label"] for o in opts] == ["A", "B"]


def test_normalize_questions_rejects_non_array_json_string():
    with pytest.raises(ListArgError, match="questions"):
        normalize_questions('{"prompt": "不是数组"}')


def test_normalize_questions_rejects_garbage_string():
    with pytest.raises(ListArgError, match="questions"):
        normalize_questions("[{broken")


async def test_ask_user_rejects_unparseable_questions_string():
    """Garbage string must fail the tool — not open an empty-option kickoff card."""
    tool = AskUserTool(
        sink=EventSink(),
        conversation_id="c1",
        timeout_seconds=1.0,
    )
    from pathlib import Path

    from agentcore.tools.protocol import ToolContext
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c1",
    )
    res = await tool.execute(
        {"message": "对齐一下方向", "questions": "[{broken"},
        ctx,
    )
    assert res.success is False
    assert res.error and "questions" in res.error
    assert "数组" in (res.error or "")


def test_normalize_options_accepts_recommendation_in_label():
    out = normalize_options(
        [
            {"label": "方案一：同风格精修（推荐）", "recommended": True},
            {"label": "方案二：风格重塑"},
        ]
    )
    assert out[0]["label"] == "方案一：同风格精修（推荐）"
    assert "recommended" not in out[0]
    assert "recommended" not in out[1]
    assert option_label_is_recommended(out[0]["label"])
    assert not option_label_is_recommended(out[1]["label"])


def test_normalize_options_allows_tuijian_substring_in_product_name():
    """「推荐」作为产品名子串（非「（推荐）」标记）不是倾向标记。"""
    out = normalize_options([{"label": "推荐算法选型", "recommended": True}])
    assert out[0]["label"] == "推荐算法选型"
    assert "recommended" not in out[0]
    assert not option_label_is_recommended(out[0]["label"])


def test_normalize_options_accepts_english_recommended_mark():
    out = normalize_options([{"label": "Option A (recommended)"}])
    assert out[0]["label"] == "Option A (recommended)"
    assert "recommended" not in out[0]
    assert option_label_is_recommended(out[0]["label"])


def test_normalize_assumptions_keeps_short_label():
    out = normalize_assumptions(
        [
            {"label": "范围", "value": "国内三家"},
            {"label": "本周=周一至周日", "value": ""},
        ]
    )
    assert out == [
        {"id": "a0", "label": "范围", "value": "国内三家"},
        {"id": "a1", "label": "本周=周一至周日", "value": ""},
    ]


def test_normalize_assumptions_merges_long_label_into_value():
    out = normalize_assumptions(
        [{"label": "调研覆盖的默认范围", "value": "国内三家"}]
    )
    assert out == [
        {"id": "a0", "label": "假设", "value": "调研覆盖的默认范围：国内三家"},
    ]


def test_normalize_assumptions_long_label_empty_value():
    out = normalize_assumptions([{"label": "调研覆盖的默认范围", "value": ""}])
    assert out == [
        {"id": "a0", "label": "假设", "value": "调研覆盖的默认范围"},
    ]


def test_normalize_assumptions_merges_inventory_label():
    out = normalize_assumptions(
        [
            {
                "label": "律所名称、简介、业务领域、联系方式",
                "value": "先用专业占位内容搭建，你提供真实资料后随时替换",
            }
        ]
    )
    assert out == [
        {
            "id": "a0",
            "label": "假设",
            "value": "律所名称、简介、业务领域、联系方式：先用专业占位内容搭建，你提供真实资料后随时替换",
        }
    ]


async def test_ask_user_accepts_recommendation_in_label():
    tool = AskUserTool(
        sink=EventSink(),
        conversation_id="c1",
        timeout_seconds=1.0,
    )
    from pathlib import Path

    from agentcore.tools.protocol import ToolContext
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c1",
    )
    res = await tool.execute(
        {
            "message": "选一下方向",
            "questions": [
                {
                    "prompt": "采用哪个方案？",
                    "kind": "choice",
                    "options": [
                        {"label": "同风格精修（推荐）"},
                        {"label": "风格重塑"},
                    ],
                }
            ],
        },
        ctx,
    )
    # This fixture has no durable frame; persist fails after normalize succeeds.
    assert "推荐标记" not in (res.error or "")
    assert "推荐标记" not in (res.output or "")


def test_ask_user_schema_points_at_recommendation_in_label():
    tool = AskUserTool(
        sink=EventSink(),
        conversation_id="c1",
        timeout_seconds=30.0,
    )
    props = tool.schema.parameters["properties"]["questions"]["items"]["properties"]["options"][
        "items"
    ]["properties"]
    assert "recommended" not in props
    assert "（推荐）" in props["label"]["description"]
    assert "放第一" in props["label"]["description"]
    assert "禁止" not in props["label"]["description"]
    assert "organize_plan" in props["detail"]["description"]
    assert "daily_review" in props["detail"]["description"]
    assert "普通" in props["detail"]["description"]


def test_ask_user_schema_advertises_action_only_when_flagged():
    sink = EventSink()
    base = dict(
        sink=sink,
        conversation_id="c1",
        timeout_seconds=30.0,
    )
    plain = AskUserTool(**base, advertise_bind_local_folder=False)
    props = plain.schema.parameters["properties"]["questions"]["items"]["properties"]["options"][
        "items"
    ]["properties"]
    assert "action" not in props
    assert "well_known" not in props
    assert "target_name" not in props
    assert "bind_local_folder" not in plain.schema.description

    advertised = AskUserTool(**base, advertise_bind_local_folder=True)
    props2 = advertised.schema.parameters["properties"]["questions"]["items"]["properties"][
        "options"
    ]["items"]["properties"]
    assert props2["action"]["enum"] == [
        "open_local_project",
        "register_local_project",
        "bind_local_folder",
        "grant_organize_folder",
        "grant_attach_folder",
    ]
    assert props2["well_known"]["enum"] == ["desktop", "downloads", "documents"]
    assert "target_name" in props2
    assert "path" in props2
    assert "open_local_project" in advertised.schema.description or "open/register/bind" in advertised.schema.description
    assert "register_local_project" in advertised.schema.description or "open/register/bind" in advertised.schema.description
    assert "bind_local_folder" in advertised.schema.description or "bind_local_*" in advertised.schema.description
    assert "grant_readonly_folder" not in advertised.schema.description
    assert "grant_organize_folder" in advertised.schema.description
    assert "external_mount_readonly" in advertised.schema.description
    assert "HOW→consult(ask_user_kickoff" in advertised.schema.description
    assert "grant_attach_folder" in advertised.schema.description
    assert "只读用" in advertised.schema.description
    assert "改导" not in advertised.schema.description
    # 口头同意闭环 / 歧义 2～3 候选怎么填：HOW 在 skill，不进工具 description。
    assert "口头同意" not in advertised.schema.description
    assert "2～3" not in advertised.schema.description
    assert "2-3" not in advertised.schema.description
    action_desc = props2["action"]["description"]
    assert "open_local_project" in action_desc or "open/register/bind" in action_desc
    assert "register_local_project" in action_desc or "open/register/bind" in action_desc
    assert "本机传统" in action_desc or "非默认" in action_desc
    assert "改导" not in action_desc
    assert "bind_local_folder" in action_desc or "open/register/bind" in action_desc
    assert "grant_readonly_folder" not in action_desc
    assert "grant_readonly_folder" not in props2["action"]["enum"]
    assert "grant_organize_folder=整理" in action_desc
    assert "grant_attach_folder=本机可写" in action_desc
    assert "口头同意" not in action_desc
    assert "2～3" not in action_desc
    assert "2-3" not in action_desc
    assert "选择器兜底" not in props2["well_known"]["description"]
    assert "picker" not in props2["target_name"]["description"].lower()
    # Desktop advertise must stay compact (dogfood ~3796 before slim); HOW → skill.
    adv_blob = advertised.schema.description + json.dumps(
        advertised.schema.parameters, ensure_ascii=False
    )
    plain_blob = plain.schema.description + json.dumps(
        plain.schema.parameters, ensure_ascii=False
    )
    assert len(adv_blob) < 3600, f"desktop ask_user schema too fat: {len(adv_blob)}"
    assert len(plain_blob) < len(adv_blob)
    assert abs(len(plain_blob) - 1661) < 80  # non-desktop path must not inflate


def test_ask_user_organize_how_lives_in_skill():
    """口头同意闭环 / 歧义 2～3 候选：HOW 钉 consult skill，不进工具 description。"""
    from agentcore.runtime.skills import build_system_skill_registry

    registry = build_system_skill_registry()
    kickoff = registry.get("ask_user_kickoff")
    mid = registry.get("ask_user_midtask")
    assert kickoff is not None
    assert mid is not None
    from agentcore.runtime.resolve.prompt import capability_how_suffix

    granted = capability_how_suffix({"external_mount_readonly"})
    assert "口头同意" in granted
    assert "grant_organize_folder" in granted
    assert "grant_organize_folder" in mid.body
    assert "consult(external_mount_readonly)" in mid.body
