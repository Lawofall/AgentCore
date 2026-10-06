"""Skill body: product_help.

Catalog summary is what this is, plus when the user is asking about the product.
Consult body = 产品合同，不附节名清单。教练节与机制选读不进语料。
节事实：consult("product_help:<用户原话>") 按节标题对上一节；精确节 id 仍可取。
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
身份问：可见正文首句用【这是什么】。问到某功能时 consult("product_help:<用户原话>")。\
对人用产品面说法（对话、协作图、工作区、检查点、审批）≠ `ask_user` / SSE / `run`。

【这是什么】
AgentCore 是 Multi-Agent AI 工作台：你只对接一位 CEO；轻问它直接答，该协作时组团后把结果交给你。\
「协作，是更高级的智能」。

【官网】
本产品官网：https://fashitianxia.xyz
桌面安装包：https://fashitianxia.xyz/download
网页版：https://app.fashitianxia.xyz

【入口】
桌面「设置 · 服务商」/「设置 · 装配」/「设置 · 用量」；手机 ☰ 打开侧栏进「设置」再点「服务商」、再点「装配」、再点「用量」。\
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


# 标题整段，或「与 / 和」两侧至少 3 字的一段。短尾（文件、工具、审批）不对上。
_TITLE_SEPS = ("与", "和")
_MIN_TITLE_PART = 3


def _title_keys(title: str) -> list[str]:
    keys = [title] if title else []
    for sep in _TITLE_SEPS:
        if sep not in title:
            continue
        left, right = title.split(sep, 1)
        for part in (left.strip(), right.strip()):
            if len(part) >= _MIN_TITLE_PART and part not in keys:
                keys.append(part)
        break
    return keys


def _sections_matching_query(query: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for sec in _sections():
        title = str(sec.get("title") or "")
        if any(key in query for key in _title_keys(title)):
            hits.append(sec)
    return hits


def build_product_help_body() -> str:
    how = _PRODUCT_HELP_HOW.rstrip()
    return f"{how}\n</本产品>"


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
    """Exact id / alias, else one section whose title appears in the words.

    Miss and multi-hit return the index card and do not list the section menu.
    """
    raw = section_id.strip()
    if not raw:
        return build_product_help_body()
    canonical = resolve_product_help_section_id(raw)
    for sec in _sections():
        if str(sec["id"]) == canonical:
            return format_product_help_section(sec)
    hits = _sections_matching_query(raw)
    if len(hits) == 1:
        return format_product_help_section(hits[0])
    if len(hits) > 1:
        names = "、".join(str(sec["title"]) for sec in hits)
        return f"对上多节：{names}。说具体是哪一个。\n\n{build_product_help_body()}"
    return build_product_help_body()
