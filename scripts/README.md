# 仓根 `scripts/`

本机跑的 monorepo 工具。入口是根 `package.json`（`pnpm gen:types` / `release:gate` / `release:ship` 等）。

| 放这里 | 不放这里 |
|--------|----------|
| 门禁、契约生成、版本 bump、手册语料同步（`sync:manual-corpus`）、开发规则镜像（`sync:rules`） | SSH 改生产、CDN、Compose、机上 backup → [`deploy/scripts/`](../deploy/scripts/README.md) |
| 跨 app 的 Vite / lint 辅助（`vite-csp`、`client-build-info`、token 检查） | 后端真跑探针 → [`apps/server/scripts/`](../apps/server/scripts/README.md) |
| `sync:logs`（只把生产日志拉到本机 `logs/prod-export`） | 各 app 自己的打包脚本 → `apps/*/scripts/` |

`release:ship` / `release:notice` 只编清单和文案；真正切流、打 CDN 仍调用 `deploy/scripts/`。
