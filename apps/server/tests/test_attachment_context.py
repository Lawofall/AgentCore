"""Tests for the attachment system-prompt block (`_build_attachment_context`).

Pins what the model sees: nothing injected when there are no usable attachments,
local path for un-resident files, the durable in-workspace path + an edit hint
once an attachment has been persisted (附件驻留), and directory listings shown as
paths only.

Conversation attachments (跨会话对话日志访问定案 P1): server deep-read via
``log_export`` — gate-off / soft-miss / truncation notes; never client shallow text.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentcore.runtime.pipeline import _build_attachment_context
from agentcore.workspace.attachment_parse import ATTACHMENT_INLINE_MAX_CHARS


@pytest.mark.asyncio
async def test_none_and_empty_return_none():
    assert await _build_attachment_context(None) is None
    assert await _build_attachment_context([]) is None
    # A file whose text is blank contributes no block → still None.
    assert await _build_attachment_context([{"name": "x", "text": "   "}]) is None


@pytest.mark.asyncio
async def test_attachment_block_is_file_facts_only():
    out = await _build_attachment_context(
        [{"name": "shot.png", "path": "/local/shot.png", "text": "pixels"}]
    )
    assert out is not None
    assert "--- File: shot.png (/local/shot.png) ---" in out
    assert "pixels" in out
    assert "以下是本条消息的附件" not in out
    assert "<附件>" not in out


@pytest.mark.asyncio
async def test_unresident_file_uses_local_path_no_hint():
    out = await _build_attachment_context(
        [{"name": "a.py", "path": "/local/a.py", "text": "print(1)"}]
    )
    assert out is not None
    assert "--- File: a.py (/local/a.py) ---" in out
    assert "print(1)" in out
    assert "are in your workspace" not in out
    assert "actionable inputs" not in out


@pytest.mark.asyncio
async def test_resident_file_uses_workspace_path_and_hint():
    out = await _build_attachment_context(
        [
            {
                "name": "a.py",
                "path": "/local/a.py",
                "text": "print(1)",
                "workspace_path": "attachments/a.py",
            }
        ]
    )
    assert out is not None
    # The header points at the durable path, not the local one.
    assert "--- File: a.py (attachments/a.py) ---" in out
    assert "print(1)" in out
    assert "are in your workspace" not in out


@pytest.mark.asyncio
async def test_spreadsheet_without_preview_omits_run_when_unassembled():
    out = await _build_attachment_context(
        [
            {
                "name": "report.xlsx",
                "path": "attachments/report.xlsx",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/report.xlsx",
            }
        ],
        available_tools=frozenset({"read", "write"}),
    )
    assert out is not None
    assert "[表格 / 仅路径]" in out
    assert "Could not read column names and sample rows." in out
    assert "includes run" not in out
    assert "C:\\" not in out
    assert "/Users/" not in out


@pytest.mark.asyncio
async def test_spreadsheet_mentions_run_only_when_in_tool_table():
    att = {
        "name": "report.xlsx",
        "path": "attachments/report.xlsx",
        "text": "",
        "binary": True,
        "workspace_path": "attachments/report.xlsx",
    }
    with_exec = await _build_attachment_context(
        [att], available_tools=frozenset({"read", "run"})
    )
    without_exec = await _build_attachment_context([att], available_tools=frozenset())
    assert with_exec is not None and without_exec is not None
    assert with_exec == without_exec
    assert "[表格 / 仅路径]" in with_exec
    assert "includes run" not in with_exec
    assert "does not include run" not in without_exec


@pytest.mark.asyncio
async def test_resident_missing_honest_block_no_saved_claim():
    """案 adsense-zip A：验盘失败 → 诚实缺件块，禁「已在工作区」口吻。"""
    out = await _build_attachment_context(
        [
            {
                "name": "独立站源码（新）.zip",
                "path": "attachments/独立站源码（新）.zip",
                "text": "",
                "binary": True,
                "resident_missing": True,
                "claimed_workspace_path": "attachments/独立站源码（新）.zip",
            }
        ]
    )
    assert out is not None
    assert "[resident missing]" in out
    assert "attachments/独立站源码（新）.zip" in out
    assert "bytes are not in the workspace" in out
    assert "are in your workspace" not in out
    assert "ask_user" not in out


@pytest.mark.asyncio
async def test_truncated_note_and_directory_listing():
    out = await _build_attachment_context(
        [
            {"name": "big.txt", "path": "/big.txt", "text": "partial", "truncated": True},
            {"name": "src", "path": "/src", "text": "a.py\nb.py", "kind": "dir"},
        ]
    )
    assert "--- File: big.txt (/big.txt) (truncated) ---" in out
    assert "--- Directory: src (/src) ---" in out
    assert "a.py" in out and "b.py" in out


@pytest.mark.asyncio
async def test_large_plain_text_is_path_pointer_not_inline_body():
    out = await _build_attachment_context(
        [
            {
                "name": "novel.txt",
                "path": "attachments/novel.txt",
                "workspace_path": "attachments/novel.txt",
                "parse_status": "ok",
                "text": "",
                "truncated": True,
                "size_bytes": 9_905_619,
                "total_lines": 184_320,
            }
        ]
    )
    assert out is not None
    assert "UNIQUE_TAIL" not in out
    assert "[large text / path pointer]" in out
    assert "9905619 bytes" in out
    assert "184320 lines" in out
    assert "Body stays on disk at this path." in out


class _AsyncCm:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *a):
        return False


def _patch_deep_read(monkeypatch, *, conv, messages, journal_map=None):
    """Stub session + repos for conversation attachment deep-read."""

    class FakeConvRepo:
        def __init__(self, session):
            pass

        async def get_by_id(self, cid, *, user_id):
            if conv is None:
                return None
            if conv.id != cid:
                return None
            if user_id and getattr(conv, "user_id", user_id) != user_id:
                return None
            return conv

    class FakeMsgRepo:
        def __init__(self, session):
            pass

        async def list_all_for_conversation(self, cid):
            return messages

    class FakeJournalRepo:
        def __init__(self, session):
            pass

        async def load_map(self, ids):
            return journal_map or {}

    # Lazy imports inside ``_deep_read_conversation_attachment`` — patch sources.
    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: _AsyncCm(),
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.ConversationRepository",
        FakeConvRepo,
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.MessageRepository",
        FakeMsgRepo,
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.TurnJournalRepository",
        FakeJournalRepo,
    )


@pytest.mark.asyncio
async def test_conversation_deep_read_success(monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    conv = SimpleNamespace(
        id="conv-1",
        title="讨论 X 方案",
        mode="chat",
        user_id="u1",
        created_at=now,
        updated_at=now,
    )
    messages = [
        SimpleNamespace(
            id="m1",
            role="user",
            content="你好",
            reasoning_content=None,
            attachments=None,
            evidence_ledger=None,
            citations=None,
            usage=None,
            created_at=now,
        ),
        SimpleNamespace(
            id="m2",
            role="assistant",
            content="在的",
            reasoning_content=None,
            attachments=None,
            evidence_ledger=None,
            citations=None,
            usage=None,
            created_at=now,
        ),
    ]
    _patch_deep_read(monkeypatch, conv=conv, messages=messages)

    out = await _build_attachment_context(
        [
            {
                "name": "讨论 X 方案",
                "path": "对话",
                # Client shallow text MUST be ignored.
                "text": "CLIENT_SHALLOW_SHOULD_NOT_APPEAR",
                "kind": "conversation",
                "conversation_id": "conv-1",
            }
        ],
        user_id="u1",
        host_conversation_id="host-now",
    )
    assert out is not None
    assert "--- Conversation: 讨论 X 方案 ---" in out
    assert "### User" in out
    assert "你好" in out
    assert "### Assistant" in out
    assert "在的" in out
    assert "CLIENT_SHALLOW_SHOULD_NOT_APPEAR" not in out
    assert "are in your workspace" not in out


@pytest.mark.asyncio
async def test_conversation_missing_id_soft_miss():
    out = await _build_attachment_context(
        [
            {
                "name": "无 id",
                "text": "CLIENT_SHALLOW",
                "kind": "conversation",
            }
        ],
        user_id="u1",
    )
    assert out is not None
    assert "缺少 conversation_id" in out
    assert "CLIENT_SHALLOW" not in out


@pytest.mark.asyncio
async def test_conversation_soft_miss_wrong_owner(monkeypatch):
    _patch_deep_read(monkeypatch, conv=None, messages=[])
    out = await _build_attachment_context(
        [
            {
                "name": "他人场",
                "text": "CLIENT_SHALLOW",
                "kind": "conversation",
                "conversation_id": "other-1",
            }
        ],
        user_id="u1",
    )
    assert out is not None
    assert "无法打开该对话" in out
    assert "CLIENT_SHALLOW" not in out


@pytest.mark.asyncio
async def test_conversation_soft_miss_handoff(monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    conv = SimpleNamespace(
        id="h1",
        title="handoff host",
        mode="handoff",
        user_id="u1",
        created_at=now,
        updated_at=now,
    )
    _patch_deep_read(monkeypatch, conv=conv, messages=[])
    out = await _build_attachment_context(
        [
            {
                "name": "h",
                "text": "CLIENT",
                "kind": "conversation",
                "conversation_id": "h1",
            }
        ],
        user_id="u1",
    )
    assert "无法打开该对话" in out
    assert "CLIENT" not in out


@pytest.mark.asyncio
async def test_conversation_truncated_note(monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    conv = SimpleNamespace(
        id="long-1",
        title="长对话",
        mode="chat",
        user_id="u1",
        created_at=now,
        updated_at=now,
    )
    huge = "X" * (ATTACHMENT_INLINE_MAX_CHARS + 500)
    messages = [
        SimpleNamespace(
            id="m1",
            role="user",
            content=huge,
            reasoning_content=None,
            attachments=None,
            evidence_ledger=None,
            citations=None,
            usage=None,
            created_at=now,
        ),
    ]
    _patch_deep_read(monkeypatch, conv=conv, messages=messages)

    out = await _build_attachment_context(
        [
            {
                "name": "长对话",
                "text": "CLIENT_SHALLOW",
                "kind": "conversation",
                "conversation_id": "long-1",
            }
        ],
        user_id="u1",
    )
    assert out is not None
    assert "truncated" in out
    assert "read_conversation" in out
    assert "conversation_id=long-1" in out
    assert "CLIENT_SHALLOW" not in out
    # Cap: full huge body must not appear inline.
    assert huge not in out


@pytest.mark.asyncio
async def test_conversation_deep_read_uses_cloud_when_account_creds(monkeypatch):
    """有 account 票 → 走云读，禁止查本机库（大众桌面无 PG）。"""
    from agentcore.account.credentials import (
        AccountCredentials,
        account_credentials_scope,
    )

    db_hits = {"n": 0}

    class _BoomCm:
        async def __aenter__(self):
            db_hits["n"] += 1
            raise AssertionError("must not hit DB when account creds bound")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: _BoomCm(),
    )

    async def _fake_cloud(creds, *, payload):
        assert creds.api_key == "acct-key"
        assert payload["conversation_id"] == "cloud-1"
        assert payload["max_chars"] == ATTACHMENT_INLINE_MAX_CHARS
        assert payload.get("focus") == "dialogue"
        return {
            "status": "ok",
            "title": "云端场",
            "conversation_id": "cloud-1",
            "transcript": "### User\n云正文\n",
            "truncated": False,
            "next_cursor": None,
        }

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_read_conversation",
        _fake_cloud,
    )

    creds = AccountCredentials(api_key="acct-key", base_url="https://api.example/v1/account")
    with account_credentials_scope(creds):
        out = await _build_attachment_context(
            [
                {
                    "name": "云端场",
                    "text": "CLIENT_SHALLOW_SHOULD_NOT_APPEAR",
                    "kind": "conversation",
                    "conversation_id": "cloud-1",
                }
            ],
            user_id="u1",
        )
    assert out is not None
    assert "--- Conversation: 云端场 ---" in out
    assert "云正文" in out
    assert "CLIENT_SHALLOW_SHOULD_NOT_APPEAR" not in out
    assert db_hits["n"] == 0


@pytest.mark.asyncio
async def test_conversation_cloud_soft_miss(monkeypatch):
    from agentcore.account.credentials import (
        AccountCredentials,
        account_credentials_scope,
    )

    async def _fake_cloud(creds, *, payload):
        del creds, payload
        return {"status": "soft_miss", "conversation_id": "missing"}

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_read_conversation",
        _fake_cloud,
    )
    creds = AccountCredentials(api_key="k", base_url="https://api.example/v1/account")
    with account_credentials_scope(creds):
        out = await _build_attachment_context(
            [
                {
                    "name": "缺失场",
                    "text": "CLIENT_SHALLOW",
                    "kind": "conversation",
                    "conversation_id": "missing",
                }
            ],
            user_id="u1",
        )
    assert out is not None
    assert "无法打开该对话" in out
    assert "CLIENT_SHALLOW" not in out


@pytest.mark.asyncio
async def test_conversation_cloud_failure_soft_degrades(monkeypatch):
    from agentcore.account.credentials import (
        AccountCloudError,
        AccountCredentials,
        account_credentials_scope,
    )

    async def _boom(creds, *, payload):
        del creds, payload
        raise AccountCloudError("down", code="account_cloud_unreachable")

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_read_conversation",
        _boom,
    )
    creds = AccountCredentials(api_key="k", base_url="https://api.example/v1/account")
    with account_credentials_scope(creds):
        out = await _build_attachment_context(
            [
                {
                    "name": "云挂",
                    "text": "CLIENT_SHALLOW",
                    "kind": "conversation",
                    "conversation_id": "c1",
                }
            ],
            user_id="u1",
        )
    assert out is not None
    assert "暂时无法深读该对话" in out
    assert "CLIENT_SHALLOW" not in out


@pytest.mark.asyncio
async def test_conversation_db_connectivity_soft_degrades(monkeypatch):
    """无票 + 本机库拒绝连接 → 软说明块，prepare 不因深读抛死。"""
    from sqlalchemy.exc import OperationalError

    class _RefuseCm:
        async def __aenter__(self):
            raise OperationalError("connection refused", None, None)

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: _RefuseCm(),
    )
    out = await _build_attachment_context(
        [
            {
                "name": "库挂",
                "text": "CLIENT_SHALLOW",
                "kind": "conversation",
                "conversation_id": "local-1",
            }
        ],
        user_id="u1",
    )
    assert out is not None
    assert "暂时无法深读该对话" in out
    assert "CLIENT_SHALLOW" not in out


class _StubBackend:
    def __init__(self, blobs: dict[str, bytes] | None = None) -> None:
        self.blobs = blobs or {}

    async def read_bytes(self, path: str) -> bytes:
        if path not in self.blobs:
            raise FileNotFoundError(path)
        return self.blobs[path]


@pytest.mark.asyncio
async def test_image_without_native_vision_honest_unconfigured():
    out = await _build_attachment_context(
        [
            {
                "name": "shot.jpg",
                "path": "attachments/shot.jpg",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/shot.jpg",
                "mime": "image/jpeg",
            }
        ],
        backend=None,
    )
    assert out is not None
    assert "[image]" in out
    assert "当前主模型不收图" in out
    assert "未配置识图兜底" not in out
    assert "勿把工作区路径当作已读图" in out
    assert "勿索要重发" not in out
    assert "run 打开" not in out
    assert "vision 槽" not in out
    assert "VISION_*" not in out
    assert "[binary]" not in out
    assert "CEO has no run" not in out


@pytest.mark.asyncio
async def test_non_image_spreadsheet_skips_vision():
    out = await _build_attachment_context(
        [
            {
                "name": "report.xlsx",
                "path": "attachments/report.xlsx",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/report.xlsx",
            }
        ],
        backend=_StubBackend(),  # type: ignore[arg-type]
        available_tools=frozenset(),
    )
    assert out is not None
    assert "[表格 /" in out
    assert "[image" not in out
    assert "未配置识图" not in out
    assert "includes run" not in out
    assert "with run" not in out


@pytest.mark.asyncio
async def test_table_structure_preview_hides_full_body():
    rows = ["date,amount,memo"]
    secret = "UNIQUE_TAIL_ROW_SHOULD_NOT_ENTER_PROMPT"
    for i in range(1, 21):
        memo = secret if i == 20 else f"item-{i}"
        rows.append(f"2024-01-{i:02d},{i}.0,{memo}")
    body = "\n".join(rows)
    out = await _build_attachment_context(
        [
            {
                "name": "ledger.csv",
                "path": "attachments/ledger.csv",
                "text": body,
                "workspace_path": "attachments/ledger.csv",
            }
        ],
        available_tools=frozenset(),
    )
    assert out is not None
    assert "[表格 / 列名与样例]" in out
    assert "[表格 / 结构面]" not in out
    assert "rows: 20" in out
    assert "date:date" in out
    assert "amount:float" in out or "amount:int" in out
    assert secret not in out
    assert "includes run" not in out
    assert "with run" not in out
    assert "columns" in out
    assert "sample rows" in out


@pytest.mark.asyncio
async def test_office_extract_declares_lossy_tables_without_run():
    out = await _build_attachment_context(
        [
            {
                "name": "voucher.pdf",
                "path": "attachments/voucher.pdf",
                "binary": True,
                "workspace_path": "attachments/voucher.pdf",
                "parsed_workspace_path": "attachments/voucher.pdf.md",
                "parse_status": "ok",
                "text": "转账时间 收款方 金额\n2024-01-01 张三 12.00",
            }
        ],
        available_tools=frozenset({"read"}),
    )
    assert out is not None
    assert "转账时间 收款方 金额" in out
    assert "lossy for tabular content" not in out
    assert "includes run" not in out


@pytest.mark.asyncio
async def test_heic_routes_to_image_not_run():
    """HEIC/HEIF stay on the image path, not the generic [binary]/run path."""
    out = await _build_attachment_context(
        [
            {
                "name": "photo.heic",
                "path": "attachments/photo.heic",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/photo.heic",
                "mime": "image/heic",
            }
        ],
        backend=None,
    )
    assert out is not None
    assert "[image]" in out
    assert "当前主模型不收图" in out
    assert "未配置识图兜底" not in out
    assert "[binary]" not in out
    assert "CEO has no run" not in out


@pytest.mark.asyncio
async def test_heif_ext_without_mime_routes_to_image():
    out = await _build_attachment_context(
        [
            {
                "name": "shot.heif",
                "path": "attachments/shot.heif",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/shot.heif",
            }
        ],
        backend=None,
    )
    assert out is not None
    assert "[image]" in out
    assert "[binary]" not in out


@pytest.mark.asyncio
async def test_generic_image_mime_routes_to_image():
    """image/* (non-excluded) follows the image path even when subtype is not in the allowlist."""
    out = await _build_attachment_context(
        [
            {
                "name": "capture.bin",
                "path": "attachments/capture.bin",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/capture.bin",
                "mime": "image/tiff",
            }
        ],
        backend=None,
    )
    assert out is not None
    assert "[image]" in out
    assert "[binary]" not in out


@pytest.mark.asyncio
async def test_svg_mime_excluded_from_vision_path():
    """SVG is image/* but not a raster — stay on binary path."""
    out = await _build_attachment_context(
        [
            {
                "name": "icon.svg",
                "path": "attachments/icon.svg",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/icon.svg",
                "mime": "image/svg+xml",
            }
        ],
        backend=_StubBackend(),  # type: ignore[arg-type]
    )
    assert out is not None
    assert "[binary]" in out
    assert "[image" not in out


@pytest.mark.asyncio
async def test_main_native_vision_builds_image_parts_skips_reader():
    """Main catalog vision → multimodal parts on the current model."""
    backend = _StubBackend({"attachments/pic.jpg": b"\xff\xd8\xffjpeg"})
    parts: list[dict] = []

    out = await _build_attachment_context(
        [
            {
                "name": "pic.jpg",
                "path": "attachments/pic.jpg",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/pic.jpg",
            }
        ],
        backend=backend,  # type: ignore[arg-type]
        main_native_vision=True,
        native_image_parts=parts,
    )
    assert out is not None
    assert "[image / multimodal]" in out
    assert "多模态" in out
    assert len(parts) == 1
    assert parts[0]["type"] == "image_url"
    url = parts[0]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")


def test_build_multimodal_user_content_and_llm_content_text():
    from agentcore.llm.provider.protocol import (
        build_multimodal_user_content,
        llm_content_text,
    )

    assert build_multimodal_user_content("hi", []) == "hi"
    parts = build_multimodal_user_content(
        "look",
        [{"type": "image_url", "image_url": {"url": "data:image/png;base64,aa"}}],
    )
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "look"}
    assert parts[1]["type"] == "image_url"
    assert llm_content_text(parts) == "look"
    assert llm_content_text("plain").strip() == "plain"
    empty_parts = build_multimodal_user_content(
        "  ",
        [{"type": "image_url", "image_url": {"url": "data:image/png;base64,aa"}}],
    )
    assert isinstance(empty_parts, list)
    assert "图片" in empty_parts[0]["text"]


@pytest.mark.asyncio
async def test_document_pin_is_not_uploaded_material():
    from agentcore.runtime.resolve.prompt.ceo_core import attachment_material_scene

    out = await _build_attachment_context(
        [
            {
                "name": "说话简短",
                "path": "设定",
                "kind": "document",
                "text": "回复保持短句。",
            }
        ]
    )
    assert out is not None
    assert "<钉住条目>" in out
    assert "回复保持短句。" in out
    assert "<附件>" not in out
    assert attachment_material_scene() is False


@pytest.mark.asyncio
async def test_document_pin_keeps_inline_slot_without_file_body():
    from agentcore.runtime.resolve.attachment_context import _build_attachment_prompt

    prompt = await _build_attachment_prompt(
        [
            {"name": "a.txt", "path": "a.txt", "text": "hello", "kind": "file"},
            {
                "name": "说话简短",
                "path": "设定",
                "kind": "document",
                "text": "短句",
            },
        ]
    )
    assert prompt.pinned_xml is not None
    assert "<钉住条目>" in prompt.pinned_xml
    assert "<附件>" not in prompt.pinned_xml
    assert "短句" in prompt.pinned_xml
    assert prompt.file_blocks[0].startswith("--- File: a.txt")
    assert prompt.file_blocks[1] == ""
    assert prompt.has_file_parts is True


@pytest.mark.asyncio
async def test_document_pin_prefers_server_body_over_client_text(monkeypatch):
    from agentcore.runtime.resolve import attachment_context as ac

    async def _load(*, document_id: str, user_id: str | None) -> str | None:
        assert document_id == "doc-1"
        assert user_id == "u1"
        return "真正正文"

    monkeypatch.setattr(ac, "_load_pinned_document_body", _load)

    out = await _build_attachment_context(
        [
            {
                "name": "说话简短",
                "path": "设定",
                "kind": "document",
                "document_id": "doc-1",
                "text": "客户端伪造",
            }
        ],
        user_id="u1",
    )
    assert out is not None
    assert "真正正文" in out
    assert "客户端伪造" not in out


@pytest.mark.asyncio
async def test_document_pin_ignores_client_text_when_id_unloadable():
    out = await _build_attachment_context(
        [
            {
                "name": "说话简短",
                "path": "设定",
                "kind": "document",
                "document_id": "doc-1",
                "text": "客户端伪造",
            }
        ]
    )
    assert out is not None
    assert "客户端伪造" not in out
    assert "未能加载" in out


@pytest.mark.asyncio
async def test_unpacked_archive_lists_folder_not_run_steer():
    out = await _build_attachment_context(
        [
            {
                "name": "病历.rar",
                "path": "attachments/病历.rar",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/病历.rar",
                "archive_status": "ok",
                "archive_dir": "attachments/病历",
                "archive_members": ["a.pdf", "notes.md"],
                "archive_member_count": 2,
            }
        ],
        available_tools=["run"],
    )
    assert out is not None
    assert "[archive / unpacked → attachments/病历]" in out
    assert "attachments/病历/a.pdf" in out
    assert "2 files in `attachments/病历`." in out
    assert "[binary]" not in out


@pytest.mark.asyncio
async def test_password_archive_is_not_binary():
    out = await _build_attachment_context(
        [
            {
                "name": "locked.zip",
                "path": "attachments/locked.zip",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/locked.zip",
                "archive_status": "password",
            }
        ],
        available_tools=["run"],
    )
    assert out is not None
    assert "[archive / password]" in out
    assert "Password-protected" in out
    assert "[binary]" not in out


@pytest.mark.asyncio
async def test_failed_archive_stays_archive_not_binary():
    out = await _build_attachment_context(
        [
            {
                "name": "1.病历资料(1).rar",
                "path": "attachments/1.病历资料(1).rar",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/1.病历资料(1).rar",
                "archive_status": "failed",
            }
        ]
    )
    assert out is not None
    assert "[archive / unreadable]" in out
    assert "[binary]" not in out
