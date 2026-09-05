# PoC: L3 "Agent 云端浏览器" gVisor 沙箱可行性验证

> 探路验证，**非产品代码**。全部自包含在本目录；不改产品 Dockerfile / `gvisor.py` / deploy / CI。
> 宿主 Windows + Docker Desktop（linux engine）。所有 runsc 步骤需 `--privileged` 容器。

## 核心问题

「依赖预装在镜像、沙箱经 ro-bind 可见」的现有沙箱形态（见 `agentcore/tools/sandbox/gvisor.py`）
加上网络后，能否跑通 **headless Chromium（Playwright 驱动）：navigate(公网) → accessibility 快照 → 截图**。

## 结论：三层全部通过（systrap 与 ptrace 均可）

| 层 | 内容 | 结果 | 关键数据（本机单跑） |
|---|---|---|---|
| L0 | privileged 容器内 runsc 起最小沙箱（`runsc do` + 产品同形空 rootfs + ro host binds） | ✅ 通过（systrap & ptrace rc=0） | — |
| L1 | 直接 Playwright+Chromium 跑链路（无 runsc） | ✅ 200 / 有效 PNG / a11y | 峰值 **376 MB**，总 **1.2s**（冷启 153ms） |
| L2 | 同链路放进 runsc（gvisor.py 同形 OCI + 浏览器 bind + `--network=host`） | ✅ 200 / 与 L1 像素一致 PNG / a11y | systrap 峰值 **0.8–1.1 GB**、总 **~3.3s**；ptrace 峰值 **~0.48 GB** |

必需 Chromium flags（仅此三项即可，无需 `--single-process` 等）：
`--no-sandbox --disable-dev-shm-usage --disable-gpu`

镜像：base `python:3.12-slim`（Debian 13 trixie，与服务端同代）119 MB → PoC **1.451 GB**，增量 **~1.33 GB**
（浏览器 bundle 646 MB + runsc 126 MB + `--with-deps` 的 X/mesa/xvfb 等 apt 依赖 ~0.56 GB；headless 其实不需 xvfb，可再瘦）。
Playwright 1.61.0 / Chromium 149.0.7827.55 / runsc release-20260714.0（spec 1.2.1）。

## 如何复跑

```bash
# 构建（长步骤：Chromium ~177MB 下载）
docker build -t poc-browser-gvisor apps/server/scripts/poc_browser_gvisor

# L0 环境探针
docker run --rm --privileged poc-browser-gvisor bash /poc/probe_l0.sh

# L1 基线（默认 bridge 有公网即可；截图落到 out/）
docker run --rm -v "$PWD/apps/server/scripts/poc_browser_gvisor/out:/out" \
  poc-browser-gvisor bash /poc/run_l1.sh

# L2 核心（privileged 起 runsc；runsc --network=host 共享容器网络）
docker run --rm --privileged -v "$PWD/apps/server/scripts/poc_browser_gvisor/out:/out" \
  poc-browser-gvisor bash /poc/run_l2.sh
# 换平台：加 -e POC_PLATFORM=ptrace
```

## L2 相对现有 OCI 配置（`gvisor.py::_build_oci_config`）的差异

1. **新增只读 bind `/opt/ms-playwright`**：Playwright 的 Chromium bundle 在 `/usr` 之外，现有 5 条
   host bind（`/usr /lib /lib64 /bin /etc`）看不到它 → 必须额外挂载（或把浏览器安装到 `/usr` 下复用现有 bind）。
2. **`/tmp` tmpfs 扩容**：产品 `size=64m` 不够（`--disable-dev-shm-usage` 让 shm 落到 /tmp）；PoC 用 `size=512m` + 显式 `mode=1777`。
3. **内存上限**：`gvisor_memory_limit_mb=512` 远不够，systrap 实测 0.8–1.1 GB → 需抬到 ~1.5–2 GB/会话。
4. **pids 上限**：`pids_limit=128` 偏低（Chromium 多进程多线程），PoC 用 512。
5. **CPU**：`cpu_limit=1.0`（1 核）偏紧，PoC 用 2 核。
6. **网络**：产品默认 `--network=none`；restricted 档加 network ns + netstack。PoC 用 `runsc --network=host`
   （最宽松、无隔离）打通，**生产网络设计见下方缺口**。
7. **命令/解释器**：沿用系统 `python3`（`/usr/local` 在 `/usr` bind 内可见）；Playwright 及其自带 node 在系统
   site-packages 内 → 无需再装 node。字体/CA 证书经 `/usr`、`/etc` bind 天然可见（Latin 渲染正常）。

## 生产化缺口清单

- **网络隔离 + SSRF**：`--network=host` 无隔离。生产应走 netstack（restricted）+ 出口过滤（禁 RFC1918 / 169.254.169.254 等）；
  沙箱内 raw socket 绕过应用层 `core/net.py` 的 SSRF 护栏（`gvisor.py` 已注明多租户边界靠 gVisor 隔离）。
- **会话级长驻生命周期（最大架构缺口）**：现有沙箱是「每次调用即建即毁」+ 60s 超时；浏览器 Agent 需要跨多次工具调用
  在**同一 page/context** 上连续操作 → 需长驻沙箱 + 控制通道（CDP/Playwright server 从宿主代理进沙箱）+ 空闲/最长寿命回收。
- **内存与并发**：~1 GB/会话（systrap）。`gvisor_max_concurrent_executions=2` 与内存上限需按会话内存重算
  （如 8GB 节点约 6–8 并发）。ptrace 省内存但为旧平台、syscall 开销高，systrap 为默认推荐。
- **字体**：产品已装 fonts-noto-cjk；需验证任意网页的 CJK/emoji/多语种覆盖；只读 rootfs 下 fontconfig 缓存不可写（可选给可写缓存目录）。
- **镜像体积/冷启**：+1.33 GB（可去 xvfb 等瘦身）；每会话浏览器冷启 ~0.6s + runsc 启动，进一步印证应走长驻会话而非每调用重启。

## 七、M0 通道验证结论（2026-07-20，`run_channel.py`，`CHANNEL_OK=True`）

D9（长驻 stdio JSON-RPC）+ D10（restricted netstack + 宿主过滤代理）**端到端跑通**，12 项断言全绿。这是进产品代码前的强制门。

| 断言 | 结果 | 证据 |
|---|---|---|
| 长驻 runsc 容器 + stdio JSON-RPC | ✅ | 一个 Chromium 跨 navigate/snapshot/screenshot/ping 多命令长活；`ping_liveness` 在多命令后仍通 |
| 沙箱经 netstack 触达宿主代理 | ✅ | `proxy_reachable`：沙箱内 raw connect 到 `10.200.0.1:8888` 成功；netdiag 显示 netstack 克隆了 veth + `default via 10.200.0.1` |
| 代理 → 公网 | ✅ | example.com / iana.org 均 HTTP 200，关键帧为真实「Example Domain」渲染（1280 宽 jpeg，14KB） |
| **无直连绕过** | ✅ | `no_direct_egress`：沙箱内 raw connect 到 `1.1.1.1:443` **超时**（无 NAT/转发，唯一出口是代理） |
| 代理 SSRF 拦截元数据 | ✅ | `169.254.169.254` → 代理 403 |
| 代理 SSRF 拦截私网 | ✅ | `10.199.0.9` → 代理 403 |

**产品化关键发现（进 M0 实现须落地）**：

1. **netstack 必须用 OCI netns path**：`ip netns exec <ns> runsc --network=sandbox` 单独用**不生效**（沙箱内 `ip addr` 为空、`Network is unreachable`）；必须在 OCI `namespaces` 里给 `{"type":"network","path":"/var/run/netns/<ns>"}`，netstack 才会克隆该 netns 的接口 + 路由。
2. **出口隔离拓扑**：专用 netns + veth pair，宿主端跑代理、**不开 NAT/IP 转发** → 沙箱唯一可达的链外地址就是代理。这满足 D10「网络层强制」（沙箱内 raw socket 也无法绕过，因根本无路由）。
3. **代理 = SSRF 执行点**：代理侧解析 DNS 并拒私网/链路本地/元数据；产品代理复用 `core/net.py`（`classify_url`/`ip_is_safe`/`PinnedIPTransport`），不用本 PoC 的简化实现。
4. **Chromium link-local 怪癖**：Chromium 不把 `169.254.x.x` 走代理（直连并挂起）——元数据拦截改用「沙箱内 HTTP 经代理」路径验证（`proxy_fetch`）。产品里 raw netstack 无 link-local 路由 + 代理拒绝，双保险。
5. **dev 环境 fake-IP**：本机 Docker Desktop 走 Clash/Mihomo fake-IP DNS（公网名解析成 `198.18.x.x`）；代理需按 `core/net.py` 的 `web_fetch_allow_fake_ip_proxy` 放行该段才能在 dev 触达真公网（真私网仍拦）。
6. **cgroups**：Docker Desktop 嵌套 cgroup v1 下非 rootless runsc 需 `--ignore-cgroups`（pids 控制器未委派）；生产宿主 cgroup 委派正常则不需要（限额在会话 OCI/宿主侧另加）。

复跑：`docker run --rm --privileged -v <pocdir>:/poc -v <pocdir>/out:/out poc-browser-gvisor python3 -u /poc/run_channel.py`

## 八、M1 直播帧源门结论（2026-07-20，`run_screencast.py`，`SCREENCAST_OK=True`）

D14 强制门：`playwright.async_api` + CDP `Page.startScreencast` 在 gVisor（systrap）沙箱内**可用**。
离线动画页（inline HTML，无网络）+ 帧 ack 背压，跑通「起播 → 逐帧推 stdio 事件行 → ack → 停播」，
两次复跑一致。**结论：screencast 在 gVisor 完全可行，帧率与单帧体积均远优于直播所需，无需回退轮询 screenshot。**

| 指标 | 实测（两次复跑） | 备注 |
|---|---|---|
| 帧率 | **57.3 / 57.7 fps** | jpeg q60、1280×800、`everyNthFrame=1`、逐帧 ack；远超「可接受」门槛（本门 MIN_FPS=5） |
| 单帧体积 | 均值 **13.7–13.8 KB**、p95 **14.2 KB**、max 14.4 KB | jpeg q60；base64 后约 ×1.34 ≈ 18–19 KB/帧 |
| 5s 窗口帧数 | 287 / 289 | driver 自报与宿主计数一致 |
| ready 握手 / 有效 JPEG | ✅ | 首帧为起播前白屏（正常），`screencast-sample.jpg` 为动画内容帧 |

**产品驱动回归**（`probe_product_driver.py`，`PRODUCT_DRIVER_OK=True`）：把**真实产品** driver
（`agentcore/tools/sandbox/browser/driver.py`，M1 已改写为 `async_playwright`）放进 runsc 跑通
`ready → start_screencast →（navigate data: 动画页）→ 直播帧并发 → stop_screencast → snapshot →
screenshot（内联关键帧）→ ping → close`：2s 窗口 120 帧（帧 7.6KB/1280×800）、stop 后立即零帧、
内联关键帧 8.2KB、六命令语义不变。**证明异步改写在 gVisor 内命令与推帧并发无死锁、契约兼容**。

**产品化取舍（进 M1 实现须落地）**：门证明「能力上限」57fps；直播不需要也不应跑满——
生产用 `everyNthFrame` / `maxWidth` / ack 背压把速率压到直播够用档（观看体验 + 带宽平衡），
并保持「≥1 观看者才起播、最后一个断开即停」的零开销原则（见提案 D13–D15）。
帧走独立非 journal 旁路（base64 EPHEMERAL SSE），不违反「事件流不传二进制」的 journal 铁律。

复跑：`docker run --rm --privileged -v <pocdir>:/poc -v <pocdir>/out:/out poc-browser-gvisor python3 -u /poc/run_screencast.py`
（换平台加 `-e POC_PLATFORM=ptrace`；调窗口/质量加 `-e DURATION_S=8 -e BROWSER_JPEG_Q=60`）

## 文件清单

- `Dockerfile` — base `python:3.12-slim` + runsc + Playwright/Chromium（系统 site-packages，bundle 到 `/opt/ms-playwright`）
- `browser_task.py` — 浏览器链路：navigate → a11y 快照 → 截图，输出 `POC_METRICS_JSON`
- `memwatch.py` — 跨 cgroup 版本的全容器 Pss 峰值采样器（L1/L2 同口径）
- `make_oci.py` — 生成与 `gvisor.py` 同形的 runsc OCI bundle（含 `--minimal` 供 L0）
- `probe_net.py` — 构建前镜像源连通性探针
- `probe_l0.sh` / `run_l1.sh` / `run_l2.sh` — 三层驱动脚本
- `browser_driver.py` — 沙箱侧长驻 driver：stdin/stdout JSON-lines RPC 驱动一个长活 Chromium（launch/navigate/snapshot/screenshot/click/type/scroll/ping/proxy_fetch/raw_connect/netdiag）
- `ssrf_proxy.py` — 宿主侧 SSRF 过滤代理（HTTP + CONNECT，自包含镜像 `core/net.py` 策略；产品代理复用真 `core/net.py`）
- `run_channel.py` — M0 通道验证编排：建 netns+veth、起代理、长驻 runsc 驱 RPC、断言并出 `CHANNEL_OK`
- `screencast_driver.py` — M1 帧源门沙箱侧：`async_playwright` + CDP `Page.startScreencast`，离线动画页逐帧推 stdio 事件行 + 帧 ack
- `run_screencast.py` — M1 帧源门编排：无网络 runsc 跑 screencast driver、计帧率/单帧体积、存样帧、出 `SCREENCAST_OK`
- `probe_product_driver.py` — M1 产品驱动回归：把真实 `driver.py`（异步改写）放进 runsc 跑通六命令+直播并发，出 `PRODUCT_DRIVER_OK`（须 `-v <repo>/apps/server/agentcore/tools/sandbox/browser:/product_browser:ro`）
- `out/` — 截图产物（gitignored）
