# apps/server/scripts

一眼区分：

| 位置 | 用途 |
|------|------|
| **本目录（根）** | 常驻：契约 dump / 生成、日志、门禁、运维、dev 启动 |
| [`archive/`](./archive/) | 一次性探针 / bench / measure / `_tmp_*`（仅本地实验） |
| `poc_browser_gvisor/` · `smoke_browser_gvisor/` | 浏览器沙箱 PoC / 冒烟（自带 README） |

## 常驻（勿随意挪走）

- **契约 / 生成**：`dump_*.py`、`gen_*.py`、`validate_sse_contract.py`、`mlr_golden_rings_check.py`
- **日志 / 注册表**：`log_timeline.py`、`log_stats.py`、`log_patrol.py`、`sync_log_event_registry.py`（`--check` 只读对账，挂 `release:gate`）
- **门禁 / 校验**：`check_schema_gate.py`、`check_workspace_ignore_parity.py`、`check_event_consumer_orphans.py`、`check_event_field_consumers.py`、`verify_gvisor_sandbox.py`
- **运维 / 开发**：`create_admin.py`、`seed_dev_user.py`、`set_quota.py`、`set_dev_llm_key.py`、`export_conversations.py`、`cleanup_test_conversations.py`、`backfill_memory.py`、`migrate_memory_pipeline.py`、`backfill_auto_desk_scratch.py`、`sync_community_prices.py`、`fetch_*.py`、`start-dev-server.ps1`
- **演示带**：`demo_tape_*.py`

探针与基准 → [`archive/`](./archive/)。

## 日志三件套怎么选

| 我要… | 用哪个 |
|------|-------|
| 一条 trace / 会话的时间线、排查包 | `log_timeline.py` |
| 一个窗的事件分布、成本、协作质量 | `log_stats.py` |
| 巡检①：全量 CID 清单 + 失败榜（带 trace/cid 反查）+ 跨窗快照 diff | `log_patrol.py` |

`log_patrol.py` 纯只读、不进 `release:gate`；失败家族表是入仓常量（`agentcore/observability/query/failure_families.py`），
新增家族安全、改名/改口径会在跨窗 diff 里响。窗口快照落 `logs/` 下（gitignore，不入仓）。
