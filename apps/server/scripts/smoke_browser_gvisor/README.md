# Smoke: L3 团队浏览器 M0+M1+M2 —— 真机 gVisor **产品模块**端到端冒烟

> 与 `scripts/poc_browser_gvisor`（探路用的**同形副本**）不同：本目录直接驱动 **产品模块**
> （`agentcore/tools/sandbox/browser/*`、`runtime/browser/*`、`tools/builtin/browser.py`），
> 在真 `runsc` + 真产品镜像上验证整条服务端浏览器栈。这是内置浏览器 / Agent 浏览器能力
> 落地后此前唯一未验证的环节——产品宿主侧编排从未在 gVisor 里跑过，`Dockerfile` 的
> `INSTALL_BROWSER=1` 层也从未构建过。
>
> 宿主 Windows + Docker Desktop（linux engine）。所有步骤需 `--privileged` 容器。

## 结论：三次复跑全绿（`SMOKE_OK=True`，25 项断言）

驱动链路（全部产品代码）：

```
tools/builtin/browser.py（六工具）
  → runtime/browser/registry.py  BrowserSessionRegistry.acquire
    → tools/sandbox/browser/gvisor_session.py  open_gvisor_browser_session
      → netns.py（真 netns+veth）+ proxy.py（真 SSRF 过滤代理，复用 core/net.py）
        + oci.py（会话 OCI，netns-path 挂载）+ runsc --platform=systrap --network=sandbox
          + driver.py（沙箱内长驻 async Playwright Chromium）
runtime/browser/live.py  BrowserLiveHub（M1 直播帧扇出）
driver 的 CDP Input 注入（M2 接管，与 …/browser/input 端点同一 session.send 路径）
```

版本：Playwright **1.61.0** / Chromium **149.0.7827.55** / runsc（gVisor latest, systrap）。

## 如何复跑

```powershell
# 1) 构建产品镜像（INSTALL_BROWSER=1；自定 tag，别覆盖他人镜像）。
#    首次含 Chromium ~177MB 下载，耐心等；本机实测全程 ~161s（runsc 层命中缓存）。
docker build -t agentcore-server:browser-smoke --build-arg INSTALL_BROWSER=1 apps/server

# 2) privileged 容器内跑产品模块端到端冒烟。
#    GVISOR_ENABLED=true 让 registry 走真 gVisor 沙箱工厂；
#    BROWSER_SANDBOX_IGNORE_CGROUPS=true 为 Docker Desktop 嵌套 cgroup v1（PoC finding #6）；
#    web_fetch_allow_fake_ip_proxy 默认 True，dev Clash fake-IP 下代理才能触达真公网（PoC finding #5）。
docker run --rm --privileged --user root `
  -e GVISOR_ENABLED=true -e BROWSER_SANDBOX_IGNORE_CGROUPS=true `
  -e SMOKE_OUT=/smoke/out `
  -v "${PWD}\apps\server\scripts\smoke_browser_gvisor:/smoke" `
  agentcore-server:browser-smoke python /smoke/smoke_product_e2e.py
```

输出 `SMOKE_METRICS_JSON=...`（逐项数据）+ `SMOKE_OK=True/False`；关键帧/直播样帧落 `SMOKE_OUT`。
（Docker Desktop 的 Windows bind-mount 有时不把容器内写回 host；要取产物改 `SMOKE_OUT=/out`
+ 去掉 `--rm`，跑完 `docker cp <容器>:/out ./out`。）

> **为什么用 `--user root`**：镜像默认 `USER app`，而每会话 netns+veth 由宿主侧 `ip` 创建、
> `runsc` 需特权。生产（真 gVisor 部署）同样需要 `CAP_NET_ADMIN` + 沙箱运行权限（部署配置，非代码）。

## 验收断言逐条结果（本机实测，三次一致）

| # | 断言 | 结果 | 关键数据 |
|---|---|---|---|
| 1 | `apps/server/Dockerfile` `INSTALL_BROWSER=1` 构建成功 | ✅ | Chromium 149.0.7827.55 + Playwright 1.61.0 真实下载；镜像 `agentcore-server:browser-smoke`。**修复见下** |
| 2 | `BrowserSessionRegistry.acquire` → 真 netns+veth+代理+runsc（冷启耗时） | ✅ | 冷启 **2.1–2.6s**；netns `acbrw0`、veth `acbrwh0@if3 link-netns acbrw0`、host_ip `10.201.0.1`、代理 `0.0.0.0:8899`、runsc 容器 `agentcore-browser-*` running |
| 3 | 六工具真跑：navigate 公网 → snapshot(a11y 非空) → click/type/scroll → screenshot；关键帧落盘非空 | ✅ | example.com **HTTP 200**；snapshot elements 18 字 / aria **232 字**（非空）；type→`smoke-typed-123` 快照可见；click→`BTN_CLICKED` 快照可见；scroll ok；**6 张关键帧** `step-0001..0006.jpg` 全 JPEG(0xFFD8) 非空（7.0–16.3 KB） |
| 4 | M1 直播：live hub 收到连续帧（帧率/单帧体积）；stop 后停帧 | ✅ | status `started`；4s 窗口 **117–118 帧 ≈ 29 fps**（everyNthFrame=2）；单帧 **~7.2 KB** jpeg；detach+grace 后 `screencast_on=false`、零新帧 |
| 5 | M2 接管：input 注入鼠标点击 + 键盘输入并生效 | ✅ | 鼠标注入 3 事件→按钮 onclick 触发（快照见 `CLICKED_OK`）；键盘：点击聚焦+insertText 3 事件→输入框值（快照见 `hi-takeover-9`） |
| 6 | SSRF 负面：沙箱内经代理访问元数据 + 私网被拒 | ✅ | `metadata.google.internal`→代理 **403 BLOCKED_HOST**；`10.199.0.9`→代理 **403 PRIVATE_IP**（均经沙箱→代理真路径，`on_decision` 记录 allowed=false）；`169.254.169.254` 经产品 `resolve_dial_target` → 拒（None/PRIVATE_IP） |
| 7 | 干净拆除：`registry.close` 后 runsc/netns/veth 全消失 | ✅ | close 前 netns/veth/runsc 各 1；close 后**全空**（netns `[]`、veth `[]`、runsc `[]`）。拆除耗时经 A/B 修复后 **0.3s**（`runsc delete --force` 0.0s；原 ~120s），见风险①（已解决） |

> a11y 快照说明：产品 driver 的快照是「可交互元素 ref 列表（`[e1] a: …`）+ ARIA 结构文本」双通道；
> example.com 的可交互元素只有 1 个链接，故 elements 短（18 字）但 aria 达 232 字——都非空，符合契约。

## 冒烟中发现并修复的产品缺口（执行层小修）

**`apps/server/Dockerfile` 的 `INSTALL_BROWSER=1` 层缺 `iproute2`。**

- 根因：`netns.py` 在**宿主侧** shell 调用 `ip netns/link` 建每会话 netns+veth（D10 出口隔离），
  但 base `python:3.12-slim` + `playwright install --with-deps chromium` 的 apt 依赖都不含 `ip`。
  该层从未构建过，故这条依赖此前无人发现；PoC 镜像自己装了 `iproute2`（`poc_browser_gvisor/Dockerfile`
  第 32 行）才跑通。不修则 `acquire` 在 `ip netns add` 处 `FileNotFoundError`，断言 2–7 全挂。
- 改动：`INSTALL_BROWSER=1` 分支内 `apt-get install -y --no-install-recommends iproute2`（语义中性，
  仅补运行时缺失的 OS 依赖，不改任何产品代码）。已装 `iproute2 6.15.0-1`，断言 2 起真 netns/veth 建立成功。

## 遗留风险

1. **拆除慢（已解决，~400× 提速：120.1s → 0.3s）。** 原 `GVisorBrowserSession.close()` 拆除单会话耗 ~120s，
   全落在 `runsc delete --force`；已 A/B 证伪定位并修复。

   **A/B 证伪假设 (b)**（本机 Docker Desktop 嵌套 gVisor，镜像 `agentcore-server:browser-smoke`，同一冒烟
   harness 挂载产品源码 `-v ...\agentcore:/app/agentcore` 复跑，仅变拆除顺序，其余不变）：

   | 变体 | `runsc kill` | `runsc delete --force` | `teardown_close_s` | 断言 |
   |---|---|---|---|---|
   | 基线（close RPC 后即 `process.kill()` SIGKILL 监督进程 → kill/delete） | 0.1s | **119.9s** | **120.1s** | 25 绿 |
   | 变体（close RPC 后先等 `runsc run` 监督进程自然退出 ≤10s，超时才 SIGKILL → kill/delete） | 0.0s | **0.0s** | **0.3s** | 25 绿 |

   → **假设 (b) 成立**：`close()` 抢先 SIGKILL 掉 `runsc run` 监督进程，会把沙箱变孤儿，逼出 runsc 的慢速强删路径；
   让容器收到 close RPC 后自然退出再 delete，`runsc delete` 秒回（与 PoC `run_channel.py` 让容器自然退出再 delete
   无延迟一致）。**已将自然退出顺序定为正式实现**：`close()` 发完 close RPC + `aclose()` 后，
   `_await_process_exit(_SUPERVISOR_EXIT_TIMEOUT=10s)` 等监督进程自然退出，仅超时才回退 SIGKILL，再 `runsc kill`/`delete`。
   影响面全部缓解：reaper 循环串行、lifespan 关停、删对话级联 close 不再被单会话拖 ~2min。

   假设 (a)（嵌套虚拟化 + `--ignore-cgroups` 下 gVisor 强清慢）**并非主因**——它解释不了「让监督进程自然退出后
   delete 立即秒回」；dev 嵌套环境至多是放大因子。真 cloud gVisor 宿主（cgroup 正常委派）预期同样受益。

   **确定性加固（无论 A/B 结果都做）**：`_runsc_cmd` 原无超时，`runsc` 真卡死会永久阻塞拆除；现统一走
   `_run_runsc_bounded` 有界等待（`_RUNSC_CMD_TIMEOUT=180s`，宽于实测最坏 ~120s，避免误杀正常慢删；超时记
   `browser.runsc_cmd_timeout` warning + 杀掉 runsc 子进程 + 放弃等待，**不抛错**），孤儿/半启动清理路径
   `_cleanup_partial` 的 `delete --force` 也对齐同一有界 helper。`close()` 保持幂等、best-effort、不抛错，
   对外接口 / 契约零变更。单测见 `tests/test_browser_session.py`（自然退出顺序 / 超时兜底 SIGKILL / 有界放弃 / 幂等）。

   **随 A/B 追踪一并修复的既有拆除缺陷**：driver 崩溃（RPC 通道死）会把会话标 `_alive=False`，而 `close()`
   原以 `_alive` 做幂等守卫 → 提前 return，跳过 netns/veth、并发槽位、runsc 容器与 bundle 目录的回收（宿主侧
   资源泄漏到进程退出；registry `_entries` 容量不受影响但 `_used_slots` 会被吃满）。已把幂等改键到独立的
   `_closed` 标志：崩溃会话照常走全量拆除（只跳过对死通道的 close RPC）。单测
   `test_close_after_driver_crash_still_reclaims_resources` 钉死该行为。
2. **生产网络权限**：每会话 netns+veth 需 `CAP_NET_ADMIN`（本冒烟靠 `--privileged`）。真 gVisor 部署须在
   deploy 侧授权（部署配置，非代码）。
3. **dev fake-IP 依赖**：本机 Docker Desktop 走 Clash fake-IP，公网名解析成 198.18.x.x，靠
   `web_fetch_allow_fake_ip_proxy=True` 让代理放行该段才触达真公网（真私网仍拦）。生产无 fake-IP 时该项无影响。

## 文件清单

- `smoke_product_e2e.py` — 产品模块端到端冒烟编排（异步，直接驱动 registry/tools/live hub/driver input/proxy），
  逐条验收 7 断言、打印指标 JSON + `SMOKE_OK`，含 `_mark`/`runsc` 计时诊断（test-only，不改产品）。
- `README.md` — 本文。
- `out/` — 关键帧/直播样帧产物（gitignored）。
