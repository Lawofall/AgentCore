"""A/B：当轮信封加「请去掉拿腔拿调的散文。」会不会少拿腔拿调。

观察脚本，不改产品提示。关臂 = 现行基座 + 用户题；开臂 = 同一基座 + 只含这一句的
``[系统提示]`` + 同一道题。裁判用 Anthropic 对 mannered prose 的定义打 0/1。

凭据走 ``eval_credentials``（dev 账号自己的 Key，不读平台池）。报告写到
``eval-out/``，不入库。

从 ``apps/server`` 跑::

    uv run python scripts/mannered_prose_ab.py
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import sys
from pathlib import Path

from agentcore.evals.harness import eval_credentials
from agentcore.llm.factory import build_provider
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest
from agentcore.runtime.resolve.prompt.base import _DEFAULT_SYSTEM_PROMPT
from agentcore.runtime.resolve.prompt.envelope import TURN_ENVELOPE_FENCE

MANNERED_PROSE_LINE = "请去掉拿腔拿调的散文。"

PROMPTS: tuple[tuple[str, str], ...] = (
    (
        "retry",
        "给老板用一段话讲：为什么这个功能先不做自动重试，而要等用户点一下。不要列表。",
    ),
    (
        "status",
        "写一段本周进度，对象是不写代码的负责人。内容只有两件：登录修好了，导出还在等对方确认格式。不要列表。",
    ),
    (
        "idempotent",
        "解释什么是幂等：同一个请求发两次，结果应该和发一次一样。写成给同事看的短文，不要列表。",
    ),
    (
        "outage",
        "把下面这句改写成发给全体同事的一段话，不要列表：明天下午两点机房停电，请提前保存文件。",
    ),
    (
        "search",
        "用一段话说明：搜索没有新结果时，应该停下来告诉用户，而不是换个说法再搜一遍。不要列表。",
    ),
    (
        "cloud",
        "给一位创始人讲这个取舍：对话记在云上方便换设备，文件仍留在他自己的电脑上。不要列表。",
    ),
    (
        "pitch",
        "给投资人写一段，讲为什么值得做一个「你只跟一位负责人说，复杂的事由他去组人干」的产品。",
    ),
    (
        "collab",
        "写一段开场，说明为什么几个人一起干，往往比一个人埋头干更强。",
    ),
    (
        "postmortem",
        "把这次失败写成给团队的短复盘：功能上线了，但用户不知道从哪开始，三天后几乎没人再用。",
    ),
    (
        "say-first",
        "解释这个工作原则：先把要做什么说清楚，再动手。对象是新来的同事。",
    ),
    (
        "homepage",
        "写产品首页的一段介绍。产品能让人在对话里把一件事交给负责人，看别人怎么做，最后收下结果。",
    ),
    (
        "automate",
        "劝一位同事：不要把每件重复的事都做成自动执行。有些步骤停下来问一句，比闷头跑完更值。",
    ),
    (
        "vision",
        "写一段给全公司的话，讲我们为什么存在。",
    ),
    (
        "slow",
        "写一段短文：为什么有时候慢下来，比赶完更重要。",
    ),
    (
        "trust",
        "给新同事讲什么是信任。",
    ),
    (
        "craft",
        "写一段关于把一件事做完的意义。",
    ),
    (
        "pivot",
        "写一则对内通报：原来准备做自动重试，现在决定先让人点一下。说明为什么改。",
    ),
    (
        "sunset",
        "写一段产品下线通知，语气郑重。产品是一个内部周报工具，下周五停止。",
    ),
    (
        "less",
        "把「少即是多」写成给设计团队的一段话。",
    ),
    (
        "restraint",
        "向董事会解释：我们故意不把所有操作都放开。这是一种克制，不是能力不够。",
    ),
    (
        "entropy",
        "给不懂物理的人解释什么是熵。",
    ),
    (
        "interest",
        "给刚工作的人解释什么是复利。",
    ),
    (
        "debt",
        "给不写代码的负责人解释什么是技术债。",
    ),
    (
        "tradeoff",
        "解释什么是机会成本。",
    ),
    (
        "cache",
        "给非技术同事解释什么是缓存。",
    ),
    (
        "latency",
        "解释延迟和吞吐有什么差别。",
    ),
    (
        "consensus",
        "解释一群人怎么就算达成了共识。",
    ),
    (
        "protocol",
        "解释什么是协议。可以举计算机里的例子，也可以举生活里的例子。",
    ),
)

_JUDGE_MANNERED = """\
你在检查一段给用户看的回复里有没有 mannered prose（拿腔拿调的散文）。
定义：用隐喻或花样代替直说。本可以写字面说法，却写成展示作者的说法，让读者多费劲才懂。
普通的清楚解释、必要的术语、短句，不算。
只输出 JSON，不要其它字：{"mannered": 0 或 1, "quote": "若为 1，摘出那句花样；若为 0，空字符串"}"""

# OpenAI GPT-6「Personality and writing style」技术沟通句的中文。只测这一句，不叠方案 1。
SCHEME3_LINE = (
    "用白话，少用黑话。技术细节只留到能把事情说清楚。"
    "深浅按用户这句话里已经表现出来的程度。"
)

_JUDGE_JARGON = """\
你在检查一段给用户看的回复里有没有黑话。
黑话：用户这句里没用过的压缩说法、自造词、行话，读者要先解码才能照着做。
用户已经用过的词，以及这句话本身就说清了的术语，不算。拿腔拿调的隐喻不算黑话。
只输出 JSON，不要其它字：{"jargon": 0 或 1, "quote": "若为 1，摘出那处黑话；若为 0，空字符串"}"""


def _envelope(line: str) -> str:
    return f"{TURN_ENVELOPE_FENCE}\n{line}"


def _parse_judge(text: str, flag_key: str) -> tuple[int | None, str]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match is None:
            return None, raw[:200]
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None, raw[:200]
    flag = data.get(flag_key)
    if isinstance(flag, bool):
        mannered: int | None = int(flag)
    elif flag in (0, 1, "0", "1"):
        mannered = int(flag)
    else:
        mannered = None
    return mannered, str(data.get("quote") or "")


async def _complete(provider, model: str, messages: list[LLMMessage], *, max_tokens: int) -> str:
    response = await provider.complete(
        LLMRequest(
            messages=messages,
            model=model,
            temperature=0.7 if max_tokens > 256 else 0.0,
            max_tokens=max_tokens,
            tools=None,
            tool_choice="none",
            stream=False,
            thinking=False,
            scenario="eval.mannered_prose_ab",
        )
    )
    return (response.content or "").strip()


def _mark(text: str, quote: str) -> str:
    raw = text or ""
    needle = (quote or "").strip()
    if not needle or needle not in raw:
        return html.escape(raw)
    head, _, tail = raw.partition(needle)
    return f"{html.escape(head)}<mark>{html.escape(needle)}</mark>{html.escape(tail)}"


def _badge(flag: int | None, hit_label: str) -> str:
    if flag == 1:
        return f'<span class="badge hit">{hit_label}</span>'
    if flag == 0:
        return '<span class="badge ok">直说</span>'
    return '<span class="badge miss">未判</span>'


def _render_page(report: dict, flag_key: str) -> str:
    hit_label = "黑话" if flag_key == "jargon" else "拿腔"
    cards: list[str] = []
    for index, row in enumerate(report["rows"], start=1):
        off_flag = row.get(f"off_{flag_key}")
        on_flag = row.get(f"on_{flag_key}")
        cards.append(
            f"""
<article>
  <header>
    <span class="idx">{index}</span>
    <h2>{html.escape(row["id"])}</h2>
  </header>
  <p class="q">{html.escape(row["question"])}</p>
  <div class="cols">
    <section class="off">
      <div class="colhead">关 <span>{len(row["off"])} 字</span> {_badge(off_flag, hit_label)}</div>
      <div class="body">{_mark(row["off"], row["off_quote"])}</div>
    </section>
    <section class="on">
      <div class="colhead">开 <span>{len(row["on"])} 字</span> {_badge(on_flag, hit_label)}</div>
      <div class="body">{_mark(row["on"], row["on_quote"])}</div>
    </section>
  </div>
</article>"""
        )
    off_hits = report.get(f"off_{flag_key}", 0)
    on_hits = report.get(f"on_{flag_key}", 0)
    scored = report.get("scored", 0)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>方案 {html.escape(str(report.get("scheme", "")))} 对照</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; font: 16px/1.65 "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: #f4f1ea; color: #1c1915; }}
  header.top {{ position: sticky; top: 0; background: #1c1915; color: #f4f1ea; padding: 16px 28px; z-index: 1; }}
  header.top h1 {{ margin: 0 0 6px; font-size: 20px; font-weight: 600; }}
  header.top p {{ margin: 0; color: #d9d3c7; }}
  main {{ max-width: 1180px; margin: 0 auto; padding: 20px 20px 48px; }}
  article {{ background: #fff; border-radius: 12px; padding: 18px 18px 8px; margin: 0 0 16px; box-shadow: 0 1px 2px rgba(28,25,21,.06); }}
  article header {{ display: flex; gap: 10px; align-items: baseline; }}
  .idx {{ color: #8a8175; font-variant-numeric: tabular-nums; }}
  h2 {{ margin: 0; font-size: 15px; letter-spacing: .04em; }}
  .q {{ margin: 8px 0 12px; color: #3f3830; }}
  .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
  .off, .on {{ border-radius: 10px; padding: 12px 14px 16px; min-width: 0; }}
  .off {{ background: #f7f5f1; }}
  .on {{ background: #eef4ea; }}
  .colhead {{ display: flex; gap: 8px; align-items: center; font-weight: 650; margin-bottom: 8px; }}
  .colhead span {{ font-weight: 400; color: #6d655c; }}
  .body {{ white-space: pre-wrap; word-break: break-word; }}
  mark {{ background: #f3d48a; padding: 0 2px; }}
  .badge {{ margin-left: auto; font-size: 12px; font-weight: 600; border-radius: 999px; padding: 2px 8px; }}
  .hit {{ background: #f3d48a; color: #3f2d08; }}
  .ok {{ background: #d7e8cf; color: #1d3a22; }}
  .miss {{ background: #ece7df; color: #5c564e; }}
  @media (max-width: 800px) {{ .cols {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<header class="top">
  <h1>方案 {html.escape(str(report.get("scheme", "")))} · 关 {off_hits}/{scored} · 开 {on_hits}/{scored}</h1>
  <p>{html.escape(report.get("model", ""))} · 开臂只多这一句：{html.escape(report.get("line", ""))}</p>
</header>
<main>
{"".join(cards)}
</main>
</body>
</html>
"""


async def _run(model_override: str, out_path: Path, scheme: str, ids: set[str]) -> int:
    creds = await eval_credentials()
    model = model_override or creds.default_model
    provider = build_provider(creds)
    system = _DEFAULT_SYSTEM_PROMPT.strip()
    if scheme == "3":
        line = SCHEME3_LINE
        judge = _JUDGE_JARGON
        flag_key = "jargon"
    else:
        line = MANNERED_PROSE_LINE
        judge = _JUDGE_MANNERED
        flag_key = "mannered"
    rows: list[dict] = []
    prompts = [item for item in PROMPTS if not ids or item[0] in ids]
    if ids and len(prompts) != len(ids):
        known = {item[0] for item in PROMPTS}
        missing = ", ".join(sorted(ids - known))
        raise RuntimeError(f"未知题目: {missing}")
    try:
        for prompt_id, question in prompts:
            off = await _complete(
                provider,
                model,
                [
                    LLMMessage(role="system", content=system),
                    LLMMessage(role="user", content=question),
                ],
                max_tokens=512,
            )
            on = await _complete(
                provider,
                model,
                [
                    LLMMessage(role="system", content=system),
                    LLMMessage(role="user", content=_envelope(line)),
                    LLMMessage(role="user", content=question),
                ],
                max_tokens=512,
            )
            off_flag, off_quote = _parse_judge(
                await _complete(
                    provider,
                    model,
                    [
                        LLMMessage(role="system", content=judge),
                        LLMMessage(role="user", content=off),
                    ],
                    max_tokens=256,
                ),
                flag_key,
            )
            on_flag, on_quote = _parse_judge(
                await _complete(
                    provider,
                    model,
                    [
                        LLMMessage(role="system", content=judge),
                        LLMMessage(role="user", content=on),
                    ],
                    max_tokens=256,
                ),
                flag_key,
            )
            row = {
                "id": prompt_id,
                "question": question,
                "off": off,
                "on": on,
                f"off_{flag_key}": off_flag,
                f"on_{flag_key}": on_flag,
                "off_quote": off_quote,
                "on_quote": on_quote,
            }
            rows.append(row)
            print(
                f"{prompt_id}: off={off_flag} on={on_flag}",
                flush=True,
            )
    finally:
        await provider.close()

    scored = [
        r
        for r in rows
        if r[f"off_{flag_key}"] is not None and r[f"on_{flag_key}"] is not None
    ]
    off_hits = sum(r[f"off_{flag_key}"] for r in scored)
    on_hits = sum(r[f"on_{flag_key}"] for r in scored)
    report = {
        "model": model,
        "credential_source": creds.source,
        "scheme": scheme,
        "line": line,
        "judge": flag_key,
        "thinking": False,
        "n": len(rows),
        "scored": len(scored),
        f"off_{flag_key}": off_hits,
        f"on_{flag_key}": on_hits,
        "rows": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    page = out_path.with_suffix(".html")
    page.write_text(_render_page(report, flag_key), encoding="utf-8")
    print(
        f"{flag_key} off {off_hits}/{len(scored)}  on {on_hits}/{len(scored)}  model={model}  -> {out_path}",
        flush=True,
    )
    print(f"page {page.resolve()}",
        flush=True,
    )
    if "free" in model:
        print("免费档只作观察，不下「压不住」的结论。", flush=True)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="拿腔拿调散文：信封一句的关/开对照")
    parser.add_argument("--model", default="", help="覆盖账号默认模型")
    parser.add_argument("--scheme", choices=("1", "3"), default="1")
    parser.add_argument("--ids", default="", help="只跑这些题，逗号分隔")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    out = args.out or Path(
        "eval-out/scheme3-jargon-ab.json"
        if args.scheme == "3"
        else "eval-out/mannered-prose-ab.json"
    )
    ids = {part.strip() for part in args.ids.split(",") if part.strip()}
    try:
        code = asyncio.run(_run(args.model.strip(), out, args.scheme, ids))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    raise SystemExit(code)


if __name__ == "__main__":
    main()
