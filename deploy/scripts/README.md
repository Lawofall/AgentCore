# `deploy/scripts/`

生产机、CDN、SSH。凭据只读 `deploy/.env.deploy.local`（不入仓）。

顶层 compose / nginx / systemd → [`deploy/README.md`](../README.md)。  
本机门禁 / 发版编排 → [仓根 `scripts/`](../../scripts/README.md)。  
后端 dogfood 探针 → [`apps/server/scripts/`](../../apps/server/scripts/README.md)。

| 类 | 例子 |
|----|------|
| 后端部署 | `remote-build-deploy.mjs`、`guest-base-id.mjs`、`finish-server.sh`、`deploy-server.sh`、`resume-finish.mjs`（镜像已在机上时续跑 finish） |
| 备份 / 探活 | `backup.sh`、`restore.sh`、`healthcheck.sh`、`check-prod-version.mjs`、`check-remote-build.mjs` |
| 安装包 CDN | `sync-release-cdn.mjs`、`prune-release-cdn.mjs`、`probe-updater-cdn.mjs`、`downloads-remote-install.sh` |
| 管理后台机上 Nginx | `admin-remote-install.sh` |
| Pages 直传 | `wrangler-pages-deploy.mjs`、`load-deploy-env.mjs` |
| CORS / Capacitor | `check-capacitor-cors.mjs`、`add-capacitor-cors.mjs`、`strip-mobile-web-cors.mjs`（清已下线的独立 `m.` origin） |
| 公网 DNS / HTTPS | `public-dns-https.mjs` |
| 产品公告上生产 | `publish-product-notice.mjs`（文案套模板走仓根 `pnpm release:notice`） |
| 平台池只读 | `probe-platform-pool.mjs`（主入口；`-focus` / `-failed` / `-logs` 是专项变体；同名 `.py` 是拷进容器的探针体，勿当重复稿删） |
| 库初始化 | `init-db.sh` |
