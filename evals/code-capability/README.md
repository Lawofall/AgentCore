# AI 代码能力全面测试

> **状态**：Phase 1 准备包已冻结；**Phase 2 真跑（2026-07-23）已汇总**。  
> **读本页即可**：本轮 verdict · 定案口径 · S1–S7 结论 · Gap 状态。  
> 规划提案全文不在公开仓；本页为公开验收真相源。

## 本轮 Verdict（2026-07-23）

**D·引擎路径主验收通过**（S1 / S2 / S4 / S5·R2 / S6 / **S7** 均 Pass）。  
**S3 Resume UI 仍待人手 / CDP**（无 S4 式 RPC 等价；勿编造 Pass）。  
**S7 P2 todo-api**：**Pass**（sidecar 真跑 + 外部 GOLDEN：`pytest` 7 passed；stdlib `http.server`；`GET`/`POST /todos`）。  
**B 环境债冒烟**：**Pass**（环境债目标点）：配额解除后 CEO 调 `code_execute` → `not_assembled` → 改 `delegate`；盘上 `fix-me-kit-smoke-b` `pytest` 3 passed（早期 4×`LLM_RATE_LIMIT` 为配额债，已过时）。  
人手步骤见 [`runbooks/s3-resume-ui.md`](runbooks/s3-resume-ui.md)。  
本轮探测到的产品接缝均已收口：S5 R1（delegate 契约拒绝误烧熔断）**已修**；云 `files/diff` 500 **已修**；环境债报错/无 bash **已修**（单测 + B 真跑）。仍开放：**S3 待人手（U）**。

| ID | 场景 | D·引擎 | 备注 |
|----|------|--------|------|
| S1 | P1 从零搭 hello-cli | **Pass** | GOLDEN 全绿；sidecar RPC |
| S2 | P3 最小修 Bug | **Pass** | pytest 3→0 fail；sidecar RPC |
| S3 | 中断 / Resume | **待人手 / CDP** | 「刷新仍见卡」无 RPC 等价；runbook 已备 |
| S4 | Checkpoint + turnFilesDiff | **Pass** | diff↔盘一致 + restore 绿；RPC 等价 |
| S5 | Delegate 多 Agent | **Pass**（R2） | R1 曾 Fail（已修，见下）；R2 有 `run_plan` + GOLDEN |
| S6 | Server API 对照 | **Pass** | 云写码闭环；探测时 `files/diff` 500 **已修** |
| S7 | P2 从零搭 todo-api | **Pass** | 独占 `todo-api-s7/`；stdlib HTTP；外部 `pytest` 7 passed |
| B | 环境债 sidecar 冒烟 | **Pass**（目标点） | CEO `code_execute`→`not_assembled`→`delegate`；盘上 pytest 3 passed；`fix-me-kit-smoke-b/` |

## 本轮定案口径（勿再请示）

1. **sidecar JSON-RPC（与 Desktop 主进程同构）算「D·引擎路径」Pass**。人手点 Electron UI 单列为可选 **U 层**，**不挡**本轮 D 验收。
2. **S3**：「刷新/重进仍见卡」属 **U**；`listPaused`/`resume` 只证帧落盘+续跑，**不**完成本场景（无 `turnFilesDiff` 式按钮数据 RPC）。解禁 = 人手按 runbook 跑通或产品决策扩 CDP。矩阵标 **待人手 / CDP**，禁止探针冒充 Pass。
3. 其余场景证据链到既有 probes 即可（见下表路径）；不要求本页重贴全量事件。

## S1–S7 证据与结论

| ID | Verdict | 工作区 | conversation_id / trace_id | 探针 |
|----|---------|--------|----------------------------|------|
| S1 | Pass | `workspaces/hello-cli-s1/` | `3f1987ed-…` / `a76f753601e34fbc93e8bd1f2d9dec3d` | [`logs/probes/code_cap_s1_20260723.json`](../../logs/probes/code_cap_s1_20260723.json) |
| S2 | Pass | `workspaces/fix-me-kit-s2/` | `1358e023-…` / `511d6f4db643455990de6644b6788bed` | [`logs/probes/probe_sidecar_1784797063.json`](../../logs/probes/probe_sidecar_1784797063.json) |
| S3 | **待人手 / CDP** | `workspaces/fix-me-kit-s3/`（试件已备） | —（未发 turn） | 人手：[`runbooks/s3-resume-ui.md`](runbooks/s3-resume-ui.md)；回填 id 后方可 Pass（U） |
| S4 | Pass | `workspaces/fix-me-kit-s4/` | `575eb0b2-…` / `0ecb7abf998a4385bcda487e9fcf3c4b` | [`logs/probes/s4_checkpoint_diff_20260723_165108.json`](../../logs/probes/s4_checkpoint_diff_20260723_165108.json) |
| S5 R1 | Fail（探测）→ **已修** | `workspaces/hello-cli-s5/` | `32838baf-…` / `3d25e573b1664ac2a40e9ec2bddf968f` | [`logs/probes/s5_delegate_20260723_165900.json`](../../logs/probes/s5_delegate_20260723_165900.json) |
| S5 R2 | Pass | 同上（重跑） | `c7cc15f0-…` / `91df59383e1a41c193a893f7a05936de` | [`logs/probes/s5_delegate_r2_20260723_170127.json`](../../logs/probes/s5_delegate_r2_20260723_170127.json) |
| S6 | Pass | 云工作区（P3 播种） | `ebce442a-…` / `99737dc9ade84de98e96116fddf1efd3` | [`logs/probes/probe_20260723-165223.json`](../../logs/probes/probe_20260723-165223.json) |
| S7 | **Pass** | `workspaces/todo-api-s7/` | `a1c0d738-…` / `7fc406c549b94c5fbf42d308a0f3396b` | [`logs/probes/probe_sidecar_1784808590.json`](../../logs/probes/probe_sidecar_1784808590.json)；外部验收 `python -m pytest -q` → **7 passed**；栈 stdlib `http.server`；入口 `python -m todo_api`；`GET`/`POST /todos`（进程内/替用端口探测 OK；本机默认 `:8765` 曾 `WinError 10013`） |

### 关键 Gap / 收口

| Gap | 来源 | 性质 | 状态 |
|-----|------|------|------|
| `delegate` 契约拒绝（playbook⊕tasks / 误传已删字段等）未标 `contract_failure` → 连拒烧穿熔断；熔断后 CEO 无写盘（**设计如此**） | S5 R1 | 校验归因 + 提示 | **已修**：契约拒绝标 `contract_failure` + 更清晰报错 + schema/CEO 提示防踩坑；**已定案不给 CEO 加 `file_write`**。R2 组队 + GOLDEN Pass |
| `GET …/messages/{mid}/files/diff` → 500（`conv` 为 None → `folder_id`） | S6 | 云 diff 接缝 Bug | **已修**：`turn_files_diff.py` 误用 `_require_owned_conversation`（无返回）→ 改为 `_get_owned_conversation`；`test_turn_files_diff` 10 passed |
| S3 刷新/重进仍见挂起卡 | S3 | U 层 | **待人手 / CDP**（无 RPC 等价；见 runbook） |
| U 层产物卡按钮未点 | S4 等 | 可选 U | 不挡 D；RPC `turnFilesDiff` / `restoreTurnBaseline` 已通 |
| 环境摩擦（`code_execute` WSL、`test_run` framework） | S1/S2/S5/S6 | 环境噪音 | **部分已清**：① CEO/未装配面误调执行类工具 → 可操作报错 + `policy_failure`（不烧熔断）；② 本机无 bash 时 `code_execute` 启动前失败并提示改用 python/js。`test_run` 在近空仓 `framework=unknown` 仍属试件早期噪音；S1 探针 900s 超时视为空转后果，随①②减轻。**不**给 CEO 加执行/写盘工具（定案不变） |

## 交付物（Phase 1 + 本轮）

| 路径 | 内容 |
|------|------|
| [`matrix.md`](matrix.md) | 场景 × 能力 × 验收；含 **D / U** 口径与 S3 待人手 |
| [`turn-recipe.md`](turn-recipe.md) | Desktop sidecar / Server API 调用配方（已同步 D=RPC 同构） |
| [`parallel-briefs.md`](parallel-briefs.md) | S1–S7 brief（S3 待人手 / CDP；S7 已 Pass） |
| [`runbooks/s3-resume-ui.md`](runbooks/s3-resume-ui.md) | **S3 U 层人手步骤**（绑盘 · 见卡 · 刷新 · 回填 id） |
| [`workspaces/hello-cli/`](workspaces/hello-cli/) | **P1** 主试件模板 |
| [`workspaces/todo-api/`](workspaces/todo-api/) | **P2** 近空模板（S7 真跑副本 `todo-api-s7/`） |
| [`workspaces/fix-me-kit/`](workspaces/fix-me-kit/) | **P3** 并行专用模板 |
| `workspaces/*-s{1..5,7}/` | 本轮独占副本（真跑落盘；含 `todo-api-s7/`） |
| `workspaces/fix-me-kit-smoke-b/` | B 环境债冒烟副本（CEO 诱使误调执行工具） |

**P2 `todo-api`**：S7 加码真跑 + 外部 GOLDEN **Pass**。

## 非目标（写死）

计费/账本精度、多租户、Mobile、Admin、无边界大项目、压测。

## R 真仓（内部评测 · R0 / R0b / R1a / R1b / R2 / R3 / R4）

与上表 **合成 S1–S7 / workspaces** 分列：合成 = 烟感与产品接缝；本栏 = **pinned 开源仓快照上的能力雷达**（只做内部评测，无 GitHub 导入产品面）。

| 项 | 现状 |
|----|------|
| Phase | **R0 ✅** · **R0b vendor 满编 ✅** · **R1a Find+Fix 首波 ✅** · **R1b Find+Fix 满编 ✅** · **R2 Extend ✅** · **R3 Collab ✅** · **R4 回归 ✅** |
| 硬 Check | `agentcore.evals`：`TestExitCode`（含可选 `pythonpath`）· `TestsUnchanged`（Extend 用 `allow_extra` 白名单 GOLDEN）· Find 用 `ContentMatches` |
| R0 Fix 烟感 | [`suites/r0/r0_fix_chunked.json`](suites/r0/r0_fix_chunked.json) + [`seeds/break_chunked.json`](suites/r0/seeds/break_chunked.json)（仅 V07） |
| R1a 任务卡 | [`suites/r1a/`](suites/r1a/)（V01·V02·V07·V08 · 16 卡 Find/Fix）· [`manifest.json`](suites/r1a/manifest.json) |
| R1b 任务卡 | [`suites/r1b/`](suites/r1b/)（V03·V04·V05·V06·V09·V10 · 12 卡 Find/Fix）· [`manifest.json`](suites/r1b/manifest.json) |
| R2 Extend | [`suites/r2/`](suites/r2/)（V01·V04·V09·V10 · 8 卡；Py+TS）· [`manifest.json`](suites/r2/manifest.json)；只追加 GOLDEN 测 + `reference_patch` |
| R3 Collab | [`suites/r3/`](suites/r3/)（V01·V07 · 4 卡；复用 R1a Fix seed；题面强制 `delegate`）· [`manifest.json`](suites/r3/manifest.json)；硬=测绿；软=`collab_diagnostics`（`run_plan` / `worker_files`，不进 hard_accept） |
| 无 LLM 对照 | [`r0_control.py`](r0_control.py) · [`r1_control.py`](r1_control.py) `--suite all --mode matrix` · [`r2_control.py`](r2_control.py) `--mode matrix` · [`r3_control.py`](r3_control.py) `--mode matrix` |
| 基线报告 | Find/Fix：[`reports/r1_baseline_latest.json`](reports/r1_baseline_latest.json)；**Extend**：[`reports/r2_baseline_latest.json`](reports/r2_baseline_latest.json)；**Collab**：[`reports/r3_baseline_latest.json`](reports/r3_baseline_latest.json) |
| LLM 烟感（D·sidecar） | 脚本 [`r_llm_smoke.py`](r_llm_smoke.py)（复用 [`probe_sidecar_turn.py`](probe_sidecar_turn.py)）；报告 [`reports/llm_smoke_latest.json`](reports/llm_smoke_latest.json)；**首波结果见下「LLM 烟感」节**（不进 PR / nightly 强制） |
| R4 冻结基线 | [`reports/baselines/`](reports/baselines/)（`r1.json`·`r2.json`·`r3.json` + [`manifest.json`](reports/baselines/manifest.json)）；棘轮脚本 [`r4_regress.py`](r4_regress.py) |
| Vendor 复现 | [`vendor/README.md`](vendor/README.md) · `_fetch_r0b.py`（维护者本地；禁 CI 现拉 main） |
| 门禁 | **不进** PR 硬门禁；R4 为本地/可选 nightly 挂载点（默认不烧 LLM）；勿与 S1–S7 Pass 口径混谈 |

### R0b 满编 vendor 状态表

去 `.git` / 无 `node_modules` 的源码树 + 仓内 `SOURCE.json`。总树约 **10.3 MiB**（十仓合计）。

| ID | 仓 | vendor 路径 | pin | 许可证 | 闸结果 | 备注 |
|----|----|-------------|-----|--------|--------|------|
| V01 | click | [`vendor/click@b2e30a175449/`](vendor/click@b2e30a175449/) | `8.4.2` / `b2e30a175449` | BSD-3-Clause | LOC≈9.1k ✅ | CLI · **R1a** · **R2** · **R3** |
| V02 | starlette | [`vendor/starlette@8ebffd067857/`](vendor/starlette@8ebffd067857/) | `1.3.1` / `8ebffd067857` | BSD-3-Clause | LOC≈5.3k ✅ | ASGI · **R1a**（测需 `httpx2`） |
| V03 | httpx | [`vendor/httpx@26d48e0634e6/`](vendor/httpx@26d48e0634e6/) | `0.28.1` / `26d48e0634e6` | BSD-3-Clause | LOC≈6.9k ✅ | HTTP 客户端 · **R1b**（硬闸用 `python -c`，避 trustme/trio 全量 pytest 依赖） |
| V04 | flask | [`vendor/flask@22d924701a6a/`](vendor/flask@22d924701a6a/) | `3.1.3` / `22d924701a6a` | BSD-3-Clause | LOC≈6.7k ✅ | WSGI · **R1b** · **R2** |
| V05 | attrs | [`vendor/attrs@7bfc49e9b22d/`](vendor/attrs@7bfc49e9b22d/) | `26.1.0` / `7bfc49e9b22d` | MIT | LOC≈4.8k ✅ | 数据模型 · **R1b**（硬闸用 `python -c`，避 hypothesis conftest） |
| V06 | pyyaml | [`vendor/pyyaml@49790e73684b/`](vendor/pyyaml@49790e73684b/) | `6.0.3` / `49790e73684b` | MIT | LOC≈4.5k ✅ | **R1b**；`PYTHONPATH=lib` 纯 Python，勿强制本机编译 libyaml |
| V07 | more-itertools | [`vendor/more-itertools@64be96ceb2a6/`](vendor/more-itertools@64be96ceb2a6/) | `v11.1.0` / `64be96ceb2a6` | MIT | 应用≈7.1k ✅ | R0 烟感 + **R1a** · **R3** |
| V08 | uuid | [`vendor/uuid@70177807e922/`](vendor/uuid@70177807e922/) | `v14.0.1` / `70177807e922` | MIT | LOC≈2.2k ✅ | TS · **R1a**（Windows：`npm ci --ignore-scripts` + `tsc` → `dist-node`，勿依赖 bash `build.sh`） |
| V09 | commander | [`vendor/commander@ba6d13ddb424/`](vendor/commander@ba6d13ddb424/) | `v15.0.0` / `ba6d13ddb424` | MIT | LOC≈3.6k ✅ | Node CLI · **R1b** · **R2**（`node --test` 直跑 JS） |
| V10 | zod | [`vendor/zod@e30870369d5b/`](vendor/zod@e30870369d5b/) | `v3.24.2` / `e30870369d5b` | MIT | LOC≈12.7k ✅ | **R1b** · **R2**；pin v3（v4 超 LOC）；ts-jest 直跑 src |

```text
# R0
python evals/code-capability/r0_control.py --lint-only
python evals/code-capability/r0_control.py --mode fixed
python evals/code-capability/r0_control.py --mode broken

# R1（R1a+R1b 全卡 fixed/broken + 写 r1/r1b 报告；硬验收）
python evals/code-capability/r1_control.py --lint-only
python evals/code-capability/r1_control.py --suite all --mode matrix

# 分波
python evals/code-capability/r1_control.py --suite r1a --mode matrix
python evals/code-capability/r1_control.py --suite r1b --mode matrix
python evals/code-capability/r1a_control.py --mode matrix   # ≡ --suite r1a

# R2 Extend（缺实现 broken / 参照实现 fixed + 写 r2 报告）
python evals/code-capability/r2_control.py --lint-only
python evals/code-capability/r2_control.py --mode matrix

# R3 Collab（Fix 同口径硬对照 + collab_diagnostics 软字段；写 r3 报告）
python evals/code-capability/r3_control.py --lint-only
python evals/code-capability/r3_control.py --mode matrix

# R4 回归（冻结基线 · 回退 >10pp → Fail；默认不烧 LLM）
python evals/code-capability/r4_regress.py --compare-latest          # latest=冻结 → 应绿
python evals/code-capability/r4_regress.py --self-test-regression    # 合成回退 11pp → Fail 演示
python evals/code-capability/r4_regress.py --lint-only               # 全相位 seed_lint（nightly 轻挂）
python evals/code-capability/r4_regress.py --run --phases r0,r3      # 可选：跑矩阵后再比（可慢）
# bump（须一句话理由；见 reports/baselines/README.md）
python evals/code-capability/r4_regress.py --update-baseline --phase r1 \
  --from reports/r1_baseline_latest.json --reason "一句话理由"
```

### R4 bump 纪律

- 冻结副本在 [`reports/baselines/`](reports/baselines/)；对比口径 = `summary.pass / summary.matrix_cells`。
- **相对基线回退 >10pp → 非零退出**；持平/更好 → 绿。
- 允许 `--update-baseline` 仅当：有意改题面/pin/硬 Check、修 harness 假红假绿、或人确认换观察线——**必须** `--reason`；禁止为变绿静默压基线。
- **Nightly 挂载点**（可选、默认可跳过、不进 PR）：见 [`.github/workflows/evals-nightly.yml`](../../.github/workflows/evals-nightly.yml) 文末注释；本地优先 `--lint-only` 或 `--compare-latest`，全矩阵 `--run` 维护者手工。

### LLM 烟感（D·sidecar · 真跑）

与无 LLM 对照矩阵分列：本栏 = 真 Agent 回合后硬 Check。首波优先 Fix 卡（须含 V07 chunked）；勿与大批量 evals 抢限流。

```text
# 从 apps/server；默认 4 张 Fix（V07 chunked · V01 bool · V05 has · V01 int）
# 默认 --timeout 900；Fix 卡默认加短 prompt_prefix 控空转（--no-prefix 关掉）
uv run python ../../evals/code-capability/r_llm_smoke.py
uv run python ../../evals/code-capability/r_llm_smoke.py --cards v07_fix_chunked,v01_fix_bool,v05_fix_has,v04_fix_flash,v06_fix_dump --timeout 900
uv run python ../../evals/code-capability/r_llm_smoke.py --no-prefix --max-resumes 0
```

| 项 | 说明 |
|----|------|
| 状态 | **甲乙后难仓复测（2026-07-29 · 仅 V05/V06）** → 快照 [`reports/llm_smoke_ab_retest_20260729.json`](reports/llm_smoke_ab_retest_20260729.json)：**2/2 pass** · 经典 hang=0 · 墙钟 timeout=0（V05 近墙钟 898s 仍 `end_turn`）· 硬 Check 全绿。相对 e-idle（测红）：**双绿修通**。历史：e-idle [`reports/llm_smoke_e_idle_20260728.json`](reports/llm_smoke_e_idle_20260728.json) 0/3；W5 [`reports/llm_smoke_baseline_w5_20260728.json`](reports/llm_smoke_baseline_w5_20260728.json) 3/5；优化前 [`reports/llm_smoke_baseline_20260728.json`](reports/llm_smoke_baseline_20260728.json) 1/5。写码完整化本段 **已收口**（大烧冻结） |
| 本轮卡（甲乙后） | V05 `v05_fix_has`（**pass** · end_turn@898s · tools=71 · 硬测双绿 · `str_replace` 上盘）· V06 `v06_fix_dump`（**pass** · end_turn@450s · tools=57 · 硬测双绿 · `str_replace`/`file_write` 上盘）。e-idle 三卡结果见历史快照 |
| 硬测 | turn 有 `finish_reason` 后跑 `TestExitCode` + `TestsUnchanged`；墙钟超时则 `checks_pass=null` |
| 已知限制 | auth / mint inference / sidecar `initialize` OK。**经典接缝 hang** = `turn_started`+timeout+几乎无 tool（仅 `message_start`/`run_*`）→ `fail_class=接缝`。**大量 tool 后墙钟 timeout**（空转烧预算，如反复 `code_execute`/`terminal`/`delegate`）→ `fail_class=模型弱`（notes 标 `wall_clock`），**勿再记为接缝死锁**。**产品路径 = CEO→delegate**（卡声明 `path=team`/`toolset=ceo`）；EvalCase 字段**不**透传 `startTurn`，烟感**不**平行造 worker 直装。长跑建议把 stdout **重定向到文件**（Cursor terminal 背压可在 timeout 后卡死 print） |
| 流程 | copytree vendor → seed → sidecar `startTurn`（prompt=可选 prefix + 卡内 `user_message`）→ 硬 Check |
| 副本 | `workspaces/llm-smoke/<task_id>/`（禁直绑 `vendor/`） |
| fail_class | 环境 / 模型弱 / 接缝 / 题面 / 需决策·交互（`ask_user` 默认不 resume） |
| 门禁 | **不进** PR；**不**改 nightly 强制 job；**不** `--update-baseline` R1–R3 冻结棘轮 |
| PYTHONPATH | 产品 `code_execute`（local/server）与硬闸 `TestExitCode` **同源**：相对 cwd 解析；产品自动注入 `.`+现存 `src`/`lib`；卡可声明 `checks[].args.pythonpath`。夹具：`tests/test_pythonpath_code_execute.py` |
| 开跑纪律 | **W0–W5 / E1–E3 / 甲·乙 / 甲乙后难仓复测已完成 · 本段收口**；**大烧冻结**——再烧须书面新归因（对照 V07 稳性 / 502 专项 / 新接缝 / 预算效率），勿无目标盲跑 |

评测只对 **copytree 隔离副本** 写盘；`seed_patch` / `reference_patch` / GOLDEN 只打副本；禁止直绑 `vendor/`。R 真仓证据性质：真仓快照上的合成任务卡 ≠ 真实用户数据。

## 仍有效的架构备注（非本轮新决策）

1. **Server API「真本地盘」对照**：`PUT …/workspace/binding` 的 `root_id` 须为桌面 `addRoot` 铸造的句柄；纯 curl **无法**单独铸造本机根。对照抽检默认走 **云工作区 + `PUT …/workspace/files/{path}` 播种试件**（S6 已按此跑通），或人手在 Desktop 绑根后只把 `conversation_id` 交给 API 探针。
