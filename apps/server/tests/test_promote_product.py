"""成品归位（``promote_product``）：CEO 把路径已核成品从 ``.agentcore`` 移进用户工作区。

Hermetic：真 ``ServerWorkspace`` 落在 ``tmp_path``，断言磁盘真实结果——归位是**移动**
不是标记，所以每个用例都查「旧路径没了 / 新路径在」，而不是只看回执文本。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentcore.runtime.delegate.promotion import note_delivery_reconciliation
from agentcore.tools.builtin.promote_product import PromoteProductTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from agentcore.workspace.stage_dirs import DRAFTS_DIR

DRAFT = f"{DRAFTS_DIR}/课程讲稿.md"
DRAFT2 = f"{DRAFTS_DIR}/预算表.md"


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext.create(
        execution_id="e1",
        run_id="s",
        agent_id="ceo",
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="conv-1",
    )


def _seed(workspace: Path, *paths: str, body: str = "正文") -> None:
    for rel in paths:
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


def _payload(*accepted: str, rejected: str | None = None) -> dict[str, Any]:
    """A batch wrap-up's ``delivery_status`` payload (the accepted 闸门的真源)."""
    artifacts: list[dict[str, Any]] = [{"path": p, "status": "accepted"} for p in accepted]
    if rejected:
        artifacts.append({"path": rejected, "status": "rejected", "reason": "citations_unverified"})
    return {
        "execution_id": "e1",
        "state": "delivered",
        "summary": "已交付",
        "delivered_files": list(accepted),
        "gaps": [],
        "actions": [],
        "artifacts": artifacts,
    }


def _reconcile(context: ToolContext, *accepted: str, rejected: str | None = None) -> None:
    """Stand in for the batch wrap-up that stamped this turn's delivery reconciliation."""
    note_delivery_reconciliation(context.promotion_ledger, _payload(*accepted, rejected=rejected))


def _fake_journal(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any] | None) -> list[str]:
    """Stand in for the durable journal; returns the conversation_ids looked up."""
    looked_up: list[str] = []

    class _Repo:
        def __init__(self, _session: Any) -> None:
            pass

        async def find_latest_delivery_status(
            self, *, conversation_id: str, exclude_turn_id: str | None = None
        ) -> dict[str, Any] | None:
            looked_up.append(conversation_id)
            return dict(payload) if payload is not None else None

    class _Session:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr("agentcore.db.base.async_session_factory", lambda: _Session())
    monkeypatch.setattr("agentcore.db.repositories.TurnJournalRepository", _Repo)
    return looked_up


async def test_promotes_accepted_product_to_workspace_root(tmp_path: Path):
    """省略 dest = 工作区根：文件真搬走，旧路径不复存在。"""
    _seed(tmp_path, DRAFT)
    context = _ctx(tmp_path)
    _reconcile(context, DRAFT)

    result = await PromoteProductTool().execute({"paths": [DRAFT]}, context)

    assert result.success is True
    assert not (tmp_path / DRAFT).exists()
    assert (tmp_path / "课程讲稿.md").read_text(encoding="utf-8") == "正文"
    assert "课程讲稿.md" in result.output
    assert [p.path for p in result.file_products or []] == ["课程讲稿.md"]


async def test_promotes_into_dest_subdirectory(tmp_path: Path):
    """代码仓这类已有结构的工作区靠 dest 指定子目录，不污染根。"""
    _seed(tmp_path, DRAFT)
    context = _ctx(tmp_path)
    _reconcile(context, DRAFT)

    result = await PromoteProductTool().execute({"paths": [DRAFT], "dest": "docs"}, context)

    assert result.success is True
    assert (tmp_path / "docs" / "课程讲稿.md").exists()
    assert not (tmp_path / DRAFT).exists()


async def test_existing_target_is_never_overwritten(tmp_path: Path):
    """目标同名文件可能是用户自己的——跳过并说明，两份都保持原样。"""
    _seed(tmp_path, DRAFT, body="AI 写的")
    _seed(tmp_path, "课程讲稿.md", body="用户自己的")
    context = _ctx(tmp_path)
    _reconcile(context, DRAFT)

    result = await PromoteProductTool().execute({"paths": [DRAFT]}, context)

    assert result.success is True
    assert (tmp_path / "课程讲稿.md").read_text(encoding="utf-8") == "用户自己的"
    assert (tmp_path / DRAFT).read_text(encoding="utf-8") == "AI 写的"
    assert "未覆盖" in result.output
    assert result.file_products == []


async def test_rejected_and_unknown_paths_are_skipped(tmp_path: Path):
    """只有 accepted 的可归位；拒收 / 不在对账里的留在工作间。"""
    rejected = f"{DRAFTS_DIR}/未验收.md"
    _seed(tmp_path, DRAFT, DRAFT2, rejected)
    context = _ctx(tmp_path)
    _reconcile(context, DRAFT, rejected=rejected)

    result = await PromoteProductTool().execute({"paths": [DRAFT, DRAFT2, rejected]}, context)

    assert result.success is True
    assert (tmp_path / "课程讲稿.md").exists()
    assert (tmp_path / DRAFT2).exists()
    assert (tmp_path / rejected).exists()
    assert not (tmp_path / "预算表.md").exists()
    assert not (tmp_path / "未验收.md").exists()
    assert result.output.count("路径已核清单") == 2


async def test_paths_outside_the_ai_workspace_are_skipped(tmp_path: Path):
    """已在用户工作区里的文件无需归位（也不该被搬来搬去）。"""
    _seed(tmp_path, "已在工作区.md")
    context = _ctx(tmp_path)
    _reconcile(context, "已在工作区.md")

    result = await PromoteProductTool().execute({"paths": ["已在工作区.md"]}, context)

    assert result.success is True
    assert (tmp_path / "已在工作区.md").exists()
    assert ".agentcore" in result.output
    assert "AgentCore/文档/" in result.output
    assert "AI 工作间" not in result.output


async def test_without_delivery_reconciliation_it_stops_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """取不到 accepted 名单时既不放行也不重算验收——诚实报错，一个文件都不动。"""
    _seed(tmp_path, DRAFT)
    context = _ctx(tmp_path)
    _fake_journal(monkeypatch, None)

    result = await PromoteProductTool().execute({"paths": [DRAFT]}, context)

    assert result.success is False
    assert result.contract_failure is True
    assert "交付对账" in (result.error or "")
    assert (tmp_path / DRAFT).exists()


async def test_dest_inside_agentcore_is_rejected(tmp_path: Path):
    """归位的目的就是搬出工作间，dest 不能又指回 AgentCore/。"""
    _seed(tmp_path, DRAFT)
    context = _ctx(tmp_path)
    _reconcile(context, DRAFT)

    result = await PromoteProductTool().execute(
        {"paths": [DRAFT], "dest": "AgentCore/文档"}, context
    )

    assert result.success is False
    assert result.contract_failure is True
    assert ".agentcore" in (result.error or "")
    assert "AI 工作间" not in (result.error or "")
    assert (tmp_path / DRAFT).exists()


async def test_empty_paths_is_a_contract_failure(tmp_path: Path):
    context = _ctx(tmp_path)
    _reconcile(context, DRAFT)

    result = await PromoteProductTool().execute({"paths": []}, context)

    assert result.success is False
    assert result.contract_failure is True


async def test_duplicate_and_colliding_basenames(tmp_path: Path):
    """同一路径重复 → 只搬一次；不同目录同名 → 第二个不覆盖第一个。"""
    twin = f"{DRAFTS_DIR}/子目录/课程讲稿.md"
    _seed(tmp_path, DRAFT, body="第一份")
    _seed(tmp_path, twin, body="第二份")
    context = _ctx(tmp_path)
    _reconcile(context, DRAFT, twin)

    result = await PromoteProductTool().execute({"paths": [DRAFT, DRAFT, twin]}, context)

    assert result.success is True
    assert (tmp_path / "课程讲稿.md").read_text(encoding="utf-8") == "第一份"
    assert (tmp_path / twin).read_text(encoding="utf-8") == "第二份"
    assert "重复" in result.output
    assert "未覆盖" in result.output


async def test_ledger_rewrite_and_republish(tmp_path: Path):
    """搬完台账不留悬空引用：对账路径改写到新位置 + promoted 行，按同 id 重发。"""
    _seed(tmp_path, DRAFT, DRAFT2)
    context = _ctx(tmp_path)
    _reconcile(context, DRAFT, DRAFT2)
    published: list[dict[str, Any]] = []

    result = await PromoteProductTool(on_promoted=published.append).execute(
        {"paths": [DRAFT], "dest": "docs"}, context
    )

    assert result.success is True
    assert len(published) == 1
    payload = published[0]
    assert payload["execution_id"] == "e1"
    assert payload["promoted"] == [{"from": DRAFT, "to": "docs/课程讲稿.md"}]
    # 已归位的指向新位置；没搬的保持原样（工作间里还在）。
    assert payload["delivered_files"] == ["docs/课程讲稿.md", DRAFT2]
    assert [a["path"] for a in payload["artifacts"]] == ["docs/课程讲稿.md", DRAFT2]
    assert all(a["status"] == "accepted" for a in payload["artifacts"])
    # 台账本身也已更新——同回合第二次归位读到的是改写后的清单。
    assert context.promotion_ledger.reconciliation == payload
    assert context.promotion_ledger.promotions == [{"from": DRAFT, "to": "docs/课程讲稿.md"}]


async def test_zero_promotion_is_not_an_error(tmp_path: Path):
    """零归位是合法状态（多幕协作的中间幕）：不报错、不重发、不产生缺口。"""
    _seed(tmp_path, DRAFT2)
    context = _ctx(tmp_path)
    _reconcile(context, DRAFT)  # DRAFT2 未验收
    published: list[dict[str, Any]] = []

    result = await PromoteProductTool(on_promoted=published.append).execute(
        {"paths": [DRAFT2]}, context
    )

    assert result.success is True
    assert published == []
    assert context.promotion_ledger.promotions == []
    assert "没有归位任何成品" in result.output


async def test_ledger_path_form_wins_over_the_requested_spelling(tmp_path: Path):
    """模型把路径抄成 `./…`：仍认得出是同一份，且改写按台账原样记，不留悬空引用。"""
    _seed(tmp_path, DRAFT)
    context = _ctx(tmp_path)
    _reconcile(context, DRAFT)
    published: list[dict[str, Any]] = []

    result = await PromoteProductTool(on_promoted=published.append).execute(
        {"paths": [f"./{DRAFT}"]}, context
    )

    assert result.success is True
    assert (tmp_path / "课程讲稿.md").exists()
    assert published[0]["promoted"] == [{"from": DRAFT, "to": "课程讲稿.md"}]
    assert published[0]["delivered_files"] == ["课程讲稿.md"]


async def test_export_lineage_follows_the_move(tmp_path: Path):
    """导出件的 ``derived_from`` 跟着源一起改写——否则中间稿折叠断链、md 与 docx 并列。"""
    docx = f"{DRAFTS_DIR}/报告.docx"
    md = f"{DRAFTS_DIR}/报告.md"
    _seed(tmp_path, md, docx)
    context = _ctx(tmp_path)
    payload = _payload(md, docx)
    payload["artifacts"][1]["derived_from"] = md
    note_delivery_reconciliation(context.promotion_ledger, payload)
    published: list[dict[str, Any]] = []

    result = await PromoteProductTool(on_promoted=published.append).execute(
        {"paths": [md, docx]}, context
    )

    assert result.success is True
    rows = {a["path"]: a for a in published[0]["artifacts"]}
    assert set(rows) == {"报告.md", "报告.docx"}
    assert rows["报告.docx"]["derived_from"] == "报告.md"


async def test_lineage_rewrite_survives_a_different_spelling(tmp_path: Path):
    """自报的源路径拼法与落盘 path 不同（`./` / `//`）：仍认得出是同一份，不留悬空。"""
    docx = f"{DRAFTS_DIR}/报告.docx"
    md = f"{DRAFTS_DIR}/报告.md"
    _seed(tmp_path, md, docx)
    context = _ctx(tmp_path)
    payload = _payload(md, docx)
    payload["artifacts"][1]["derived_from"] = f"./{DRAFTS_DIR}//报告.md"
    note_delivery_reconciliation(context.promotion_ledger, payload)
    published: list[dict[str, Any]] = []

    result = await PromoteProductTool(on_promoted=published.append).execute(
        {"paths": [md]}, context
    )

    assert result.success is True
    rows = {a["path"]: a for a in published[0]["artifacts"]}
    assert rows[docx]["derived_from"] == "报告.md"


async def test_gap_paths_follow_the_move(tmp_path: Path):
    """软提醒里的 ``gaps[].paths``（「打开相关文件」）同样不能指向已搬走的旧位置。"""
    _seed(tmp_path, DRAFT)
    context = _ctx(tmp_path)
    payload = _payload(DRAFT)
    payload["gaps"] = [
        {
            "role": "文书撰写",
            "description": "有一处待核实",
            "reason": "unverified_note",
            "severity": "warning",
            "paths": [DRAFT],
        }
    ]
    note_delivery_reconciliation(context.promotion_ledger, payload)
    published: list[dict[str, Any]] = []

    result = await PromoteProductTool(on_promoted=published.append).execute(
        {"paths": [DRAFT]}, context
    )

    assert result.success is True
    assert published[0]["gaps"][0]["paths"] == ["课程讲稿.md"]


async def test_falls_back_to_the_conversations_last_durable_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """主流路径：批次收尾 → ask_user → 续跑归位。续跑是新台账，靠 journal 拿 accepted。"""
    _seed(tmp_path, DRAFT)
    context = _ctx(tmp_path)  # 全新回合：台账空
    looked_up = _fake_journal(monkeypatch, _payload(DRAFT))
    published: list[dict[str, Any]] = []

    result = await PromoteProductTool(on_promoted=published.append).execute(
        {"paths": [DRAFT]}, context
    )

    assert result.success is True
    assert looked_up == ["conv-1"]  # 只认同一 conversation
    assert (tmp_path / "课程讲稿.md").exists()
    assert not (tmp_path / DRAFT).exists()
    # 落盘那条卡按同 execution_id 重发，路径改写到新位置。
    assert published[0]["execution_id"] == "e1"
    assert published[0]["delivered_files"] == ["课程讲稿.md"]


async def test_turn_ledger_wins_over_the_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """回合内台账优先：有对账就不查库（库里那条可能是更早的批次）。"""
    _seed(tmp_path, DRAFT2)
    context = _ctx(tmp_path)
    _reconcile(context, DRAFT2)
    looked_up = _fake_journal(monkeypatch, _payload(DRAFT))

    result = await PromoteProductTool().execute({"paths": [DRAFT2]}, context)

    assert result.success is True
    assert looked_up == []
    assert (tmp_path / "预算表.md").exists()


async def test_journal_only_admits_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """跨回合也只放行 accepted——落盘的是对账结果，不是「凡产出皆可归位」。"""
    rejected = f"{DRAFTS_DIR}/未验收.md"
    _seed(tmp_path, DRAFT, rejected)
    context = _ctx(tmp_path)
    _fake_journal(monkeypatch, _payload(DRAFT, rejected=rejected))

    result = await PromoteProductTool().execute({"paths": [rejected]}, context)

    assert result.success is True
    assert (tmp_path / rejected).exists()
    assert "路径已核清单" in result.output


async def test_second_promotion_reads_the_rewritten_card_and_keeps_prior_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """归位后重发让 journal 最新那条已改写——二次归位读到它既不重搬也不丢旧行。"""
    _seed(tmp_path, DRAFT2)
    _seed(tmp_path, "课程讲稿.md")  # 上一回合已归位
    rewritten = _payload("课程讲稿.md", DRAFT2)
    rewritten["promoted"] = [{"from": DRAFT, "to": "课程讲稿.md"}]
    context = _ctx(tmp_path)
    _fake_journal(monkeypatch, rewritten)
    published: list[dict[str, Any]] = []

    result = await PromoteProductTool(on_promoted=published.append).execute(
        {"paths": ["课程讲稿.md", DRAFT2]}, context
    )

    assert result.success is True
    # 已在工作区的那份原地不动（不吃自己的尾巴），只搬还在工作间的。
    assert (tmp_path / "课程讲稿.md").exists()
    assert (tmp_path / "预算表.md").exists()
    assert ".agentcore" in result.output
    assert "AgentCore/文档/" in result.output
    assert "AI 工作间" not in result.output
    # 上一轮的 {from,to} 是旧路径唯一的回查线索，重发时必须还在。
    assert published[0]["promoted"] == [
        {"from": DRAFT, "to": "课程讲稿.md"},
        {"from": DRAFT2, "to": "预算表.md"},
    ]
    assert published[0]["delivered_files"] == ["课程讲稿.md", "预算表.md"]


async def test_journal_lookup_failure_reports_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """库挂了 = 取不到清单：诚实回报，不放行、不抛栈。"""
    _seed(tmp_path, DRAFT)
    context = _ctx(tmp_path)

    def _boom() -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr("agentcore.db.base.async_session_factory", _boom)

    result = await PromoteProductTool().execute({"paths": [DRAFT]}, context)

    assert result.success is False
    assert result.contract_failure is True
    assert (tmp_path / DRAFT).exists()


def test_closing_declaration_lives_in_the_prompt_not_a_gate():
    """「收口说清归位了什么」走提示词层：装配了才提，且明写可答「无」。"""
    from agentcore.runtime.resolve.prompt.compose import compose_ceo_chat_prompt

    with_tool = compose_ceo_chat_prompt("BASE", ceo_tool_names={"delegate", "promote_product"})
    without = compose_ceo_chat_prompt("BASE", ceo_tool_names={"delegate"})

    assert "【成品归位】" in with_tool
    assert "【成品归位】" not in without
    # 零归位可诚实答「无」——不是硬闸，收口不因此被拦。
    assert "本轮无成品归位" in with_tool
    assert ".agentcore" in with_tool
    assert "AgentCore/文档/" in with_tool
    assert "AI 工作间" not in with_tool


def test_schema_uses_agentcore_label_and_disk_path():
    """用户/模型可见 schema 与 UI 同称 `.agentcore`，并点明盘上 `AgentCore/文档/`。"""
    schema = PromoteProductTool().schema
    paths_desc = schema.parameters["properties"]["paths"]["description"]
    blob = schema.description + paths_desc

    assert ".agentcore" in schema.description
    assert "AgentCore/文档/" in blob
    assert "AgentCore/文档/" in paths_desc
    assert "AI 工作间" not in blob


async def test_republish_failure_does_not_fail_the_move(tmp_path: Path):
    """重发是旁路：卡片没更新也不能让已经搬好的文件报失败。"""
    _seed(tmp_path, DRAFT)
    context = _ctx(tmp_path)
    _reconcile(context, DRAFT)

    def _boom(_payload: dict[str, Any]) -> None:
        raise RuntimeError("sink closed")

    result = await PromoteProductTool(on_promoted=_boom).execute({"paths": [DRAFT]}, context)

    assert result.success is True
    assert (tmp_path / "课程讲稿.md").exists()
