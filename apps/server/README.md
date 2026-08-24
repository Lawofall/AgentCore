# AgentCore 后端（apps/server）

FastAPI 后端：HTTP / SSE API、多 Agent **runtime 执行引擎**、LLM 网关、工具与记忆、认证与计费等平台能力。Python 包入口为 `agentcore`。

## 何时读这里

- 改 API、编排、工具、DB、认证、部署后端 → 从本目录动手
- 只改桌面 / 手机渲染层 → 见 [`apps/desktop`](../desktop/README.md) / [`apps/mobile`](../mobile/README.md)

## 文档入口

| 主题 | 文档 |
|------|------|
| clone 后跑通（权威步骤） | [`docs/02-架构/本地开发.md`](../../docs/02-架构/本地开发.md) |
| 分层与模块边界 | [`后端架构`](../../docs/02-架构/后端架构.md)、[`项目结构`](../../docs/02-架构/项目结构.md) |
| REST / SSE / Store 契约 | [`核心接口定义`](../../docs/02-架构/核心接口定义.md) |
| DAG / CEO / Run / SSE | [`运行时总览`](../../docs/03-AI核心/运行时总览.md) |
| 任务路由总表 | [`docs/索引.md`](../../docs/索引.md) |

## 目录速览

```text
agentcore/
  api/          # 路由薄层（不在 handler 里驱动 runtime / 建 LLM）
  runtime/      # 执行核心：pipeline、journal、events、调度…
  llm/          # 网关本体（纯）+ 凭据服务（可碰 DB）
  tools/        # 内置工具与编排原语（delegate / debate / …）
  conversation/ # 对话与流式入口等业务
  memory/       # 记忆与知识
  db/           # ORM / 仓储地基
  config/       # 按域拆分的 settings
scripts/        # 开发与运维脚本（启动、seed、dump OpenAPI…）
tests/          # pytest（含架构边界测试）
```

硬约束（调用方向、路由不干执行层的活）→ [`项目结构` §二](../../docs/02-架构/项目结构.md)。实现细节以代码与专题文档为准。

## 本地启动

完整步骤 → [`本地开发` §1–§2](../../docs/02-架构/本地开发.md)。Windows 清树启动：`powershell -File apps/server/scripts/start-dev-server.ps1`。长时间真跑前 `.env` 设 `AGENTCORE_RELOAD=false`。

## 常用命令

在 `apps/server` 或经根脚本：

| 命令 | 作用 |
|------|------|
| `uv run python -m agentcore` | 启动 API |
| `uv run alembic upgrade head` | 应用迁移 |
| `uv run pytest --ignore=tests/integration` | 单元测试 |
| `uv run pytest --cov=agentcore --cov-report=term-missing --ignore=tests/integration` | 覆盖率基线报告（无 fail-under；棘轮后续再加） |
| `uv run python scripts/dump_openapi.py` | 导出 OpenAPI（前端 `gen:types` 上游） |
| 仓库根 `pnpm test:server:unit` | 同上单元测试的快捷入口 |
| 仓库根 `pnpm test:server:cov` | 同上覆盖率报告的快捷入口 |
| 仓库根 `pnpm gen:types` | 同步跨端 REST / 事件类型 |
| 仓库根 `pnpm release:gate --only backend` | 仅跑门禁后端段 |

改 OpenAPI / EventType / InteractionKind 后：根目录 `pnpm gen:types`，若动了 SSE / fold 再 `pnpm conformance`。

## 测试与门禁

- 架构 import 边界：`tests/test_arch_boundaries.py`
- 发布前：仓库根 `pnpm release:gate`（与 CI 同构）
- 贡献约定：[`CONTRIBUTING.md`](../../CONTRIBUTING.md)
