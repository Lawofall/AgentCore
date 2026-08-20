# AGENTS.md

AgentCore：面向大众的 **Multi-Agent AI 工作台**——真正的 Agent 团队协作（非「单 Agent + 子任务派发」）。Monorepo：`apps/server` · `apps/desktop` · `apps/mobile` · `apps/admin` + `packages/`。

跨工具 AI / 贡献者入口（本文件入仓、保持短）。详细产品与架构说明在 `docs/`，不在此复述。

文档以**中文**为准；根 README 仅一句英文产品介绍（检索用）。English docs are not maintained.

## 先读哪里

| 我要… | 去哪 |
|------|------|
| 任务路由 / 设计文档总入口 | [`docs/索引.md`](docs/索引.md) |
| 产品一句话、最短跑通、仓库地图 | [`README.md`](README.md) |
| 贡献 / PR / 勿提交什么 | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| 问问题 / Issue 去哪 | [`SUPPORT.md`](SUPPORT.md) |
| clone 后跑通前后端 | [`docs/02-架构/本地开发.md`](docs/02-架构/本地开发.md) |

## Docs 与 Rules

| | 公开仓 | 说明 |
|---|---|---|
| `docs/01`–`05` | ✅ | What/Why：现状 + 已确认蓝图（⏳ = 已确认未落地；以代码与文内短指针为准） |
| `docs/06-规划/` | ❌ 不入公开树 | 详细提案仅维护者本地；勿依赖公开 clone 里存在该目录 |
| `.cursor/rules/` | ✅ | How（Cursor AI 操作细则）；与 `docs/` 分工见 `doc-governance.mdc` |

## 开发 / 测试（最短）

在仓库根 `pnpm install`；后端见本地开发（`uv sync`、Compose、Alembic）。

**验证分三档，默认走最低档**——全量很贵：一次干净 `release:gate` 约 15–25 min，其中桌面截图独占 ~4 min（4 worker），后端全量 pytest ~5 min。

1. **改完即验（默认）**——只跑点名的测试文件 / 用例：`uv run pytest tests/x.py::test_y`、`pnpm --filter agentcore-desktop exec vitest run <文件>`
2. **交付收尾**——只跑改动**所涉包**：`pnpm test:server:unit`、`pnpm --filter <包> test -- --run`、`pnpm --filter <包> typecheck`；不碰没改的包
3. **发布门禁**——仅发布决策时 `pnpm release:gate`；非发布语境要跑就 `--only <段>` 或 `release:gate:lite`

改 OpenAPI / SSE / fold 才需 `pnpm gen:types` + `pnpm conformance`（没动契约就别跑，`gen:types` 是全仓重生成）。

窄化命令配方、门禁分段与反空转纪律 → [`verify-scope.mdc`](.cursor/rules/verify-scope.mdc)。

包级命令见各 `apps/*/README`。安全漏洞 → [`SECURITY.md`](SECURITY.md)（勿开公开 Issue）。
