"""出网健康探针 —— DNS 解析 + SSRF 判定 + web_fetch 端到端真读，一处验证联网取证层是否通。

诊断场景：辩论 / web_search / web_fetch「无据可依」，或迁到新机器 / 服务器时先验证出网。
典型故障：本机代理的 **fake-IP 模式**（Clash/Mihomo/Surge/v2rayN）把所有域名 DNS 应答成
``198.18.0.x`` 占位 IP（RFC 2544 保留段）→ 应用自解析拿到保留 IP → SSRF 守卫（行为正确）
全拦。本探针能一眼认出这个签名，并在改回 ``redir-host`` / 关 fake-ip 后确认恢复。

从 apps/server 下跑：

    uv run python scripts/archive/probe_egress.py                 # 默认目标（CN + 国际各若干）
    uv run python scripts/archive/probe_egress.py https://a.com https://b.com   # 自定义目标

它对每个 URL：① 用应用同路径 ``_getaddrinfo`` 解析、标出 fake-IP / 保留段；② 跑
``classify_url`` 的 SSRF 判定；③ 判定放行则调**真实 ``WebFetchTool``** 端到端读一次。末尾给
PASS/FAIL 汇总。见 .cursor/rules/conversation-logs.mdc、docs/02-架构/本地开发.md（fake-IP 排障）。
"""

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_TARGETS = [
    "https://www.gov.cn/",
    "https://www.baidu.com/",
    "https://www.163.com/",
    "https://en.wikipedia.org/wiki/Main_Page",
]


def _ip_tag(ip: str) -> str:
    """人读标签：public / FAKE-IP / reserved-or-private。"""
    from agentcore.core.net import ip_is_safe, is_fake_ip_proxy_signature

    if is_fake_ip_proxy_signature(ip):
        return "FAKE-IP(198.18/15)"
    if not ip_is_safe(ip):
        return "reserved/private"
    return "public"


async def _probe_one(url: str) -> dict:
    from agentcore.core.net import _getaddrinfo, classify_url
    from agentcore.tools.builtin.web.web_fetch import WebFetchTool
    from agentcore.tools.protocol import ToolContext

    host = (urlparse(url).hostname or "").lower()
    row: dict = {"url": url, "host": host, "ips": [], "fake_ip": False, "verdict": "?", "fetch": ""}

    # ① DNS（应用同路径）
    try:
        ips = await _getaddrinfo(host)
    except Exception as e:  # noqa: BLE001
        ips = []
        row["fetch"] = f"(resolve error: {type(e).__name__}: {e})"
    tagged = []
    for ip in ips:
        tag = _ip_tag(ip)
        tagged.append(f"{ip}({tag})")
        if tag.startswith("FAKE-IP"):
            row["fake_ip"] = True
    row["ips"] = tagged

    # ② SSRF 判定
    block = await classify_url(url)
    row["verdict"] = block.name if block is not None else "ALLOW"

    # ③ 放行才真读
    if block is None:
        ctx = ToolContext.create(
            execution_id="probe",
            run_id="probe",
            agent_id="probe",
            backend=None,  # web_fetch 只用 conversation_id，不碰 backend
            user_id="probe",
            conversation_id="",
        )
        try:
            res = await WebFetchTool().execute({"url": url, "max_chars": 800}, ctx)
            if res.success:
                meta = res.metadata or {}
                row["fetch"] = f"ok  title={meta.get('title', '')!r}  chars={meta.get('content_chars')}"
                row["ok"] = True
            else:
                row["fetch"] = f"FAIL: {res.error}"
        except Exception as e:  # noqa: BLE001
            row["fetch"] = f"EXC: {type(e).__name__}: {e}"
    return row


async def main() -> None:
    targets = sys.argv[1:] or DEFAULT_TARGETS
    print("=" * 78)
    print("出网健康探针（DNS + SSRF + web_fetch 端到端）")
    print("=" * 78)
    rows = [await _probe_one(u) for u in targets]

    for r in rows:
        print(f"\n[{r['host']}]")
        print(f"  DNS   -> {', '.join(r['ips']) or '(none)'}")
        print(f"  SSRF  -> {r['verdict']}")
        print(f"  FETCH -> {r['fetch'] or '(skipped: blocked)'}")

    any_fake = any(r["fake_ip"] for r in rows)
    ok_reads = sum(1 for r in rows if r.get("ok"))
    blocked_fake = any_fake and any(r["verdict"] == "PRIVATE_IP_FAKE_PROXY" for r in rows)
    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"  fake-IP 检出: {'YES' if any_fake else 'NO'}")
    print(f"  web_fetch 成功: {ok_reads}/{len(rows)}")
    if blocked_fake:
        print(
            "  => FAIL：本机代理仍在 fake-IP 模式且 WEB_FETCH_ALLOW_FAKE_IP_PROXY=false。\n"
            "     处置：设 WEB_FETCH_ALLOW_FAKE_IP_PROXY=true（默认已开），或代理关 fake-ip / 改 redir-host。"
        )
    elif any_fake and ok_reads == len(rows):
        print(
            "  => PASS（fake-IP + allow 模式）：占位 DNS 经本地代理路由，web_fetch 端到端可读。"
        )
    elif ok_reads == len(rows):
        print("  => PASS：DNS 落真实公网 IP、SSRF 放行、web_fetch 端到端可读。取证层健康。")
    else:
        print("  => 部分失败：DNS 已正常但仍有读取失败，见各条 FETCH（可能反爬/超时/单站问题）。")


if __name__ == "__main__":
    asyncio.run(main())
