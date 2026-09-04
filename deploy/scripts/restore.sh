#!/usr/bin/env bash
#
# AgentCore 数据库恢复（部署与运维.md §7.7「破坏性干净恢复」）。
#
# ⚠️ 危险：DROP SCHEMA public CASCADE 清空当前库后回灌备份。误用 = 数据全失。
#
# 流程（§7.7）：解析备份 → 完整性校验 → 确认门 → 停应用（避免并发写）→
# DROP SCHEMA public CASCADE + CREATE SCHEMA（非空库直接灌会主键冲突）→
# gunzip | psql 回灌 → 不自动起应用（须重新 deploy 对齐版本）。
#
# 用法：
#   restore.sh [<backup.sql.gz>]    # 缺省取 BACKUP_DIR 最新 backup-*.sql.gz
#   FORCE=1 restore.sh ...          # 跳过交互确认（自动化用，慎）
#
# 配置：同 backup.sh（路径见 deploy-paths.sh；BACKUP_DIR / PG_USER / PG_DB …）。

set -euo pipefail

# 活栈 compose/env：deploy-paths.sh（backup.sh / restore.sh / deploy-server.sh 共用）。
_ac_paths="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy-paths.sh"
if [[ ! -f "$_ac_paths" ]]; then
  _ac_paths="${AGENTCORE_HOME:-/opt/agentcore}/repo/deploy/scripts/deploy-paths.sh"
fi
# shellcheck source=deploy-paths.sh
. "$_ac_paths"
unset _ac_paths
BACKUP_DIR="${BACKUP_DIR:-$AGENTCORE_HOME/backups}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-agentcore}"
PG_USER="${PG_USER:-agentcore}"
PG_DB="${PG_DB:-agentcore}"
FORCE="${FORCE:-0}"

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m[warn]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[31m[error]\033[0m %s\n' "$*" >&2; }

COMPOSE_FILES=(
  -f "$DEPLOY_DIR/docker-compose.server.yml"
  -f "$DEPLOY_DIR/docker-compose.app.yml"
)
dc() { docker compose -p "$COMPOSE_PROJECT" "${COMPOSE_FILES[@]}" --env-file "$ENV_FILE" "$@"; }

[[ -f "$ENV_FILE" ]] || { err "env file not found: $ENV_FILE"; exit 1; }

# ── 1. 解析备份文件（参数优先，否则取 BACKUP_DIR 最新 backup-*）──
BACKUP_FILE="${1:-}"
if [[ -z "$BACKUP_FILE" ]]; then
  BACKUP_FILE="$(ls -1 "$BACKUP_DIR"/backup-*.sql.gz 2>/dev/null | sort | tail -n1 || true)"
  [[ -n "$BACKUP_FILE" ]] || { err "BACKUP_DIR 无 backup-*.sql.gz：$BACKUP_DIR（请显式传文件路径）"; exit 1; }
fi
[[ -f "$BACKUP_FILE" ]] || { err "备份文件不存在：$BACKUP_FILE"; exit 1; }
gzip -t "$BACKUP_FILE" 2>/dev/null || { err "备份文件损坏（gzip -t 失败）：$BACKUP_FILE"; exit 1; }

log "AgentCore DB restore"
warn "目标库：$PG_DB（用户 $PG_USER，compose 项目 $COMPOSE_PROJECT）"
warn "回灌源：$BACKUP_FILE"
warn "将 DROP SCHEMA public CASCADE 清空当前库再回灌——不可逆。"

# ── 2. 确认门（FORCE=1 跳过；非 TTY 无输入则视为取消）──
if [[ "$FORCE" != "1" ]]; then
  printf '请输入数据库名 \033[1m%s\033[0m 确认恢复（其他任意输入取消）: ' "$PG_DB"
  read -r reply || reply=""
  [[ "$reply" == "$PG_DB" ]] || { err "确认不匹配，已取消（未改动任何数据）。"; exit 1; }
fi

# ── 3. 停应用（避免并发写；app 若在宿主机运行则无此 service，warn 跳过）──
# --timeout 40 与 compose stop_grace_period 对齐，避免默认 10s 砍断排空/抢救/收尾。
log "停应用容器（避免并发写）"
dc stop --timeout 40 api sandboxd 2>/dev/null || warn "未停到 api/sandboxd（app 可能在宿主机运行）——请自行确保无实例在写库。"

# ── 4. 破坏性干净恢复：DROP SCHEMA + 回灌 ──
log "DROP SCHEMA public CASCADE + CREATE SCHEMA"
dc exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

log "回灌备份（gunzip | psql）"
if ! gunzip -c "$BACKUP_FILE" | dc exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 >/dev/null; then
  err "回灌失败——库可能处于半截状态，请检查后重灌或换备份。"
  exit 1
fi

log "恢复完成 ✅  源：$(basename "$BACKUP_FILE")"
warn "按 §7.7 恢复后不自动起应用——请重新 deploy 对齐版本（deploy-server.sh <sha>）后再起 api。"
