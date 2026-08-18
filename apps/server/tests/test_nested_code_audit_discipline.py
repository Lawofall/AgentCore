"""Inherited nested code_audit discipline (parent gate → child tasks)."""

from __future__ import annotations

from agentcore.runtime.runs.playbooks.audit import (
    CODE_AUDIT_REQUIRED_SECTIONS,
    CODE_AUDIT_SECTION_BY_DESIGN,
    apply_inherited_code_audit_discipline,
    companion_audit_json_path,
)


def test_companion_audit_json_path():
    assert companion_audit_json_path("a/b.md") == "a/b.audit.json"
    assert companion_audit_json_path("a/b.txt") == "a/b.txt.audit.json"


def test_apply_inherited_stamps_gate_json_and_supplement():
    tasks = apply_inherited_code_audit_discipline(
        [
            {
                "role": "代码审计员",
                "task": "审 simulation",
                "deliverable": {
                    "form": "files",
                    "artifacts": ["AgentCore/文档/reviews/code-audit-2-simulation-sub.md"],
                },
            }
        ]
    )
    assert len(tasks) == 1
    d = tasks[0]["deliverable"]
    assert d["code_audit_gate"] is True
    assert d["artifacts"] == [
        "AgentCore/文档/reviews/code-audit-2-simulation-sub.md",
        "AgentCore/文档/reviews/code-audit-2-simulation-sub.audit.json",
    ]
    assert "嵌套审计·收工" in tasks[0]["system_prompt_supplement"]
    supp = tasks[0]["system_prompt_supplement"]
    assert "骨架先落 → 补全 → 成文" in supp
    assert d["required_sections"] == list(CODE_AUDIT_REQUIRED_SECTIONS)
    assert CODE_AUDIT_SECTION_BY_DESIGN in supp
    # artifacts 声明不变：仍为 [md, companion .audit.json]
    assert d["artifacts"][0].endswith(".md")
    assert d["artifacts"][1].endswith(".audit.json")


def test_apply_inherited_preserves_explicit_gate_false():
    tasks = apply_inherited_code_audit_discipline(
        [
            {
                "role": "x",
                "task": "t",
                "deliverable": {"code_audit_gate": False, "artifacts": ["a.md"]},
            }
        ]
    )
    assert tasks[0]["deliverable"]["code_audit_gate"] is False
    assert "a.audit.json" in tasks[0]["deliverable"]["artifacts"]
    assert tasks[0]["deliverable"].get("required_sections") in (None, [])


def test_apply_inherited_does_not_duplicate_supplement():
    first = apply_inherited_code_audit_discipline(
        [
            {
                "role": "x",
                "task": "t",
                "deliverable": {"artifacts": ["r.md"]},
            }
        ]
    )
    again = apply_inherited_code_audit_discipline(first)
    assert again[0]["system_prompt_supplement"].count("嵌套审计·收工") == 1
    assert again[0]["system_prompt_supplement"].count("审计分栏") == 1


def test_apply_inherited_skips_non_dicts():
    assert apply_inherited_code_audit_discipline(["nope", 1, None]) == []


def test_handwritten_reviews_md_gets_section_contract_without_playbook():
    """CEO 手写路（不传 playbook）：已声明 reviews/ Markdown 也盖上同字面章节契约。"""
    tasks = apply_inherited_code_audit_discipline(
        [
            {
                "role": "记忆审计员",
                "task": "审记忆注入",
                "deliverable": {
                    "form": "files",
                    "artifacts": ["AgentCore/文档/reviews/memory.md"],
                },
            }
        ],
        only_shaped=True,
    )
    d = tasks[0]["deliverable"]
    assert d["required_sections"] == list(CODE_AUDIT_REQUIRED_SECTIONS)
    assert CODE_AUDIT_SECTION_BY_DESIGN in d["required_sections"]
    assert "code_audit_gate" not in d
    assert d["artifacts"] == ["AgentCore/文档/reviews/memory.md"]
    supp = tasks[0]["system_prompt_supplement"]
    assert CODE_AUDIT_SECTION_BY_DESIGN in supp
    assert "嵌套审计·收工" not in supp


def test_handwritten_unrelated_task_not_stamped():
    tasks = apply_inherited_code_audit_discipline(
        [
            {
                "role": "前端",
                "task": "做页面",
                "deliverable": {"form": "files", "artifacts": ["site/index.html"]},
            }
        ],
        only_shaped=True,
    )
    assert "required_sections" not in tasks[0]["deliverable"]
    assert "system_prompt_supplement" not in tasks[0]


def test_handwritten_review_prose_sections_not_overwritten():
    tasks = apply_inherited_code_audit_discipline(
        [
            {
                "role": "审校",
                "task": "审官网",
                "deliverable": {
                    "form": "files",
                    "artifacts": ["AgentCore/文档/reviews/审校报告.md"],
                    "required_sections": ["问题", "建议", "评分"],
                },
            }
        ],
        only_shaped=True,
    )
    assert tasks[0]["deliverable"]["required_sections"] == ["问题", "建议", "评分"]
    assert "system_prompt_supplement" not in tasks[0]


def test_handwritten_stale_four_column_list_upgrades_by_design_column():
    tasks = apply_inherited_code_audit_discipline(
        [
            {
                "role": "审计员",
                "task": "审",
                "deliverable": {
                    "form": "files",
                    "artifacts": ["AgentCore/文档/reviews/x.md"],
                    "required_sections": [
                        "〇、人审速览",
                        "一、属实缺陷",
                        "二、已撤销",
                        "三、观察与工程债",
                    ],
                },
            }
        ],
        only_shaped=True,
    )
    assert tasks[0]["deliverable"]["required_sections"] == list(
        CODE_AUDIT_REQUIRED_SECTIONS
    )


def test_handwritten_code_audit_named_artifact_gets_full_discipline():
    tasks = apply_inherited_code_audit_discipline(
        [
            {
                "role": "审计员",
                "task": "审",
                "deliverable": {
                    "form": "files",
                    "artifacts": ["AgentCore/文档/reviews/code-audit-0-memory.md"],
                },
            }
        ],
        only_shaped=True,
    )
    d = tasks[0]["deliverable"]
    assert d["code_audit_gate"] is True
    assert d["required_sections"] == list(CODE_AUDIT_REQUIRED_SECTIONS)
    assert any(str(p).endswith(".audit.json") for p in d["artifacts"])
    assert "嵌套审计·收工" in tasks[0]["system_prompt_supplement"]
    assert CODE_AUDIT_SECTION_BY_DESIGN in tasks[0]["system_prompt_supplement"]
