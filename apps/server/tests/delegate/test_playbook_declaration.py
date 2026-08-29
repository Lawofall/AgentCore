"""Playbook declaration gate: structure only; no intent hard-reject on none."""

from agentcore.runtime.delegate.playbook_declaration import (
    declaration_reject_gate,
    resolve_playbook_declaration,
    try_declaration_reject_gate,
)
from tests.delegate.conftest import Provider, ctx, tool


def test_declaration_reject_gate_helpers():
    assert declaration_reject_gate("delegate 须传手写 `tasks`，其余…") == "empty"
    assert declaration_reject_gate("未知 playbook『x』") == "unknown"
    from agentcore.runtime.delegate.playbook_declaration import (
        HANDWRITTEN_PLAYBOOK_ARGS_MSG,
        PLAYBOOK_TASKS_XOR_MSG,
    )

    assert declaration_reject_gate(PLAYBOOK_TASKS_XOR_MSG) == "xor"
    assert declaration_reject_gate(HANDWRITTEN_PLAYBOOK_ARGS_MSG) == "xor"
    # try_* is None for non-declaration errors (normalize must not bucket as unknown).
    assert try_declaration_reject_gate("缺少必填参数：query") is None
    assert try_declaration_reject_gate(None) is None
    assert try_declaration_reject_gate("未知 playbook『x』") == "unknown"
    assert try_declaration_reject_gate("delegate 缺 tasks/playbook：…") == "empty"


def test_resolve_playbook_xor_tasks_rejected():
    """具名 playbook + 非空 tasks → 声明闸拒收（不进入 expand）。"""
    from agentcore.runtime.delegate.playbook_declaration import PLAYBOOK_TASKS_XOR_MSG

    name, err = resolve_playbook_declaration(
        {
            "playbook": "lens_crosscheck",
            "playbook_args": {"topic": "X"},
            "tasks": [{"role": "a", "task": "b"}],
        }
    )
    assert name is None
    assert err == PLAYBOOK_TASKS_XOR_MSG
    assert "continue_from_run_id" in PLAYBOOK_TASKS_XOR_MSG
    assert "调查批" in PLAYBOOK_TASKS_XOR_MSG


def test_resolve_handwritten_with_playbook_args_rejected():
    from agentcore.runtime.delegate.playbook_declaration import (
        HANDWRITTEN_PLAYBOOK_ARGS_MSG,
    )

    name, err = resolve_playbook_declaration(
        {
            "playbook_args": {"topic": "should not appear"},
            "tasks": [{"role": "a", "task": "写报告"}],
        }
    )
    assert name is None
    assert err == HANDWRITTEN_PLAYBOOK_ARGS_MSG


def test_resolve_handwritten_without_playbook_ok():
    """自由组队：不传 playbook，直接手写 tasks → 过。"""
    name, err = resolve_playbook_declaration(
        {"tasks": [{"role": "a", "task": "调研并写报告"}]}
    )
    assert err is None
    assert name is None


def test_resolve_none_without_reason_ok():
    """省略 playbook + 手写 tasks → 过（与显式 none 同义）。"""
    name, err = resolve_playbook_declaration(
        {
            "tasks": [{"role": "a", "task": "b"}],
        }
    )
    assert err is None
    assert name is None


def test_resolve_none_with_legacy_reason_ignored():
    name, err = resolve_playbook_declaration(
        {
            "playbook_none_reason": "机械单步改一句文案",
            "tasks": [{"role": "a", "task": "b"}],
        }
    )
    assert err is None
    assert name is None


def test_resolve_named_playbook_ok():
    name, err = resolve_playbook_declaration(
        {"playbook": "cite_write_review", "playbook_args": {"topic": "X"}}
    )
    assert err is None
    assert name == "cite_write_review"


def test_named_build_website_is_unknown():
    """具名建站快捷已撤：走未知 playbook，拒文不推荐该套餐。"""
    name, err = resolve_playbook_declaration(
        {"playbook": "build_website", "playbook_args": {"topic": "X"}}
    )
    assert name is None
    assert err is not None
    assert "未知" in err
    assert "已下线" not in err
    assert "建站推荐" not in err
    assert "手写" in err
    assert "一人一页" in err
    assert "内部模板" in err
    assert "build_app" not in err
    assert "可用：" not in err
    assert declaration_reject_gate(err) == "unknown"


def test_resolve_empty_delegate_rejected():
    """无 tasks 且无具名 playbook → 拒（短文案含可抄 tasks 骨架，不倾倒 playbook 全家桶）。"""
    from agentcore.runtime.delegate.playbook_declaration import (
        _EMPTY_DELEGATE_MSG,
        HANDWRITTEN_TASKS_SKELETON,
    )
    from agentcore.runtime.runs.playbooks import available_playbooks

    name, err = resolve_playbook_declaration({})
    assert name is None
    assert err == _EMPTY_DELEGATE_MSG
    assert "tasks" in err
    assert "默认" in err
    assert HANDWRITTEN_TASKS_SKELETON in err
    assert '"role"' in err and '"task"' in err
    assert "deliverable" in err
    # 空失败弱化嵌套 arguments / 长纠错叙事；playbook 仅次选一句。
    assert "arguments" not in err
    assert "次选" in err and "playbook" in err
    # Must not dump the full playbook catalog into every empty reject.
    assert available_playbooks() not in err
    assert "build_toolshed" not in err
    assert declaration_reject_gate(err) == "empty"
    assert declaration_reject_gate("delegate 缺 tasks/playbook：…") == "empty"


def test_website_intent_none_allowed():
    """建站意图 + none → 放行（不再硬拒；软引导靠 skill/schema）。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook_none_reason": "build_website 未在目录确认，手写内容+前端两阶段构建官网",
            "tasks": [
                {"role": "内容策略师", "task": "撰写官网文案"},
                {"role": "前端工程师", "task": "构建完整官网页面"},
            ],
        },
    )
    assert err is None
    assert name is None


def test_website_intent_handwritten_without_declaration_allowed():
    """建站意图 + 缺省手写 tasks（不传 playbook）→ 放行。"""
    name, err = resolve_playbook_declaration(
        {
            "tasks": [
                {"role": "文案", "task": "写落地页文案"},
                {"role": "前端", "task": "实现落地页 HTML"},
            ],
        },
    )
    assert err is None
    assert name is None


def test_website_intent_none_from_user_message_alone_allowed():
    """User turn clearly asks to build a site → vague none still allowed."""
    name, err = resolve_playbook_declaration(
        {
            "playbook_none_reason": "自定义拆法",
            "tasks": [{"role": "工程师", "task": "按要求交付"}],
        },
    )
    assert err is None
    assert name is None


def test_website_intent_named_build_website_unknown():
    """建站意图 + 具名已撤套餐 → 未知 playbook（手写 tasks 仍可）。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook": "build_website",
            "playbook_args": {"topic": "面向中小商家的 GEO 营销官网"},
        },
    )
    assert name is None
    assert err is not None
    assert "未知" in err
    assert "已下线" not in err
    assert "建站推荐" not in err
    assert "一人一页" in err
    assert "build_app" not in err
    assert "可用：" not in err


def test_toolshed_intent_none_allowed():
    """控制台意图 + none → 放行（不再硬拒）。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook_none_reason": "手写内容+前端两阶段构建控制台",
            "tasks": [
                {"role": "内容", "task": "撰写控制台文案"},
                {"role": "前端", "task": "实现管理后台页面"},
            ],
        },
    )
    assert err is None
    assert name is None


def test_toolshed_intent_named_build_website_style_unknown():
    name, err = resolve_playbook_declaration(
        {
            "playbook": "build_website",
            "playbook_args": {"topic": "订单运营控制台", "style": "toolshed"},
        },
    )
    assert name is None
    assert err is not None
    assert "未知" in err
    assert "已下线" not in err
    assert "建站推荐" not in err
    assert "一人一页" in err
    assert "build_app" not in err
    assert "可用：" not in err


def test_legacy_build_toolshed_playbook_unknown():
    """旧名直接未知 playbook——无别名 / 静默改写。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook": "build_toolshed",
            "playbook_args": {"topic": "订单运营控制台"},
        },
    )
    assert name is None
    assert err is not None
    assert "未知" in err
    assert "build_toolshed" in err
    assert "可用：" in err
    assert "绿场推荐" not in err
    assert "绿场软件推荐" not in err


def test_automation_delivery_ignored_named_playbooks_still_ok():
    """场面账拆除：具名 playbook 不再因交付形态拒。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook": "cite_write_review",
            "playbook_args": {"topic": "Ops"},
        },
    )
    assert err is None
    assert name == "cite_write_review"


def test_automation_console_allows_named_remaining_playbook():
    name, err = resolve_playbook_declaration(
        {
            "playbook": "lens_crosscheck",
            "playbook_args": {"topic": "Ops"},
        },
    )
    assert err is None
    assert name == "lens_crosscheck"


def test_automation_plan_allows_website():
    name_w, err_w = resolve_playbook_declaration(
        {"playbook": "build_website", "playbook_args": {"topic": "X"}},
    )
    assert name_w is None
    assert err_w is not None
    assert "未知" in err_w
    assert "建站推荐" not in err_w


def test_automation_runnable_allows_toolshed_shaped_none():
    """控制台形手写 none 仍可（不强制已撤站点套餐）。"""
    name, err = resolve_playbook_declaration(
        {
            "tasks": [
                {"role": "工程师", "task": "搭运营控制台自动化流水线"},
            ],
        },
    )
    assert err is None
    assert name is None


def test_non_website_none_still_ok():
    """明显非建站 + none → 仍可。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook_none_reason": "机械单步改一句配置",
            "tasks": [{"role": "工程师", "task": "把超时改成 30s"}],
        },
    )
    assert err is None
    assert name is None


def test_research_handwritten_no_prefer_pressure():
    """调研意图手写 tasks：可过；拒文/路径不再强推 cite_write_review。"""
    name, err = resolve_playbook_declaration(
        {
            "tasks": [
                {"role": "调研员", "task": "写实务研究报告"},
                {"role": "写作者", "task": "成篇", "depends_on": ["调研员"]},
            ],
        },
    )
    assert err is None
    assert name is None
    name2, err2 = resolve_playbook_declaration(
        {
            "playbook": "cite_write_review",
            "playbook_args": {"topic": "起诉第三者立案"},
        },
    )
    assert err2 is None
    assert name2 == "cite_write_review"


def test_legacy_playbook_ids_are_unknown():
    """旧四名直接未知——无别名 / 静默改写。"""
    for pid, args in (
        ("parallel_brief", {"topic": "X", "angles": ["甲", "乙"]}),
        ("research_report", {"topic": "X"}),
        ("multi_lens_research", {"topic": "X"}),
        ("repair_code", {"problem": "x", "verify": "pytest"}),
    ):
        name, err = resolve_playbook_declaration({"playbook": pid, "playbook_args": args})
        assert name is None
        assert err is not None
        assert "未知" in err
        assert "暂未列入" not in err
        assert "绿场推荐" not in err
        assert "绿场软件推荐" not in err
        assert declaration_reject_gate(err) == "unknown"


def test_website_followup_audit_none_ok():
    """审计/修复帧 + none → 放行。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook_none_reason": "质量敏感成品独立审计，1 名审计员覆盖前端+文案",
            "tasks": [
                {
                    "role": "审计员",
                    "task": "对 GEO 官网的两个交付物进行独立审计并出报告",
                }
            ],
        },
    )
    assert err is None
    assert name is None


def test_build_website_verify_named_unknown():
    """Second-act verify shortcut retired — declaration treats as unknown."""
    name, err = resolve_playbook_declaration(
        {
            "playbook": "build_website_verify",
            "playbook_args": {"topic": "GEO 官网"},
        },
    )
    assert name is None
    assert err is not None
    assert "未知" in err
    assert "已下线" not in err
    assert "建站推荐" not in err
    assert "一人一页" in err
    assert "build_app" not in err
    assert "可用：" not in err


def test_website_continuation_none_allowed():
    """用户「继续完成官网…」+ 建站形 hand-write → 放行（不再硬拒）。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook_none_reason": "手写补完",
            "tasks": [
                {"role": "前端", "task": "补全分区 HTML CSS 落地页"}
            ],
        },
    )
    assert err is None
    assert name is None


def test_generic_project_continue_none_ok():
    """「讨论继续完成项目的开发」+ 手写前后端 → 声明闸通过。"""
    args = {
        "playbook_none_reason": "继续法庭迷局游戏开发",
        "tasks": [
            {
                "role": "后端工程师",
                "task": "实现案件状态机与证据 API",
            },
            {
                "role": "前端工程师",
                "task": "HTML5 画布与卡牌交互 UI",
            },
        ],
    }
    name, err = resolve_playbook_declaration(args)
    assert err is None
    assert name is None


async def test_execute_accepts_handwritten_without_playbook():
    """自由组队无声明：声明闸放行（后续可能因无 LLM 失败，但不因 playbook 拒）。"""
    t = tool(Provider([]))
    result = await t.execute(
        {
            "tasks": [{"role": "A", "task": "做一点"}],
        },
        ctx(),
    )
    assert "playbook_none_reason" not in (result.error or "")
    assert "须声明 playbook" not in (result.error or "")
    assert "声明必填" not in (result.error or "")


async def test_execute_allows_website_none_bypass():
    """建站意图 + none：声明闸不再硬拒（后续可能因风格/LLM 失败）。"""
    t = tool(Provider([]))
    t._user_message = "请帮我搭建一个营销落地页"
    result = await t.execute(
        {
            "playbook_none_reason": "手写两阶段即可",
            "tasks": [
                {"role": "文案", "task": "写落地页文案"},
                {"role": "前端", "task": "实现落地页 HTML"},
            ],
        },
        ctx(),
    )
    # Must not be the old website none hard-reject.
    assert "禁止" not in (result.error or "") or "build_website" not in (
        result.error or ""
    )
    assert not (
        result.contract_failure
        and result.error
        and "禁止" in result.error
        and "none" in result.error
        and "build_website" in result.error
    )


def test_software_intent_none_thin_html_allowed():
    """软件意图 + none + 单前端单 HTML → 放行（不再硬拒）。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook_none_reason": "单 HTML 即可交付基础版",
            "tasks": [
                {
                    "role": "前端工程师",
                    "task": "写一个 mindmap.html 单文件思维导图工具",
                }
            ],
        },
    )
    assert err is None
    assert name is None


def test_software_greenfield_none_allowed():
    """绿场 SPA / 数据看板 + none → 放行（手写可用，不再硬拒）。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook_none_reason": "手写前后端两节点",
            "tasks": [
                {"role": "前端", "task": "搭 Vite 脚手架"},
                {"role": "前端", "task": "写看板页面"},
            ],
        },
    )
    assert err is None
    assert name is None


def test_software_greenfield_audit_readonly_none_ok():
    """全面审计不改代码 + none → 放行。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook_none_reason": "多角度并行只读审计，不走 build_app",
            "tasks": [
                {
                    "role": "审计员",
                    "task": (
                        "架构与项目结构审计：monorepo / React / Vite；"
                        "只读、不修改代码，产出审计笔记"
                    ),
                },
                {
                    "role": "审计员",
                    "task": "代码健康审计，可对照 build_app 文档仅作参考",
                },
            ],
        },
    )
    assert err is None
    assert name is None


def test_software_greenfield_vue_spa_from_scratch_none_allowed():
    """从0到1做 Vue SPA + none → 放行。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook_none_reason": "手写单 worker",
            "tasks": [{"role": "前端", "task": "从零搭 Vue SPA"}],
        },
    )
    assert err is None
    assert name is None


def test_software_greenfield_named_build_app_unknown():
    """具名工厂图纸已撤：走未知闸，拒文教手写 + consult(building_software)。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook": "build_app",
            "playbook_args": {"app": "运营数据看板", "stack": "Vue3+Vite+TS"},
        },
    )
    assert name is None
    assert err is not None
    assert "未知" in err
    assert "手写" in err
    assert "consult(building_software)" in err
    assert "不要再传" in err
    assert "绿场推荐" not in err
    assert "绿场软件推荐" not in err
    assert "可用：" not in err
    assert declaration_reject_gate(err) == "unknown"


def test_non_greenfield_free_teaming_still_ok():
    """非绿场自由组队（调研）仍可手写 tasks。"""
    name, err = resolve_playbook_declaration(
        {"tasks": [{"role": "调研员", "task": "调研竞品并写报告"}]},
    )
    assert err is None
    assert name is None


def test_software_intent_named_build_feature_unknown():
    """具名 build_feature 已撤：走未知 playbook（手写多角色仍可）。"""
    name, err = resolve_playbook_declaration(
        {
            "playbook": "build_feature",
            "playbook_args": {"feature": "思维导图编辑器", "stack": "FastAPI+React"},
        },
    )
    assert name is None
    assert err is not None
    assert "未知" in err
    assert declaration_reject_gate(err) == "unknown"


def test_software_intent_none_multi_role_ok():
    """软件意图 + none + 多角色工程拆分 → 可。"""
    name, err = resolve_playbook_declaration(
        {
            "tasks": [
                {"role": "后端工程师", "task": "实现本地存储与同步 API"},
                {
                    "role": "前端工程师",
                    "task": "实现编辑器 UI，depends 后端契约",
                    "depends_on": ["api"],
                },
                {
                    "role": "测试工程师",
                    "task": "覆盖同步与编辑边界",
                    "depends_on": ["api"],
                },
            ],
        },
    )
    assert err is None
    assert name is None


async def test_execute_allows_software_thin_html_none():
    t = tool(Provider([]))
    t._user_message = "帮我做一个思维导图软件"
    result = await t.execute(
        {
            "playbook_none_reason": "单 HTML 基础版就够",
            "tasks": [
                {"role": "前端工程师", "task": "实现 mindmap.html"},
            ],
        },
        ctx(),
    )
    assert "build_feature" not in (result.error or "")
    assert not (
        result.contract_failure
        and result.error
        and "单前端" in result.error
    )
