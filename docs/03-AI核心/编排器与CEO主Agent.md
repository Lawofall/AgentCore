---
status: landed
code: apps/server/agentcore/
related:
  - docs/03-AI核心/运行时总览.md
  - docs/03-AI核心/执行引擎架构设计.md
  - docs/03-AI核心/检查点与开工卡.md
  - docs/05-平台与运维/平台LLM接入.md
skip_if:
  - 只改检查点卡片 UX（读检查点与开工卡 / 前端UX）
---

# 编排器与 CEO 主 Agent

> **权威范围**：CEO 定位、职责边界、路由 / 团队形态 / 认知分工判据、关键字段语义、冷启动探索幕**的编排流程**（触发条件权威在 [记忆 · 探索触发](/docs/03-AI核心/Agent记忆与知识系统.md)）、`replan`。开场卡与检查点 → [检查点与开工卡](/docs/03-AI核心/检查点与开工卡.md)。实现细节 → 见代码: `apps/server/agentcore/runtime/`。
>
> **主循环归属**：接住「**说**」（唯一对话入口）并实现「**组**」（判轻重、自动组队）→ [主循环](/docs/01-产品/产品定位与品牌.md)。

## 核心定位

编排能力归属会话型 **CEO 主 Agent**：唯一对话入口与声音，也是团队规划大脑。用户是老板；CEO 受雇掌管团队、对其负责——关键岔路请示、收尾汇报。确需团队时经 `delegate` 下达子任务，执行引擎调度 worker，CEO **用自己的声音**收尾。

CEO 是**管理者**（不是调查员）：主要持只读 / 检索工具，用于开工前轻量探路与收尾综述——**不**独自跑完整调查或亲手产出。本地且已装配 `terminal` 时，可对工作区长驻进程做启/停/读（纯启服轻量例外）。生产 / 变更一律 `delegate`；成规模广度调查（哪怕只读、最终只回一段话）也扇出并行调研 worker，回报精炼结论后由 CEO 综述。

底线：对用户呈现**一个 CEO 声音**；轻量 / 单点只读直答与纯启服（零编排开销）；组团 / 动手 / 广度调查按需触发。

### 职责边界

| ✅ CEO 做 | ❌ CEO 不做 |
|---|---|
| 与用户对话、来回澄清 | 持有写 / 改 / 删 / 移文件、Git 写入、跑代码等变更工具 |
| 轻量 / 单点只读直答（一两处文件 / 一条事实） | 亲自串行跑成规模广度调查 |
| 本地纯启服 / 重启 / 看长驻进程是否活着（`terminal`） | 用 `host_shell` 启长驻；改码 / 装依赖后仍假装自己动手 |
| 开工前只读探路；团队跑完写简短概览 | 为简单对话支付规划税 |
| 理解意图、拆任务、定角色与依赖（`depends_on`） | 复述各 worker 全文（细节由前端 run / 图视图展示） |

工具结构分界：`approval=NEVER` → CEO 持有；`GRANTABLE` schema → 仅 worker——**GRANTABLE 例外**：① 本机 Host 的 `host_shell`（CEO+worker · `host` 轴授 · 禁 kickoff 静默授；L2/L3 仍仅 worker）；② **`browser_navigate` / `click` / `type` / `scroll` / `snapshot`**（CEO+worker · `browser_class` · 有 Bridge/gVisor 才装配；captain 直调跳过审批；**`browser_screenshot` 仍仅 worker**）。另：**本地 `terminal`** 亦 CEO 可持（schema `NEVER`，`start` 运行时升审批，与 `git` 写同姿）——纯启服 / 停 / 读，非改产物。自研编排（否决 LangGraph / CrewAI 等）：编排是核心壁垒，须完全掌控。聊天优先 + 按需编排（否决「编排器唯一入口」——每条消息付编排税）。

**档位取舍**：档 2.5 = 结构取档 2（CEO 只读 + 窄例外；否决档 1 全能 CEO、档 3 纯编排 CEO）+ 路由按「活的规模与结构」细化。档 1 污染上下文、弱化团队心智；档 3 给高频轻量只读 / 纯启服加委派税。

**✅ 2026-08-18 复评：维持档 2.5，否决给 CEO 开有限写权。** 动机是压「小改动延迟」——用户只要改一行也得走完整派单。实测把动机否掉了（dogfood 付费 BYOK，**1 次**，非分布）：改一个标题端到端 **16.5s**，其中 5 次 `llm.call` 合计 15.0s，**管道固定开销仅 1.5s**；worker 冷开（`worker.prepare_phase`）总计 **16ms**，分段之和 12ms——冷开没有可压浪费，派单的贵全在**轮次结构**（多出 `delegate` / `handoff` / CEO 收口三跳 LLM）。CEO 直写最多省 6–8s，不足以换架构取舍：上下文污染是**复利**成本（文件内容进 CEO 上下文后，此后每回合都付），而收益一次性。同期查明真正白扔的时长在系统收口把同一结论重讲一遍（生产窗 61 次派单 21 次命中），已修 → [执行引擎 · 首回合 `attached_inject` 自己收口](/docs/03-AI核心/执行引擎架构设计.md)。**否决**通用 `file_write` / `file_move` 给 CEO（语义太宽，会被拿去干别的）；日后若仍要开，形态只能是语义封死的 CEO-only 窄口（与 `promote_product` 同族：只作用于已 accepted 产物、进 `file_products` 台账、可 diff 可回滚）。**另案**：CEO 持 `terminal` / `host_shell` 已能让字节落盘且**不进台账、不可 diff 回滚**——按阶梯先埋观测看真实流量是否发生，**禁止**直接提硬拦（→ `intercept-discipline`）。

## 路由 / 团队 / 认知分工

发问优先：先判是否**挡路**（无答复则不能负责任推进），再判规模。挡路 → `ask_user` 短澄清（可穿插探路）；能按合理默认续聊/推进 → 不当检查点。产品原则全文 → [检查点与开工卡 · §一 · 挡路拍板](/docs/03-AI核心/检查点与开工卡.md)；无开场提案/场面硬账。信息齐了再判自己做 vs 交团队。**规格已齐立刻派**与**载体/手段顾问**正交——见下节。

| 判据 | 结论 |
|---|---|
| **直答** | 单点确认、读已知少量文件、纯问答 / 闲聊、聊天里短文或短改写（未要求存文件）、开工前轻量探路；**本地纯启服 / 重启 / 看长驻是否活着**（`terminal`，勿为此派 `runtime_ready` 批） |
| **委派** | ① 实质交付物（代码 / 应用 / 要求落盘的成篇文字，哪怕一行）；② 成规模广度调查（横扫多来源、可拆多角度、需对比 / 辩论）——哪怕只读、最终只回一段话。单 worker 能胜任 → 派 1 人，收口仍由 CEO 写简短概览；形状拿不准 → `consult(team_orchestration_advanced)` |
| **团队形态** | 按活的自然缝拆、能少则少；可独立并行才多派；跨域合成流水线常见 1～2 人，勿默认每人一种专长。广度调查扇出并行调研，task 点明「回报精炼结论」。**结局分层**：先定桌上结果再组队——一起弄懂/多路摸清（未明示成文；「论文/开源」当资料 ≠ 成文）→ `parallel_brief`（方向笔记→CEO 对话综述；少扇出常 2）；明示报告/论文/落盘成文且需正式长文/可提交 → `research_report`（提纲→撰稿→审校）；点选成文但主题大/形态未定 → 先短摸底或提纲过目，勿立刻满编；普通构想不默认学术审校。**讨论类开场卡**：仅当可能变成组队摸清/成文且形态挡住编制时短问——默认推荐「摸清、对话对齐」，次选「写成文档保存」；桌上结果已是对话本身则正文推进、不发卡；选项只写桌上结果、不写编制（原则 → [检查点 · 挡路拍板](/docs/03-AI核心/检查点与开工卡.md)）。公共事件多维研判 → `multi_lens_research`；点名开辩 → `debate`。「多角度 / 多 Agent」≠成文产线。`result_handling` 只管上游→下游，**不**影响回到 CEO 的内容。**立刻派 ≠ 立刻全量**：方向/方案选定后仍立刻派，默认 MVP 或设计/API 契约切片；强耦合 UI / **多屏 UI / 单文件大原型**默认真两段（1 人两段=同人结构续派）或 wave1=`form=files`；**真两段≠同 task 文案两阶段**（须 wave 拆开 / `depends_on` / `continue_from_run_id`）；Electron/桌面壳可 `playbook=none` 但禁首 grant「设计+主进程/渲染/Agent核+可跑闭环」；禁首 grant「完整可玩 N 屏」（用户明示一次做完除外）；规格已齐≠全量；单页/落地页仍可一人整页（→ 见代码: `runtime/resolve/prompt/`）。**交付档（一等）**：先定桌上结果再填 `playbook_args.intensity`（结构槽，非意图分类器 / 禁扫用户原文）。建议 ask `label`：一页先上线 / 品牌站流水线 / 工具壳 / MVP 主流程可点 / 模块流水线一次做完 / 只改一处。映射：一页→`build_website`+`solo`；品牌站→`standard`；工具壳→同 playbook+`style=toolshed`；MVP→`build_app`+`lean`（默认）；模块流水线→`full`+显式 `modules`；只改一处→`build_feature`/手写/`repair_code`。`style` 只管气质；编制由 intensity 分支拓扑（→ 见代码: `playbooks/build_site.py` · `runs/build_app.py`） |
| **认知分工** | 约束归 CEO、专业方案归专家；task 只写【目标·约束·验收】；`contract` 是验收契约非结构蓝图；审查类「重点关注」进 `seed_notes`(kind=heads_up)，勿写进 task 替 worker 作答 |

短文分界：未要求存文件 → 回复直写；明确落盘 → 派 1 人。CEO 绝不为省委派把整份代码贴进正文。

### ✅ 载体/手段顾问式短对齐

- **目标**：目标跟用户；**手段**可由 AI 纠偏——点名载体/手段且（能力不够或明显次优）时，不默默锁死次优路径，也不擅自换载体开做。
- **行为**：先一次短 `ask_user`：推荐更好手段 + 明确选项「仍按你点的做」；用户坚持 → **零摩擦**按点名开做。触发含格式载体 / 本机路径与「框架别动 / 按模板 / 只换内容」类复刻约束。
- **与「规格已齐立刻派」正交**：**内容齐 ≠ 手段已核**——风格 / 站点类型 / 交付档等桌上规格已齐 → **立刻派**；但用户**已点名手段**且该手段能力不够或明显次优（含会明显损害可读 / 可扫 / 可编辑）→ **载体顾问优先**，规格已齐不得吞掉顾问；用户写死该手段只进「仍按你点的做」。勿把载体顾问扩成开场场面账；合理点名仍立刻派。
- **落地阶梯**：仅提示词 / Skill（拦截阶梯 1；→ 见代码: `runtime/resolve/prompt/` · `ask_user_kickoff`）；**禁**硬闸、**禁**意图分类器扫文猜「该不该换载体」、**禁**复活 `format_options` / 场面格式硬账（场面账权威 → [检查点与开工卡 · §一](/docs/03-AI核心/检查点与开工卡.md)）。提示词只写可复用原则，**禁**为单次失败 case 写话术剧本（具体回归放 conformance）。
- **验收**：读者能分清「规格已齐→立刻派」vs「点名手段次优→短顾问、坚持零摩擦」；否决项见下表。回归：`carrier_means_consult_*` conformance。

**部分材料明示范围**：用户附材料并收窄为本轮附件 / 工作区已有产物时，须先对照动手（缺口分析或改一版）；缺整仓只说明局限与单点缺件——禁止整轮只催源码。与打开本地项目正交（开项目=换工程面，非开工前置）。

**实证（一行）**：team 价值是同预算更便宜 / 更稳过硬性判据，非「更聪明」；跨域整合组队全面溃败 → 产品收窄为「按缝拆、跨域合成少派」。数据 → `apps/server/eval-out/`；跑法 → [本地开发 · evals](/docs/02-架构/本地开发.md)。

## `delegate` / `replan`

`delegate` 默认**非终态**：worker 跑完交回 CEO，CEO 写简短概览收尾（否决独立 SYNTHESIS 合稿节点；单 worker 成功亦然）。曾有 `finalize=true` 单人直出（HANDOFF 当回合答复、省合成轮），与「一个 CEO 声音」冲突，已撤。图由 CEO 在 ReAct 循环里增量声明——非外部一次性 JSON 计划。**参数主路**：默认手写顶层 `tasks`；具名 `playbook` = 固化流水线快捷套餐（与 tasks XOR，禁同时有内容；建站等可点名快捷）。

**跨文件夹（✅）**：跨已登记文件夹（只读摸底与写盘通吃）一律 `delegate` 各填 `target_folder_id`（=该队员坐哪张桌；写不写盘由 write_scope/grant 正交）；CEO 的 `list_folder_dir` / `read_folder_file` 仅派前轻量认桌/抽样，非摸底主通道；均不改会话 `folder_id`。点名：`list_folders` / `resolve_folder`（按路径解析，同名多层返歧义候选而非静默猜）；**显式**新建云桌用 `create_folder`（可带 `parent_path` 挂到某层下；仅用户点名新建 / 多线显式先建——禁止为过写盘闸而建）；`delete_folder` **只认 id**（跨层同名合法，按名删必然误删）。裸聊写盘缺桌 → 运行时 `ensure_bare_chat_auto_cloud_desk` 建云文件夹并 `turn_target_desk` 继承，**建成即在对话里告知落点**（`auto_folder_created`；告知非审批、不挂起、可当场改名）；首次落 `Conversation.auto_desk_folder_id`（不改出生 `folder_id`），后续回合复用；CEO 文件视野可坐落地桌。**文件夹根即工作区根**：空桌成品落在该文件夹根，禁止再套工程壳（✅ 写入剥壳）。进云另经桌面导入到云 / 连接 Git；本机传统走 `open_local_project` / `register_local_project` / `bind_local_folder`（合法非默认）。裸聊：纯对话/只读（非名册目标）可不点名（scratch 禁写）；名册目标与写盘禁默写 scratch。→ [工作区 · §五 · §5.4](/docs/02-架构/双模式工作区.md)。

| 动作 | 语义 |
|---|---|
| 一次塞 N 个 task | 全景计划（一批声明完整分工） |
| 同回合再调 `delegate` | 并入**【同一张】**协作图（同 `execution_id`）；协调中经 `live_plan`、本回合上一张图经自动合入，均可在 `build_run_plan` 前解析宿主节点；`depends_on` 可填本批 / 宿主声明 `id`、无歧义角色名。**同回合开辩同理**：一条消息里先 MLR 再 `debate` 合入本图 `acts[]` 加一幕（`act-2`，`anchor` = 合成节点），不铸第二张图——判据是 `same_turn`（宿主 `message_id` 是否就是本条消息），由 `host_graph_binding` 单点消费 |
| 跨回合延续 | 仍由模型显式表达「接着上一支团队干」（`append_to_execution_id` **只填 `"latest"`**，引擎解析到最近一张；模型侧拿不到图 id——`<recent_team_graph>` 与工具回显都不打印，只给队员名册 + `run_id`），语义是**新开一张锚在本回合的图 + 系统写 `prev_execution_id` 链回去**，不把新人塞进旧图。**判据是回合边界，不是上一张图死没死**：上一张仍在后台跑时同样新开——`adopt_active_execution` 只把本回合绑上那条 live execution 供 `wait` / `cancel_worker` / 插话路由，派单落图一律读本回合 mint 的 `execution_id`；让 adopt 顺带决定图归属，等于「上一轮碰巧还没跑完」就静默改变新人去哪张图。**已废**复用旧图继续生长（**跨回合**的辩论幕挂宿主同废；同回合开辩仍是同图加幕，见上行）——生长回流到已收口的旧宿主会让图锚错回合、进度分母吃旧节点、journal 因宿主回合已死被丢。**否决**「无参数自动链上一张」：同对话里毫不相干的新团队会被误判成延续。团队延续读图链，**否决**为此另建 team 实体（roster 仍只管同人续派的现场） |
| 并行度 | 由节点 `depends_on` 数据声明（无依赖即同波并行），非靠模型并行 tool call |
| 任务 `id` | flat / DAG 均保留声明 `id`（铸 `{prefix}_{raw}`）；未声明 flat 仍用序号。跨批依赖靠声明 id 或角色名，勿臆造未声明短 id |

**`replan`**（波边界续跑，与 `delegate` 正交）：含晚绑定（`bind_after_deps`）或队员 `escalate kind=scope` 时，调度器在决策边界让出；CEO 定稿 / 纠偏后续跑**同一张 DAG**。

| 参数 | 要点 |
|---|---|
| `binds` | 据上游产出把占位节点定稿（role / task / deliverable） |
| `steers` | 给尚未运行的下游追加操舵；已完成步骤不可操舵 |
| `add` | 追加计划外新节点（拓扑校验；未知依赖 / 成环等整批拒绝） |
| `stop` | 未跑步骤 SKIPPED，已完成产出交回 CEO 收尾 |

`binds+steers+add` 先全量校验，任一非法 → 整批拒绝、暂停计划零改动。否决把 `delegate` 重载成「续跑旧计划」入口；带现场续派另见 [多轮编排与同人续派](/docs/03-AI核心/多轮编排与同人续派.md)。

**✅ `promote_product`（成品归位）**：CEO 收口前把用户要的成品从 AI 工作间（`AgentCore/文档/*`）移进**用户工作区**的显式动作，只认 `delivery_status` 里 `accepted` 的产物。**为何独立工具而非放开 `file_move`**：后者 worker-only 且语义太宽，给 CEO 一把通用移动权会被拿去干别的；本工具语义单一，且移动本身就是可见的归位记录。**为何是收口时刻**——派单时 CEO 还不知道 worker 会产出几个文件、哪个是主的；写盘时 worker 手上只有自己的任务书（局部视角，且没有自贬动机，人人都说自己那份是成品则该字段作废）；收口是全链路唯一同时看得见用户原始请求与全部实际产出的位置。零归位合法（多幕中间幕），须在收口正文说明，**不设硬闸**。**跨回合可用**：批次收尾 → `ask_user` 问用户要不要 → 下一轮再搬，这是主流路径不是边角；回合台账取不到对账时回退本会话最近一条落盘 `delivery_status`（同 conversation、台账优先、仍只放行 accepted）——那是已落盘的对账结果，不是重算验收。归位后按同 `execution_id` 重发，结构化路径字段（`delivered_files` · `artifacts[].path` / `derived_from` · `gaps[].paths`）一并按归一化路径改写，自由文里的路径**不扫不替**；跨回合再归位时旧 `{from,to}` 行保留（旧路径唯一的回查线索）→ [执行引擎 · 可用性诚实性](/docs/03-AI核心/执行引擎架构设计.md)、[术语表 · 成品归位](/docs/01-产品/术语表.md)。

协调模式（根 CEO；**含单 worker**）：默认后台跑、CEO 继续 ReAct；`coordinate=false` / 嵌套 lead / 含 `checkpoint_after` 仍阻塞。单 worker 也进协调，是为让用户插话在派单期可达（阻塞路径下 CEO 把执行权交给了 worker，插话读到也无从响应）并让 CEO 手上有 `cancel_worker`；开工卡与团队合成预览各自的 ≥2 闸**独立保留**，单 worker 的零摩擦外观不因此变重。结构跟着证据走：调研成篇用 `depends_on` + `checkpoint_after` 把「定结构」摆到调研之后。委派后用团队产出写综述（提示强化，非硬禁只读）；根 CEO 探路成功的 list/read/grep/code_search 经路径筛选后摘要注入 worker 开局。worker 协作通道 → [Agent 协作模式](/docs/03-AI核心/Agent协作模式.md)。协调 `wait` 在用户侧热审批/授权未决时禁止空等（勿假装推进）；用户显式停止 / regenerate 会 orphan 热交互并写入 journal（取活 turn 的 `message_id` 作 `turn_id`，非路径上的用户消息 id）。**协调期 CEO 可见面纪律**（提示/工具 schema）：图在转无新结论时可静默；禁止用用户可见 content 复述「谁还在跑」类进度（协作图是进度真相）；开口仅请示 / 报告阻塞与选项 / 宣布阶段结论；插话须先回用户句；`update_synthesis` 禁纯进度播报；协调态进度旁白经 `deliverable_only` 不进终稿 `messages.content`（过程仍进 process）。

### 批次入闸 vs 回合收敛（边界）

`drive` / `replan` **开工前**的批次门控（`post_close_gate`：收口后冷开整团重派硬拒；`channel_dead_gate`：通道死且需写桌则硬拒，任何 `force` scope 都不逃生；`completion` / `supervised`：能力·冷启动软检与补跑合入）与 engine 回合内收敛门控**分轨**——权威总述 → [执行引擎 · 治理门控双轨](/docs/03-AI核心/执行引擎架构设计.md#治理门控双轨)。新闸：能拒整批开工的挂 delegate；只影响 ReAct 继续/丢稿的挂 engine。

#### 同队续派 = 一等入口（定案 · 审计 D3）

批次一收口，协调会话不再活跃、`replan` 也不在（它只活在受监督态），「给同一支团队补跑 / 接着干」于是只剩会被冷开闸拦下的冷派——**缺的是入口，不是模型服从度**。四道闸（收口后冷开 / 通道死写桌 / 同构 / 触顶换马甲）曾各自建议「显式传 `force=true`」，而那一开同时关掉全部四道。

定案：把同队续派提升为一等入口，闸从「拒绝并要求模型改写参数」改为**按结构路由**。`post_close_gate` 先把批次拆三堆（只看结构字段，**不扫 task 自由文、不做意图分类**）：

| 堆 | 判据 | 入闸 |
|---|---|---|
| **续派** | `continue_from_run_id` 指向非缺口 run | 不进闸、**不限条数**（名册与作者链 `recall_count` 自然兜底；目标现场存不存在由续派执行层逐节点如实拒） |
| **补缺口** | `replaces_run_id`，或 `continue_from` 指向 FAILED/SKIPPED | 仍按 `MAX_GAP_FILL_ADDS` 限流（与同图 `replan` 补跑闸同判定） |
| **冷开** | 两者皆无 | 只对**这一堆**判 substantial 大扇出并拒 |

配套：`<recent_team_graph>` 事实行补出 `run_id=`（不给 run_id，点名入口就是空头支票——这正是旧实现把模型逼向 `force` 的一环）。**否决**为此新增工具 / 新增与 `append_to_execution_id` 语义重叠的参数：入口就是 `delegate.tasks[]` 上的两个既有结构字段。→ 见代码: `runtime/delegate/team_continuation.py`

#### `force` = 逐闸开关，不跨调用（ORCH-A2）

`force` 收一个**闸名数组**（`post_close` / `isomorphic` / `thrash` / `seat_overlap`），每道闸只问自己那一格；**没有「全开」值**，历史布尔 `force=true` 解析成空集（记一条 info，不再放行任何闸）。各闸拒绝正文报出**自己的** scope 名，不再互相顺手打开。

`force` 不再是工具实例上的长命标记：`execute` / `replan` **各自在入口无条件重解析**——旧实现里一次冷派的 `force` 会经实例状态漏进随后的 `replan`（座位重叠闸静默失守）。→ 见代码: `runtime/delegate/force_scopes.py`

**触顶换马甲闸的记忆有窗**：闸给的唯一出路是对那个 run 设 `continue_from_run_id` 带现场续派，而现场活在留人名册里，所以闸的对话级记忆与名册**同寿**——按记录入册时间过 idle TTL 即失效，进程内再按对话 LRU 淘汰（此前是模块级 dict，进程内永驻，几十轮后重开同一主题的新人仍被一条谁也用不上的旧记录拦住）。回收只做减法：判据、相似度口径与拒绝文案一概不动，过期只让闸**更少**开火。→ 见代码: `runtime/coordination/thrash.py`

### 执行写路径 vs 进度读视图

| 面 | 职责 | 禁止 |
|---|---|---|
| **`drive*`**（`drive` / `drive_coordinated` + setup/preview/finalize/terminal/redirect） | 派发与执行**写路径**：建图、跑批、收口记账 | 为「好看」改写协作图投影语义冒充执行真相 |
| **`CoordinationSession.live_plan`** | 协调态执行真相（由 supervised / host / session 恢复写入） | 经只读投影回写 |
| **`pipeline_view`** | 只读进度投影，注入 CEO 可见面 | 当作第二写路径 |
| **`isomorphic`（+ thrash）** | **drive 入闸**（拒同构再派 / 拒触顶换马甲），各认各的 `force` scope，不是 UI fold | 与前端图折叠混为一谈；一道闸的放行顺手开另一道 |
| **前端协作图** | SSE → `projectExecution` 等**读投影** | 反向充当执行权威 |

**定案**：本轮不把 drive 事件流合成进协作图通道（合成列观察项）。写只走 drive / session；读视图与 UI 只派生。本表是该分工的**唯一权威**（协作模式 / 协作图 UX 只留短指针）→ [协作图 UX](/docs/04-前端/协作图与双视图UX.md)

收尾：先对账拼图边（4b：冲突 / 缺口 / 重复）→ 核验原始目标（4a：完工判定）→ 写概览；未达成就续派 / `replan`，别假装收工。`playbook`：建站/工具台/绿场软件(`build_app`)推荐具名形状（不再硬拒 `none`/手写）；`build_website` 默认 `intensity=standard`（三串），`solo`=一人整页；`build_app` 默认 `intensity=lean`（三节点），`full`=五阶段+模块扇出——已确认 MVP /「先…以后再说」禁默升 `full`。多角摸清/讨论对齐默认 `parallel_brief`，正式长文成文专线 `research_report`（点选成文≠立刻满编；普通构想不默认学术审校），代码审计 `code_audit`（单缝只 `scope`；探路见 ≥2 可并行子面则填 `modules`，按自然缝扇出、整仓/多子系统常 4–8、能少则少，折叠顶 8；playbook **不**从 scope 自动拆、禁把多目录拼进 scope），其余自由组队（可选快捷形状）。**Agent/自动化**不靠场面账三档硬闸；缺形态且挡住编制时 `ask_user` 短问（可能成摸清/成文则默认推摸清对齐；桌上结果已是对话本身则不发卡；糊「做个网站」须消歧展示页/工具壳/业务应用 + 本轮桌上档），由模型自洽选择交付路径 → [检查点与开工卡 · §一 · 挡路拍板](/docs/03-AI核心/检查点与开工卡.md)。对抗性多视角另走 `debate` → [辩论编排设计](/docs/03-AI核心/辩论编排设计.md)。

提示词分层：常驻 = 路由脊柱 + 按需目录 + 短钩子；进阶 HOW 在系统 Skill，用时 `consult`。同一条知识只在唯一所有者出现。全局工作纪律分层：共享基座 `<work_authority>`（权威序 / **当前课题：工作区＞全局「正在做 X」** / 冲突通道 escalate·ask_user / 决策权限，CEO+worker）；CEO core 仅权威线索、「继续项目跟工作区」与「未定案·窄」钩；进阶 HOW → `consult(work_discipline)`（设计三问、补丁绊线等）。禁止为读规则再派 worker。**跨回合交付账本 one-shot**：上轮 journal 的 `delivery_status` 为 `partial`/`blocked` 且含 blocking gaps 时，下轮 CEO 易变尾注入一次性可忽略 `<prior_delivery_gaps>`（与 `<prior_delegate_retry>` 互斥、缺口优先；真源仅上一回合 journal，勿粘 conversation 全局 latest）；不 emit / 不 stamp verdict。→ 见代码: `runtime/delegate/prior_delivery_gaps.py`。**跨回合同一动作徒劳 one-shot**：上轮 journal 的 `tool_call.cross_turn_retry=futile`（未知/缺失/`not_futile` 不收）时，下轮 CEO 易变尾注入一次性可忽略 `<prior_futile_retries>`（真源仅上一其它回合 journal；空则产出空串、assembler 丢段以保住 prefix cache）；提示信息、不拦截。→ 见代码: `runtime/delegate/prior_futile_retries.py`。

## 关键字段语义（摘要）

| 字段 / 概念 | 语义要点 |
|---|---|
| `depends_on` | 并行 / 串行的唯一开关；空 = 可立即并行；调度器据依赖定并行度。同回合二次委派解析范围 = 本批 ∪ 宿主图（活跃 `live_plan` 或本回合上一张图）；失败回执列可用节点 + 可执行下一步（角色名 / id） |
| `result_handling` | 上游→下游注入保真：`pass_through`（默认偏全文）/ `summarize`；**不**作用于 CEO 综述。综述是否带 worker 正文另由「叶子 + 无落盘」判定（有下游 / 落盘者只吃交接简报）→ [Agent 协作模式 · handoff](/docs/03-AI核心/Agent协作模式.md) |
| `complexity_hint` | `light`/`standard`：编排姿态（如 light 隐含 `coordination=none`），**不**映射 worker token/超时 |
| `coordination` | 便签墙档；缺省 `none`；权威 → [Agent 协作模式](/docs/03-AI核心/Agent协作模式.md) |
| `deliverable` | 落盘契约 = `form=files` 和/或非空 `artifacts`（否决悬空 `output_schema`）。`form=prose` = 纯文字交付（写工具仍装配，靠角色提示自觉勿乱写）；`form=files` / 省略 = 可写盘。`form=prose` 不得同时声明非空 `artifacts`（硬拒）。**`form` 只表交付形态，不再代理探索期「别乱写工程」、也不再硬卸写工具**。约定文档中间笔记（`AgentCore/文档/{research,reviews,debate}/`）默认**不**计入 `form=files` 修码产品落盘（零写 soft），除非 `artifacts` 声明该路径。**数据文件整理且无执行**：完整交付 = 原件结构报告 + 待跑变换脚本（`artifacts` 写死这两份）+ 一句「运算环境暂时不可用，稍后再试」——这是完成态，不是「表的缺口」；禁止手抄 csv 顶替、禁止让用户绑本机文件夹。硬缺口 `no_exec_table` 的前提是本回合存在 worker 无法可靠解析的源数据文件（附件 / 工作区源文件的类型信号，不扫正文、不靠文件名）；数据内联在消息里、无此类源文件时落 csv/xlsx 不是缺口。否决加闸扫收口话术。→ skill `data_file_landing`。✅ S3：不再有按 criteria kind 的队形闸。✅ 已删 `requires_files` / `name` / `must_contain` / `min_length`（见下节） |
| `write_scope` ✅ | worker 本批可写范围：`none` / `explore_memory`（仅 `AgentCore/` 约定记忆与探索笔记）/ `project`（用户工程树，默认满权限批次）。探索硬挡 pending 时上限 `explore_memory`；越权在**写工具层**拒，不在 `delegate` 入口因 `form=files` 拒整批。否决：explore 专用 playbook 分叉、pending 时静默把 files 改成 prose |
| ~~`completion_criteria`~~ | ✅ **已删**（S3）——见下节；工具面 `test_run`↔`code_execute` 分流仍在（与 kind 正交） |
| ~~`requires_files` / `name` / `must_contain` / `min_length` / `objective` / `playbook_none_reason`~~ | ✅ **已删**（交付契约瘦身）——见下节 |
| `continue_from_run_id` | 带现场续派；权威 → [多轮编排与同人续派](/docs/03-AI核心/多轮编排与同人续派.md) |
| worker 模型 ✅ | 每 worker **节点/run** 可选显式模型身份（与辩论辩手身份同族）；**省略** = Worker 槽（空则 follow 主）。CEO/`tasks[]` 节点显式仍有效；开工卡确认面**不**提供人改模。定案全文 ↓ |

嵌套委派：硬上限 `depth≤3`（合法链 CEO → depth1 → depth2 → depth3 叶子；depth&lt;3 默认获 `delegate`，`replan` 在已有子计划后挂上），无 `can_delegate` 字段。worker 工具集缺省全量（内部装配）；CEO **不必也不应**手填 `tasks[].tools` 收窄（填了也不生效）。depth=1 captain 对路径 B 形 brief（成果级目标·约束·验收、本轮无结构钉成单切片）→ **优先**先再 `delegate` 补编制再整合（优先级 nudge，非硬流程、非「未嵌套禁写」）；豁免：单文件 / 已钉薄壳 / 强耦合同 run 切片 / 小修·机械单步。整里程碑 M0 / 空仓多模块骨架不在豁免内。**禁止**「凡大活必嵌套」；失败只回退提示词，不升级闸。父节点已盖 `code_audit_gate` 时，手写嵌套 tasks **继承**收工纪律（盖 gate、补 `*.audit.json`、注入一次交接短提示、补与 playbook 同字面的 `required_sections`——含独立「设计如此」栏）——不重跑整本 `code_audit` playbook，以免单点子审被扩成多模块团；普通非审计嵌套不误挂。 CEO 手写路（不传 playbook）对已声明 `reviews/` 落盘的审计节点同样盖上该章节契约（不扫 role/task）。

### ✅ S3 · 删除 `completion_criteria` kind 体系

- **目标**：删掉批次验收 kind 枚举与按 kind 的硬挡；引擎不再按验收标签硬判完成。错收工接盘 = CEO 提示词复盘 + 现有 deliverable / contract / 落盘 soft + 人审。
- **边界**：已退役 `files_written` / `code_verified` / `runtime_ready` / `graph_consistent` / `custom` 等 **kind 枚举与按 kind 的 binding**，以及 `validate_criteria_kind_fit`。schema **已删除** `completion_criteria` 字段。保留 deliverable / contract / 落盘 soft / 人审与工具面 `test_run`↔`code_execute` 分流。省略即常态，无「显式 kind → binding」分叉。
- **关键取舍**：选删体系（S3）；**否决**领域 kind 扩表（S2a/b）。**禁**边删边加新启发式完成硬闸；**禁**把「验码绿」等再伪装成新 kind。
- **验收**：schema / 运行时无 kind 枚举与按 kind 批次硬挡；`validate_criteria_kind_fit` 不存在；无替代 kind 的新启发式完成硬闸。→ 见代码: `runtime/delegate/completion.py` + `tools/builtin/delegate/schema.py`

### ✅ 交付契约瘦身（删 A+B+C 字段）

- **目标**：从 CEO/`replan` 可见契约删掉低价值参数，减少填参面与 soft 验收噪音。
- **已删**：`playbook_none_reason`、`deliverable.name`、`tasks[].objective`、`must_contain`、`min_length`、`requires_files`。写盘只认 `form=files` 和/或非空 `artifacts`；目标语义并入 `task`。
- **成篇硬门**：只认具名 `playbook=research_report`；**否决**字数结构腿与扫 task 自由文补门。handoff 正文地板 = 非空（不暴露 CEO 字数旋钮）。
- **`code_audit` 报告纪律 ✅**（与成篇硬门正交）：具名 `playbook=code_audit`；Markdown + 配套 `*.audit.json`；章节契约区分属实缺陷 / 设计如此（模块 docstring 或设计文档已写明的目标形态，独立成栏、不进 N）/ 观察与工程债；手写路经同一继承函数盖上同字面 `required_sections`；字段闭集含验证方式 / 定案 / 严重度（未读全·待核实不得标中+）；`code_audit_gate` 结构闸；写盘通道死时缺 JSON 不硬充结构失败。→ 见代码: `runtime/runs/playbooks/audit.py` · `code_audit_gate.py`
- **边界**：保留 `form` / `artifacts` / `artifact_dir` / `strict` / `required_sections` / `output_format`；顶层 D 档旋钮（`complexity_hint` 等）本刀不动；内部 `write_scope` 不变。建站 `must_contain_soft` 位保留（非已删 `must_contain`）。
- **验收**：schema / 类型 / 合同 / 预算 / 切片钉无上述字段功能依赖；→ 见代码: `tools/builtin/delegate/schema.py` + `runtime/runs/types.py` + `runtime/runs/contract.py`

### ✅ 删除角色名启发式改写 deliverable

- **目标**：删除 `is_independent_review_role` 等对 **role 名正则/子串** 的匹配，以及由此触发的**静默改写** deliverable（抬 `form=files` / 塞 `reviews/` artifacts / 追加纪律文案）。
- **边界**：审校落盘纪律**仅当** playbook 或 deliverable **已声明** `form=files` / 非空 `artifacts`（或等价结构 flag）时施加；`research_report` 等在 playbook **写死**审校员 files 契约，不靠运行时猜角色名。成篇审计硬门只认具名 `playbook=research_report`（无字数结构腿）。**不加**新 `completion_criteria` kind（遵守 ✅ S3）。
- **关键取舍**：删猜测入口优于保留误伤面；否决「降软但仍扫角色名」。能力回退用 playbook 结构补，不靠旁路正则。
- **验收**：无角色名→改写 deliverable 路径；名叫「审校/review」但未声明 files 的轻角色不被抬契约；playbook 审校默认落盘仍成立。→ 见代码: `runtime/runs/research_quality.py`（结构谓词）+ `runtime/runs/playbooks/research.py`（审校 files 契约）

### ✅ 接通 consumer_deps 软提示

- **目标**：`check_consumer_missing_depends` 命中时，软警告进入 **CEO 可见**的委派工具结果（尾巴/note），不再只打日志。
- **边界**：仍**不拒收、不自动补 `depends_on`、不改图**；一次性软提示，**禁止**因「软无效」升级硬拒或累计计数。cue 保持窄（队友/上游产出指称）；误伤大则收窄正则，不扩面。
- **关键取舍**：漏边是真 DAG 危害，接通优于半残日志；否决「只留观测日志当终态」。净负则删闸，不加硬闸。
- **验收**：同批「吃队友」且 `depends_on` 空 → CEO 当轮结果可见告警文案；委派仍成功入图。→ 见代码: `tools/builtin/delegate/tool.py`（tails）+ `runtime/delegate/consumer_deps.py`

### ✅ 接通 design_impl_same_grant 软提示

- **目标**：单 task / 单 grant 同时塞设计+实现（artifacts 设计类+代码类，或 task 文案「阶段 A」+「阶段 B」）且未结构拆开时，软警告进入 **CEO 可见**委派结果尾。
- **边界**：仍**不拒收、不自动拆 tasks、不改图**；一次性软提示，**禁止**升级硬拒 / 累计计数 / 扫用户长文意图。已拆两波（`depends_on` / `checkpoint_after`）、仅设计、仅代码、轻量单文件小改不命中。
- **关键取舍**：混装是真波次危害，软提示优于默许；否决硬拒与自动改图。净负则删闸。
- **验收**：DESIGN+src 同 task → CEO 可见建议拆波文案；委派仍成功。→ 见代码: `tools/builtin/delegate/tool.py`（tails）+ `runtime/delegate/design_impl_slice.py`

### ✅ 接通 root_slice_honesty 软提示（根委派切片诚实）

- **目标**：根侧 `depth=0` 单节点手写写工程且无结构钉本轮切片时，软警告进入 **CEO 可见**委派结果尾——把「立刻派 ≠ 立刻全量」落成通用能力（非场景特例）。
- **命中**（可证明结构）：无具名 playbook ∧ 恰好 1 task ∧ 显式写工程（`form=files`；form 省略不算）∧ 无切片钉。
- **切片钉白名单**（任一豁免）：非空 `artifacts` / `artifact_dir` / 非空 `required_sections` / 本 task `checkpoint_after`。
- **路径**：根多节点 / 具名 playbook / deliverable 钉边界（A）与 **单 lead 嵌套扇出**（B）等价合法；软文案须明示嵌套可用。路径 B 与整锅入口同构 → **接受软提示对 B 亦响**（nudge，非拒）。路径 B 责任落 **lead**：接到成果级且无结构钉时 **先招人再整合**（captain 常驻短判决 + skill 旋钮；非硬流程、非「未嵌套禁写」），非强制 CEO 改平铺、亦非「凡大活必嵌套」。
- **编排自主（✅ 提示/技能，非硬编码 playbook）**：范围大或拆缝不清时，CEO/lead 可自判 **摸底波→专班**（同批 `depends_on` 或再 `delegate`/`replan`）与路径 A/B 并列；通用于审计/摸仓/大改等，**禁止**写成「凡 X 必两拨人 / 必嵌套」。CEO **不知轻重时禁止猜「一人能扛整座成果」**——缝不清先短摸底再专班，缝已在文档/目录则按块派（不必先称每块有多重）；任务里写「先组队 / 你可以组队」**不算**已拆编制。交 lead 只写目标·约束·验收，禁止「你去执行整个里程碑」口吻。路径 B 下 lead：成果级无钉 → 先招再整合；**已钉薄切片却读出整仓** → `escalate kind=scope`，不默默扩编。「不要为委派而委派」只约束本来就小的活。真两段结构 OK；同 task 假两段仍禁。不扩软闸、不按读轮次催招。→ `skills`「编排自主·摸底波 / 专班 / 嵌套」· CEO `【立刻派 ≠ 立刻全量】` · captain `_WORKER_CAPTAIN_INTRO`
- **边界**：仍**不拒收、不改图**；不做硬拒；不扫用户/task 长文；不用 `write_scope`（非 grant 槽）。阶梯沿用 `design_impl` 先例（提示词后直接软提示）。
- **验收**：单 task + `form=files` + 无钉 → CEO 可见告警；具名 playbook / 有 artifacts 等 → 不告警。→ 见代码: `tools/builtin/delegate/tool.py`（tails）+ `runtime/delegate/root_slice_honesty.py`

### ✅ Per-worker 模型覆盖（A+B+C 同一功能）

- **目标**：每个 worker run 可绑与队友不同的大模型——**一个功能**：① 省钱默认（组合 Worker 槽便宜、节点不填）；③ CEO 在 `delegate`/`tasks[]` 填写节点显式。效果好坏另说，能力要有；默认路径与今日一致（不填=跟槽）。开工卡等**产品确认面不提供**人手改模（原「人盖 CEO」产品入口已藏）。
- **边界**：解析优先级 **节点显式 > 组合 Worker 槽 > follow 主模型**。CEO/`tasks[]` 节点显式仍有效。wire 可保留 `model_overrides` 契约，**不**写产品确认面用法。续派 / 同人续跑：默认 **继承该 run 已解析模型**；本次 payload 显式改则覆盖。候选与组合槽、辩论身份对齐（统一目录 + BYOK 手填；platform allowlist）；非法配置 **硬失败**，禁 silent 回退。协作图与用量须能看见该队员用了哪个模型。凭据 / sidecar / 跨 origin → [平台 LLM 接入](/docs/05-平台与运维/平台LLM接入.md)。
- **关键取舍**：能力开放（含 CEO 可选填）优先于「怕选不好就关能力」；确认面藏人改模 UI，**不**删后端节点显式 / 契约字段。**否决**角色名猜模、质量档矩阵、账号级角色→模型主设置、无可见性的暗箱路由。旧 `ModelTier` / `model_preference` 档位体系仍废，不复活。
- **落地 ✅**：① CEO/`replan` `tasks[]` 三元组→路由键→`RunSpec.model`（`runtime/delegate/task_models.py`）；跨 provider 窄接 extras；续派继承可改。③ 节点显式仍走执行链；`model_overrides` 契约可保留（非确认面产品用法；开工卡 UI 不暴露人改模）。sidecar inference proxy 认节点路由键。图 peek / 用量详情经 `run_completed.model`（跑中 Face chip 非本切片）。
- **验收**：省略显式 → 行为同今日全体 Worker 槽；节点显式且合法 → 仅该 run 用该模；非法 → 硬失败可见；开工卡确认面无人改模入口；续跑继承可改；完成后图/用量可观测。

## 冷启动探索幕

**触发（有项目）** ✅ 软硬分层（取代「空画像即挡请求」）——**触发条件表不在本文**：闸看的全是记忆态（画像空否 / `explore_workspace_key` / 指纹），权威 → [记忆 · 探索触发与挡请求](/docs/03-AI核心/Agent记忆与知识系统.md)。本文只管**开幕之后怎么编排**。

一句话记住分层：软幕（仅空画像）不挡请求；硬挡三因（换绑 / 用户点名 / 空画像+工程信号）先探索再继续；指纹漂移不挡，走脏标记 + 旁路刷新。

**硬挡流程**：注入 `<cold_start_explore>`（换绑 / 点名刷新 / 空画像+工程信号；指纹与「仅空画像」**不**进此块）→ 先轻量探路（≤探路硬上限，见下段；同轮并行多工具只计 1 轮）→ `delegate`（`team_preview`）组调研队（**≥2 角并行**，禁止 1 人包办整仓）→ 收尾经 `update_folder_profile` 合并写文件夹 **画像 + 导航.md**，记录 `workspace_key` 与指纹；主题软顶 5 / 总数受 `memory_max_topic_files` → **立刻继续原请求**。pending 期间允许 `form=files`，但写盘不得出 `explore_memory` 根——**无例外**（原 `code_verified` 修码批例外已随 S3 kind 退役 ✅）；厚背景资料（`主题/` 条目）不在本幕写。产物谁写的完整口径 → [记忆 · 产物谁写 D1](/docs/03-AI核心/Agent记忆与知识系统.md)。点名硬闸（与 pending 同级）✅。**resume 与开场同源**：空画像软降级走 `resolve_hard_explore_reason`，禁止 resume 把「仅空画像」误硬拦。

**强制 / 豁免**：点名强制开幕（合并更新；硬闸 ✅）。旧画像无 key → 不因缺 key 硬开。裸聊 / 纯闲聊 / 空工作区不自动开幕、不写假画像/导航。对已有工程「继续开发 / 全面摸底」亦须 ≥2 角并行（提示词纪律；冷启动闸另硬拒单 worker）。探路硬闸**不扫用户原文猜意图**分叉：统一「到限后 delegate，或短答并自报归类（闲聊/单点事实/追问）」；闸后长文一律丢稿再催一次。轮数唯一真源 = `settings.engine_team_gate_investigation_rounds`（默认 7；提示词文案与本文都跟它，禁各处硬编码）——实测触发时模型每轮并行 2–3 次调用，但 3/3 触发都有整轮花在幻觉路径上，故：**一轮内调查调用全失败不计探路轮**（那轮没换到情报，不该扣广度预算；防空转仍靠既有同目标 spin / 工具失败熔断 / unproductive 梯子，不新加兜底层）；**`delegate` 成功即归还本闸收走的只读工具**（闸的目的是逼派工，不是罚一整回合——否则 CEO 派完读不了 worker 落盘产物，收尾对账打折；只还本闸新增那批，熔断 / 通道死 / `read_url` 退役收走的不复活，`team_gate_fired` 仍锁定不重开计数）。**两处边界（已确认，勿再提案）**：① 归还只认 `delegate` 成功，`debate` 不还——闸催的出口就是派工，辩论后 CEO 消化的是辩论结论；真出现「辩后须读落盘取证才能成文」再放。② 闸后挂起（审批 / `ask_user`）→ resume 不重剥：被剥名单是 loop 局部态、不进 seed，闸标记却锁着，等于恢复后工具自动回来——**有意不堵**，堵它要改 seed 契约，而挂起点用户在场、乱查可见，收益不抵成本。成篇形状 / 修码选型 / 跑·修·打开验证终向 / 点名对比扇出靠提示词与结构验收，不靠意图分类器（`exec_verify` 用户意图硬闸、`named_entity_fanout` 用户扫硬拒已移除）。成篇审计硬门只认成文专线 `playbook=research_report`（无字数结构腿）；`parallel_brief` / 普通多角摸底不进硬门（软闸亦同）；硬门**不**扫 task/角色自由文。✅ 已删角色名扫与静默改写 deliverable（审校落盘靠 playbook/结构声明，见上节）。审后默认向用户收口，同轮 `continue_from_run_id` 修订非默认路径。

**边界**：不新建 Explore 原语；指纹 = 顶层树 + 关键清单（不以纯天数 / commit 为唯一闸）。产物只落 `AgentCore/`（记忆；厚背景资料走 `主题/` 按需条目，且不在探索 pending 批）。**权威分层**：触发条件 / 产物谁写 / 主题上限 → [记忆 · 探索触发](/docs/03-AI核心/Agent记忆与知识系统.md)；探路轮数闸 / `delegate` 组队纪律 / 收尾 → **本文**。

**否决（本定案）**：pending 时按 `form=files` 拒整批；为冷启动单开 prose 版调研 playbook；delegate 入口静默改写 playbook/tasks XOR。

## 失败与否决（一行）

| 场景 / 方案 | 处理或否决理由 |
|---|---|
| `delegate` 参数非法 | 非终态回 CEO，改参重试 |
| 单 worker 失败 | 按 `on_failure`；宽松扇入默认放行，不必拖垮整 DAG |
| 无需团队 | 不调 `delegate` = 单 Agent 直答 |
| 纯路由器替换 CEO / 前置分类器 / Worker 直连 / 取消 CEO 综述 | 规划壁垒、编排税、不可观测、丧失「一个声音」 |
| 累计 N 次只读软提醒护栏 | A/B 净负已移除；靠提示词边界 + 失控硬兜底 |
| 默认加硬闸/软闸「优化服从度」 | **否决**；阶梯见 `.cursor/rules/intercept-discipline.mdc`（提示词→观测→一次性软提示→结构化硬拒） |
| 扩大收口姿势 A / 完成话术正则冒充近零误报 | **否决** → [执行引擎 · 可用性诚实性](/docs/03-AI核心/执行引擎架构设计.md) |
| 未派工靠禁语表 / 完成话术正则拦正文 | ✅ **已撤**（诚实性走 `team_batch` 结构面）→ 同上 |
| 无对账卡时拦同条 A∪C | ✅ **已撤**（无卡不拦正文）→ 同上 |
| 零写落盘声称扫词硬回炉（答完清气泡） | ✅ **已撤**（2026-08-09 定案 B；解释禁语误伤）→ 同上 |
| 领域 kind 扩表 / 边删 kind 边加启发式完成硬闸 | **否决**；S3 接盘见上节 |
| `validate_criteria_kind_fit` 扫 task 拟合硬闸 | ✅ **已随 S3 退役** |
| `host_shell` fuse 改可批可跑（B）/ 仅改文案当终案（D） | **否决** → [安全 · 熔断方案 C](/docs/05-平台与运维/安全权限与治理.md) |
| 扫角色名静默改写 deliverable | **已删**（见上节） |
| 扫 role·task 自由文正则决定产物落 `research` 还是 `reviews` | ⏳ **净删除**（成品归位）：意图分类器形态，且误判对用户不可见；落点只认显式来源，其余进 `工作稿/` → [工作区 §四](/docs/02-架构/双模式工作区.md#四约定文档目录约定) |
| 产物地位（成品 vs 过程材料）靠路径推断 / 派单预判 / worker 自判 | **否决**：只有收口时刻看得见用户原始请求 + 全部实际产出 → [术语表 · 成品归位](/docs/01-产品/术语表.md) |
| 手写 `min_length` 字数腿 / 扫自由文补成篇硬门 | **否决**（成篇硬门只认 `research_report`；见交付契约瘦身） |
| 裸 `requires_files` 第三写盘开关 | **已删**（写盘只认 `form=files` ∪ `artifacts`） |
| `consumer_deps` 软警告只打日志 | **已接通**（见上节） |
| `design_impl_same_grant` 设计+实现同 grant | **已接通**软提示（见上节）；禁硬拒 / 自动改图 |
| `root_slice_honesty` 根单节点手写写工程无切片钉 | **已接通**软提示（见上节）；路径 B 嵌套合法且可响；禁硬拒 / 扫长文 / 用 `write_scope` |
| 载体/手段纠偏靠硬闸 / 意图分类器 / 复活 `format_options` | **否决**；定案为提示词/Skill 顾问短问（见上节 · 载体/手段顾问） |
| 账号级角色→模型矩阵 / `ModelTier{fast,strong}` 质量档 / 自动降级 / silent 回退野模型 | **否决**（与 per-run 显式覆盖正交；见上节 Per-worker） |
| 无 UI 的 CEO 暗箱选模（有字段但图/用量不可见） | **否决** |

## 开场卡 / 检查点

`ask_user` 通用澄清、`team_preview` 团队预审、`checkpoint_after` 波边界把关 → 全文见 [检查点与开工卡](/docs/03-AI核心/检查点与开工卡.md)，本文不复述。
