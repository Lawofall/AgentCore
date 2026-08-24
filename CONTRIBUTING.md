# 参与贡献（Contributing）

感谢关注 AgentCore。[`Lawofall/AgentCore`](https://github.com/Lawofall/AgentCore) 是对外公开的产品仓库（见根 [README](./README.md)）。欢迎在该地址提 Issue 与 Pull Request。

文档与贡献说明以**中文**为准；根 README 仅保留一句英文产品介绍（检索 / AI）。不维护完整英文 docs。问问题去哪 → [SUPPORT.md](./SUPPORT.md)。

## 怎么帮忙

- 通过 [GitHub Issues](https://github.com/Lawofall/AgentCore/issues) 报 Bug、提想法
- 提交行为或文档的 PR；请保持改动聚焦
- 安全漏洞按 [SECURITY.md](./SECURITY.md) 私下报告（优先 GitHub Private vulnerability reporting）——不要开公开 Issue

跨模块、影响面大的改动：请先开 Issue 对齐。

## 建议阅读顺序

按角色选一条即可。设计文档总入口与任务路由全表权威：[`docs/索引.md`](./docs/索引.md)。跨工具 AI 最短入口：[`AGENTS.md`](./AGENTS.md)。根 README 只给最短跑通与常用子集指针；包级命令见各 `apps/*/README`。

1. [`README.md`](./README.md) — 产品定位、最短跑通、仓库地图  
2. [`docs/02-架构/本地开发.md`](./docs/02-架构/本地开发.md) — clone 后跑通  
3. 按方向深入：
   - 后端 / runtime → [`apps/server/README.md`](./apps/server/README.md) → [`运行时总览`](./docs/03-AI核心/运行时总览.md)
   - 桌面 → [`apps/desktop/README.md`](./apps/desktop/README.md) → [`前端地图`](./docs/04-前端/前端地图.md)
   - 手机 → [`apps/mobile/README.md`](./apps/mobile/README.md) → [`前端技术` §五 客户端架构（桌面 + 手机）](./docs/04-前端/前端技术与架构.md)
   - 管理后台 → [`apps/admin/README.md`](./apps/admin/README.md) → [`管理员后台`](./docs/05-平台与运维/管理员后台.md)
4. 术语不确定时查 [`术语表`](./docs/01-产品/术语表.md)

公开设计权威在 `docs/01`–`05`（⏳ = 已确认未落地，以代码与文内短指针为准；详细提案不在公开仓）。`.cursor/rules/` 是 Cursor AI 行为规则（How）。规划草案若在维护者机器上的 `docs/06-规划/`，不会出现在公开 clone。

## 开发环境

完整步骤见 [`docs/02-架构/本地开发.md`](./docs/02-架构/本地开发.md)。

后端最小集：

```bash
docker compose -f deploy/docker-compose.dev.yml up -d
cd apps/server && uv sync
```

前端 / monorepo 包：在**仓库根**执行 `pnpm install`（勿只在子包单独装）。

## Pull Request

1. 改动聚焦；小 PR + 说清要解决的问题。
2. `apps/server` 行为变更请补或更新测试。
3. 不要提交密钥、本地 `.env` / `.env.local`、`data/`，或临时 `tmp_*` / `_tmp_*` / `.tmp_*` / `reviews/` 等草稿。
4. `docs/06-规划/`、`reviews/` 已在 `.gitignore`；勿强行 `git add -f`。规划草案与审查草稿仅维护者本地。
5. 风格与现有代码一致；提交前跑下面的检查。

### 提交前检查

可选（推荐）：安装 [pre-commit](https://pre-commit.com/) hooks，对 staged 的 Python 跑 ruff、对 desktop/mobile 跑 Biome（见仓库根 `.pre-commit-config.yaml`）。`uv tool install pre-commit` 后在仓库根执行 `pre-commit install`。不强制。

验证分三档（默认点名用例，勿默认全量 gate）→ [`AGENTS.md`](./AGENTS.md)。窄化命令与门禁分段 → [`verify-scope.mdc`](./.cursor/rules/verify-scope.mdc)。改了 OpenAPI / SSE / fold：仓库根 `pnpm gen:types`，再 `pnpm conformance`。

## 许可证

提交即表示你同意贡献内容按 [MIT License](./LICENSE) 授权。
