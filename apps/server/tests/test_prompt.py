"""Tests for system-prompt assembly (`assemble_system_prompt`) and the slim CEO core.

Pins structure only (上下文工程 · 测试守卫原则): XML tags, English tool/field/skill
names, consult pointers, fences, assembly order, date granularity, one-layer-one-place.
Does not pin teaching Chinese. Skill HOW bodies belong in ``test_skills.py``;
this file only asserts those identifiers are absent from the core / compose opening.

The CEO factory core and the shared base are empty unless a residual is injected.
Who to trust is message roles and injection fences.
HOW lives in system Skills / tool descriptions.
"""

import json
import re

from agentcore.runtime.context.consultable import ConsultDirectoryEntry
from agentcore.runtime.resolve.prompt import (
    _ATTACHMENT_MATERIAL_HINT,
    _CEO_CORE_HINT,
    _DEFAULT_SYSTEM_PROMPT,
    assemble_ceo_core,
    assemble_system_prompt,
    attachment_material_scene,
    capability_how_suffix,
    compose_ceo_chat_prompt,
    compose_worker_base_prompt,
    derive_ceo_addon,
)
from agentcore.runtime.resolve.prompt.compose import _on_demand_preamble
from agentcore.runtime.resolve.prompt.memory_rules import _RULES_ROUTING_FENCE
from agentcore.runtime.skills import (
    build_system_skill_registry,
    render_skill_directory,
)
from agentcore.runtime.skills.registry import AUDIENCE_WORKER
from agentcore.tools.builtin.delegate.schema import (
    DELEGATE_DESCRIPTION,
    DELEGATE_PARAMETERS,
    DELEGATE_STAFF_HOW,
    DELEGATE_WHEN,
    NESTED_DELEGATE_DESCRIPTION,
    NESTED_STAFF_HOW,
    TASK_FILL_HOW,
)
from agentcore.tools.builtin.run import run_description

_TASK_PROPS = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]
_HANDBOOK_SIGNATURES = (
    "wait_for",
    "永不代填密码",
    "host(action=os_log)",
    "host(action=status)",
    "host(action=shell)",
)


def _compose_ceo(tool_names: set[str], **kwargs) -> str:
    return compose_ceo_chat_prompt(
        assemble_system_prompt(),
        skill_registry=build_system_skill_registry(),
        ceo_tool_names=tool_names,
        **kwargs,
    )


def test_derive_ceo_addon_splits_shared_prefix_from_full_ceo_prompt():
    base = assemble_system_prompt()
    ceo = _compose_ceo({"delegate", "consult", "ask_user"})
    addon = derive_ceo_addon(base, ceo)
    assert addon
    assert "<文件夹清单>" not in ceo
    assert "<身份>" not in addon
    assert "<按需目录>" in addon
    assert ceo.startswith(base)
    assert addon == ceo[len(base) :].lstrip("\n")
    assert ceo == base + ceo[len(base) :]


def test_shared_base_is_untagged_paragraph():
    out = assemble_system_prompt()
    assert "<身份>" not in out
    assert "</工作区>" not in out
    assert "<运行时>" not in out
    assert "\n\n" not in _DEFAULT_SYSTEM_PROMPT
    assert re.search(
        r"<([a-zA-Z_\u4e00-\u9fff][a-zA-Z0-9_\u4e00-\u9fff]*)>",
        _DEFAULT_SYSTEM_PROMPT,
    ) is None


def test_web_search_not_restated_in_base_tooling():
    assert "web_search" not in assemble_system_prompt()


def test_runtime_context_uses_date_granularity_for_cache_stability():
    from agentcore.runtime.resolve.prompt import render_ceo_turn_envelope, render_runtime_date_block

    block = render_runtime_date_block()
    assert re.search(r"当前日期：\d{4}-\d{2}-\d{2}", block)
    assert not re.search(r"\d{2}:\d{2}:\d{2}", block)
    assert render_runtime_date_block() == block
    env = render_ceo_turn_envelope()
    assert re.search(r"当前日期：\d{4}-\d{2}-\d{2}", env)
    worker = compose_worker_base_prompt(assemble_system_prompt())
    assert re.search(r"当前日期：\d{4}-\d{2}-\d{2}", worker) is None
    from agentcore.runtime.resolve.prompt import render_worker_turn_envelope

    worker_env = render_worker_turn_envelope()
    assert re.search(r"当前日期：\d{4}-\d{2}-\d{2}", worker_env)
    assert "<运行时>" not in assemble_system_prompt()


def test_output_style_survives_memory_and_context_layers():
    out = assemble_system_prompt(
        rules_markdown="- 用户偏好简洁回复",
        extra_context="<附件>...</附件>",
    )
    assert "用户偏好简洁回复" in out
    assert "<附件>" in out
    assert "<设定>" in out and "</设定>" in out
    assert _RULES_ROUTING_FENCE in out


def test_style_precedes_ceo_only_core_when_composed():
    base = assemble_system_prompt()
    ceo = _compose_ceo({"delegate", "consult"})
    assert ceo.startswith(base)
    assert "<身份>" not in ceo
    assert "<按需目录>" in ceo
    assert "<运行时>" not in ceo


def test_capability_how_gated_on_ceo_tool_names():
    """工具侧无 consult 手册；冻结核与 compose 开场都不挂旧手册字。"""
    spine = _CEO_CORE_HINT
    for sig in _HANDBOOK_SIGNATURES:
        assert sig not in spine
    assert capability_how_suffix({"run"}) == ""
    assert capability_how_suffix({"host"}) == ""
    assert capability_how_suffix({"browser"}) == ""
    assert capability_how_suffix({"external_mount_readonly"}) == ""
    run_how = capability_how_suffix({"run"})
    host = capability_how_suffix({"host"})
    browser = capability_how_suffix({"browser"})
    assert "wait_for" not in run_how
    assert "永不代填密码" not in run_how
    assert "wait_for" not in host
    assert "wait_for" not in browser
    assert "delegate" not in host

    for names in (
        {"delegate", "consult"},
        {"delegate", "run", "host", "browser"},
    ):
        prompt = compose_ceo_chat_prompt(
            assemble_system_prompt(),
            ceo_tool_names=names,
        )
        for sig in _HANDBOOK_SIGNATURES:
            assert sig not in prompt
        assert assemble_ceo_core(names) == spine


def test_how_consult_pointer_has_handbook_body():
    """HOW→consult(name) 必须能拉到手册：系统 Skill，或 capability_how_suffix 有正文。"""
    from agentcore.tools.registration import declared_tool_schema, declared_tools

    skills = {s.name for s in build_system_skill_registry().list_all()}
    blobs: list[tuple[str, str]] = [("delegate.nested", NESTED_DELEGATE_DESCRIPTION)]
    for cls in declared_tools():
        schema = declared_tool_schema(cls)
        blob = schema.description + json.dumps(schema.parameters, ensure_ascii=False)
        blobs.append((schema.name, blob))
    empty: list[str] = []
    for tool_name, blob in blobs:
        targets = re.findall(r"HOW→consult\((\w+)\)", blob)
        if targets:
            targets.extend(re.findall(r"、consult\((\w+)\)", blob))
        for target in targets:
            if target in skills or capability_how_suffix({target}).strip():
                continue
            empty.append(f"{tool_name}→{target}")
    assert not empty, f"HOW→consult 指向没有手册的键：{empty}"


def test_consult_hook_lives_only_in_the_core():
    """consult 钩在按需目录 / consult description；场面 HOW 在 skill 正文；目录只写这是什么。"""
    directory = render_skill_directory(
        build_system_skill_registry(),
        {"delegate", "consult", "ask_user", "debate"},
    )
    hint = _CEO_CORE_HINT
    ceo = _compose_ceo({"delegate", "consult", "ask_user", "debate"})
    preamble = "\n".join(_on_demand_preamble())
    assert "consult(name)" not in hint
    assert "<按需目录>" not in hint
    assert "consult(name)" in ceo
    assert "consult(name)" in preamble
    assert "<按需目录>" in preamble and "</按需目录>" not in preamble
    assert ceo.count("<按需目录>") == 1 and ceo.count("</按需目录>") == 1
    assert "page_ui" in directory
    assert "page_ui" in ceo
    assert "HOW→consult" not in hint


def test_delegate_schema_keys_one_layer():
    hint = _CEO_CORE_HINT
    props = DELEGATE_PARAMETERS["properties"]
    assert "depends_on" in _TASK_PROPS
    assert "target_folder_id" in _TASK_PROPS
    assert "artifacts" in _TASK_PROPS
    assert set(props) == {"tasks", "team_brief"}
    for key in (
        "depends_on",
        "target_folder_id",
    ):
        assert key not in hint
        assert key not in DELEGATE_DESCRIPTION


def test_how_identifiers_not_in_resident_core():
    hint = _CEO_CORE_HINT
    for sig in _HANDBOOK_SIGNATURES:
        assert sig not in hint
    for key in (
        "folders",
        "create_folder",
        "mkdir",
        "md_export",
        "consult(name)",
        "consult(browser)",
        "HOW→consult",
        "delegate",
        ".mdc",
        "Cursor",
    ):
        assert key not in hint
    for fence in (
        "【本轮材料收窄】",
        "【已确认约束】",
        "【执行 / 运行 / 打开】",
        "【落盘前对齐】",
        "【对人说】",
    ):
        assert fence not in hint
    assert "【对人说】" not in _DEFAULT_SYSTEM_PROMPT
    assert _RULES_ROUTING_FENCE not in hint
    assert not hint.strip()
    assert "<身份>" not in hint


def test_delegate_when_is_shared_window_bound():
    """根 / 嵌套 delegate 共用 when-to-use 核与编制合同；嵌套另加拆层。"""
    assert DELEGATE_WHEN in DELEGATE_DESCRIPTION
    assert DELEGATE_WHEN in NESTED_DELEGATE_DESCRIPTION
    assert DELEGATE_STAFF_HOW in DELEGATE_DESCRIPTION
    task_desc = _TASK_PROPS["task"]["description"]
    assert task_desc == TASK_FILL_HOW
    assert TASK_FILL_HOW not in DELEGATE_DESCRIPTION
    assert TASK_FILL_HOW not in NESTED_DELEGATE_DESCRIPTION
    assert TASK_FILL_HOW not in _CEO_CORE_HINT
    assert DELEGATE_STAFF_HOW in NESTED_DELEGATE_DESCRIPTION
    assert NESTED_STAFF_HOW in NESTED_DELEGATE_DESCRIPTION
    assert NESTED_STAFF_HOW not in DELEGATE_DESCRIPTION
    assert "HOW→consult" not in DELEGATE_DESCRIPTION
    assert "HOW→consult" not in NESTED_DELEGATE_DESCRIPTION


def test_work_authority_does_not_host_tool_when_to_use():
    shared = _DEFAULT_SYSTEM_PROMPT
    assert "escalate" not in shared
    assert "ask_user" not in shared
    worker = compose_worker_base_prompt(assemble_system_prompt())
    ceo = _compose_ceo({"delegate", "consult", "ask_user"})
    assert "<身份>" not in ceo
    assert "<身份>" not in worker


def test_worker_opening_drops_ceo_orchestration_context():
    """叶子与嵌套 lead 开场都不含编制手册（HOW 在 nested delegate 按钮）。"""
    base = assemble_system_prompt()
    worker_bare = compose_worker_base_prompt(base)
    assert "<按需目录>" not in worker_bare

    reg = build_system_skill_registry()
    leaf_names = {s.name for s in reg.available(set(), audience=AUDIENCE_WORKER)}
    lead_names = {s.name for s in reg.available({"delegate"}, audience=AUDIENCE_WORKER)}
    assert leaf_names == {"data_file_landing", "page_ui"}
    assert lead_names == leaf_names
    worker_dir = compose_worker_base_prompt(
        base,
        on_demand_entries=[
            ConsultDirectoryEntry(name=s.name, summary=s.summary)
            for s in reg.available(set(), audience=AUDIENCE_WORKER)
        ],
    )
    assert "page_ui" in worker_dir
    assert "data_file_landing" in worker_dir
    lead_dir = compose_worker_base_prompt(
        base,
        on_demand_entries=[
            ConsultDirectoryEntry(name=s.name, summary=s.summary)
            for s in reg.available({"delegate"}, audience=AUDIENCE_WORKER)
        ],
    )
    assert "page_ui" in lead_dir


def test_run_description_does_not_ban_curl():
    """公网门是改道，不是禁止句。"""
    for hay in (run_description("server"), run_description("local")):
        assert "不要用 curl" not in hay
        assert "禁止用 curl" not in hay


def test_core_teaches_narrowed_attachment_scope_must_start():
    # 定案 A：场面门：常驻核不载全文，仅本回合有附件块 /
    # [resident missing] 时注入。
    hint = _CEO_CORE_HINT
    assert "【本轮材料收窄】" not in hint
    gated = _ATTACHMENT_MATERIAL_HINT
    assert "<本轮材料>" in gated and "</本轮材料>" in gated
    assert "【本轮材料收窄】" in gated
    assert "[resident missing]" in gated

    from agentcore.runtime.resolve.prompt import render_ceo_turn_envelope

    names = {"consult", "delegate", "ask_user"}
    without = compose_ceo_chat_prompt(
        "BASE",
        ceo_tool_names=names,
    )
    with_flag = render_ceo_turn_envelope(attachment_material=True, include_runtime=False)
    assert "<本轮材料>" not in without
    assert "<本轮材料>" in with_flag
    assert "【本轮材料收窄】" in with_flag
    assert "[resident missing]" in with_flag
    assert attachment_material_scene("<附件>\nfoo\n</附件>") is True
    assert attachment_material_scene("--- File: a.zip [resident missing] ---") is True
    assert attachment_material_scene(None) is False
    assert attachment_material_scene("") is False
    assert attachment_material_scene("<队员点名/>") is False
    assert attachment_material_scene("<钉住条目>\n设定\n</钉住条目>") is False
