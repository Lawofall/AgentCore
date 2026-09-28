"""Skill body: product_help.

Catalog summary is what this is, plus when the user is asking about the product.
Consult body = 产品合同 + 可查事实节；教练节与机制选读不进语料。
节事实 fetch with consult("product_help:<id>")。
身份问走本卡【这是什么】。CEO 核不写路由尺；何时派在 delegate description。
上报与「看不到服务端日志」跟本技能同一 WHEN，不另立排查 skill。
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

PRODUCT_HELP_NAME = "product_help"
PRODUCT_HELP_SECTION_SEP = ":"

_CORPUS_PATH = Path(__file__).with_name("product_help_corpus.json")
_SURFACE_ZH = {"desktop": "桌面", "web": "网页", "mobile": "手机"}
_ALL_SURFACES = ("desktop", "web", "mobile")

_PRODUCT_HELP_HOW = """\
<本产品>
身份问：可见正文首句用【这是什么】。用户问到某功能再 consult("product_help:<节id>")。\
对人用产品面说法（对话、协作图、工作区、检查点、审批）≠ `ask_user` / SSE / `run`。

【这是什么】
AgentCore 是 Multi-Agent AI 工作台：你只对接一位 CEO；轻问它直接答，该协作时组团后把结果交给你。\
「协作，是更高级的智能」。

【官网】
本产品官网：https://fashitianxia.xyz
桌面安装包：https://fashitianxia.xyz/download
网页版：https://app.fashitianxia.xyz

【入口】
桌面「设置 · 服务商」/「设置 · 模型组合」/「设置 · 用量」；手机 ☰ 打开侧栏进「设置」再点「服务商」、再点「模型组合」、再点「用量」。\
手机无对应页 → 「手机无此入口」或真实替代路径 ≠ 编手机页名。\
桌面可附手册 `#/toolbox/manual/{章}?s={节}`；手机无手册入口。

【产物】
对照 `<工作区>`「执行」：本机可给真实路径；云端文件不在用户电脑 ≠ 本机磁盘路径 / 已在电脑上 / 双击打开。\
对人指路用面板文件夹名 / 文件名 ≠ 「工作区根」。\
HTML「完整预览」仅桌面；Web / 手机走文件面板下载。\
拿走：回复里可点的文件名；网页/手机点开后下载、桌面可合回本机。\
「下载路径 / 下载链接」= 上述入口 ≠ 传到网盘再给人网址。

【规矩】
Cursor `.cursor/rules` / `.mdc`（仓内文件）≠ AgentCore `.agentcore/rules/*.md`（提示词条目，不在工作区树）；\
`skills/*.json` ≠ 平台规则迁移目标。

【上报】
看不到服务端日志 ≠ 假装读了。对人短答 consult("product_help:troubleshooting") ≠ 改产品仓 / 开 PR。
"""


@cache
def load_product_help_corpus() -> dict[str, Any]:
    return json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))


def _sections() -> list[dict[str, Any]]:
    return list(load_product_help_corpus()["sections"])


def _aliases() -> dict[str, str]:
    raw = load_product_help_corpus().get("aliases") or {}
    return {str(k): str(v) for k, v in raw.items()}


def resolve_product_help_section_id(section_id: str) -> str:
    key = section_id.strip()
    return _aliases().get(key, key)


def list_product_help_section_ids() -> list[str]:
    return [str(s["id"]) for s in _sections()]


def _toc_lines() -> str:
    default_rows: list[str] = []
    optional_rows: list[str] = []
    for sec in _sections():
        row = f"- {sec['id']} — {sec['title']}"
        if sec.get("ai") == "optional":
            optional_rows.append(row)
        else:
            default_rows.append(row)
    parts = [
        "【可查事实】用户问到该功能再 consult(\"product_help:<id>\")。",
        *default_rows,
    ]
    if optional_rows:
        parts.extend(["选读：", *optional_rows])
    return "\n".join(parts)


def build_product_help_body() -> str:
    how = _PRODUCT_HELP_HOW.rstrip()
    return f"{how}\n\n{_toc_lines()}\n</本产品>"


def _availability_line(availability: list[str]) -> str:
    present = [s for s in _ALL_SURFACES if s in availability]
    missing = [s for s in _ALL_SURFACES if s not in availability]
    if not missing:
        return ""
    yes = "、".join(_SURFACE_ZH[s] for s in present)
    no = "、".join(_SURFACE_ZH[s] for s in missing)
    return f"【可用性】{yes}；{no}无此入口。\n"


def format_product_help_section(sec: dict[str, Any]) -> str:
    avail = [str(x) for x in sec.get("availability") or list(_ALL_SURFACES)]
    bits = [f"# {sec['title']}", ""]
    line = _availability_line(avail)
    if line:
        bits.append(line)
    bits.append(str(sec.get("text") or "").strip())
    href = str(sec.get("href") or "").strip()
    if href:
        bits.extend(["", f"桌面手册：`{href}`"])
    return "\n".join(bits).strip() + "\n"


def fetch_product_help_section(section_id: str) -> str:
    """Return section body or a soft-miss listing valid ids (never None)."""
    raw = section_id.strip()
    if not raw:
        ids = "、".join(list_product_help_section_ids())
        return f"缺少节 id。可查阅：{ids}。"
    canonical = resolve_product_help_section_id(raw)
    for sec in _sections():
        if str(sec["id"]) == canonical:
            return format_product_help_section(sec)
    ids = "、".join(list_product_help_section_ids())
    return f"没有名为 '{raw}' 的手册节。可查阅：{ids}。"
