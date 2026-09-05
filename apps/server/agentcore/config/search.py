"""Web search backend settings."""

from pydantic import BaseModel


class SearchSettings(BaseModel):
    searxng_url: str = "http://localhost:18888"
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"

    # PI-002 出网外泄硬守卫（默认关，仅观测）：开启后，web_fetch 对「本会话 web_search 未
    # surfaced 的新域名 + 携带较长查询参数」的请求直接拒绝（视为外泄信标），而非仅记
    # tool.web_fetch_novel_domain 告警日志。默认 False——只观测、不阻断，避免误伤用户直接
    # 粘贴或模型合法构造的长查询链接；运营方接受摩擦时再开。见 项目审计-提示注入专项 §五.
    web_fetch_block_novel_query: bool = False

    # Clash/Mihomo fake-IP 模式会把所有域名 DNS 应答成 198.18.0.0/15 占位 IP；SSRF 守卫
    # 若一律拦截该段会导致 web_fetch 在本地开发/代理环境下 100% 失败。开启后（默认开）仅
    # 放行该占位段、仍拦截真·私网/回环/链路本地；生产机无 fake-IP DNS 时本开关无影响。
    # 若需最严 SSRF（不信任本机代理路由），设为 false 并把代理改 redir-host / 关 fake-ip。
    web_fetch_allow_fake_ip_proxy: bool = True
