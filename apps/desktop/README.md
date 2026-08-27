# AgentCore 桌面端（apps/desktop）

主产品客户端：**Electron + React**。负责对话与协作 UI、本地工作区 Sidecar、预览浏览器、终端等桌面能力；AI 执行在 `apps/server`，本仓库渲染层消费 REST / SSE。

## 何时读这里

- 改聊天 / 协作图 / 工作区 UI / Electron main·preload → 从本目录动手
- 改执行语义、API、工具实现 → [`apps/server`](../server/README.md)
- 改手机端（独立应用，非本包裁剪）→ [`apps/mobile`](../mobile/README.md)

## 文档入口

| 主题 | 文档 |
|------|------|
| 前端权威归属与读序 | [`前端地图`](../../docs/04-前端/前端地图.md) |
| Store / IPC / SSE 消费 | [`前端技术与架构`](../../docs/04-前端/前端技术与架构.md) |
| 布局、协作图、检查点 UX | [`前端 UX 设计`](../../docs/04-前端/前端UX设计.md) |
| 云+本地工作区 | [`双模式工作区`](../../docs/02-架构/双模式工作区.md) |
| clone 后跑通 | [`本地开发`](../../docs/02-架构/本地开发.md) |
| 离线回放 AI 态 / 截图自检 | `.cursor/rules/frontend-preview.mdc`（AI How，贡献者可不读）；入口见本地开发 §3 |

## 目录速览

```text
src/
  main/       # Electron 主进程：窗口、Sidecar、本地 FS、预览…
  preload/    # 桥接 API
  renderer/   # React UI：chat / graph / workspace / settings…
  shared/     # main ↔ renderer 契约
scripts/      # shoot 截图、发版、sidecar 打包等
e2e/          # Playwright
```

跨端共享的是 `packages/` 里的**契约与 token**，不是业务 stores / 组件（各端自建）。

## 本地启动

依赖在**仓库根**安装；后端需已在 `:8000`（或按 `.env.local` 指向）。

```bash
# 仓库根
pnpm install
cp apps/desktop/.env.example apps/desktop/.env.local
pnpm -C apps/desktop dev
```

可选：

| 命令 | 作用 |
|------|------|
| `pnpm -C apps/desktop dev:web` | 纯浏览器跑渲染层（无 Electron，便于 UI 迭代） |
| `pnpm -C apps/desktop shoot` | 无头截图自检 |
| `pnpm -C apps/desktop shoot:graph-probe` | 协作图视口探针 |
| `pnpm -C apps/desktop shoot:graph-perf` | 协作图离线掉帧探针（`#/preview`） |
| `pnpm -C apps/desktop shoot:graph-perf-live` | 协作图**实时**掉帧探针（CDP 连正在跑的 dev 应用） |

实时探针需先带调试端口启动应用（测真实 ELK / 大图；离线探针造不出）：

```bash
pnpm -C apps/desktop exec electron-vite dev --remoteDebuggingPort=9222
# 另开终端
pnpm -C apps/desktop shoot:graph-perf-live
# 可选：pnpm -C apps/desktop shoot:graph-perf-live -- 180
# 可选：pnpm -C apps/desktop shoot:graph-perf-live -- --cid <conversationId>
```

报告写入 `shoot-out-graph-perf/live-*.json`（含实测刷新率；遮挡导致的 ~1Hz rAF 限流秒会剔除）。

开发账号可与后端 `seed_dev_user.py` + `.env.local` 自动登录配合（见本地开发）。

## 常用命令

| 命令 | 作用 |
|------|------|
| `pnpm -C apps/desktop dev` | Electron 开发 |
| `pnpm -C apps/desktop test` | Vitest 单元测试 |
| `pnpm -C apps/desktop typecheck` | `tsc --noEmit` |
| `pnpm -C apps/desktop lint` | Biome + UI token / localStorage 门禁 |
| `pnpm -C apps/desktop conformance` | 本端协议 conformance |
| 仓库根 `pnpm conformance` | 全仓 fold / 投影对账 |
| 仓库根 `pnpm gen:types` | 同步 OpenAPI / 事件类型 |
| `pnpm -C apps/desktop build:win` 等 | 安装包构建（发版细节见部署文档） |

改 SSE 载荷、fold、InteractionKind 后：根目录 `pnpm gen:types` **与** `pnpm conformance`。

## 贡献

[`CONTRIBUTING.md`](../../CONTRIBUTING.md) · 门禁 `pnpm release:gate`（可 `--only desktop`）。
