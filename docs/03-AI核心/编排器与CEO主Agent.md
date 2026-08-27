---
status: landed
code: apps/server/agentcore/
related:
  - docs/03-AI核心/运行时总览.md
  - docs/03-AI核心/执行引擎架构设计.md
  - docs/03-AI核心/检查点与开工卡.md
  - docs/03-AI核心/上下文工程.md
  - docs/05-平台与运维/平台LLM接入.md
skip_if:
  - 只改检查点卡片 UX（读检查点与开工卡 / 前端UX）
---

# 编排器与 CEO 主 Agent

> **权威范围**：CEO 定位、职责边界、路由 / 团队形态 / 认知分工判据、**具名 playbook 准入与名单**、关键字段语义、冷启动探索幕**的编排流程**（触发条件权威在 [记忆 · 探索触发](/docs/03-AI核心/Agent记忆与知识系统.md)）、`replan`。开场卡与检查点 → [检查点与开工卡](/docs/03-AI核心/检查点与开工卡.md)。实现细节 → 见代码: `apps/server/agentcore/runtime/`。
>
> **主循环归属**：接住「**说**」（唯一对话入口）并实现「**组**」（判轻重、自动组队）→ [主循环](/docs/01-产品/产品定位与品牌.md)。

## 核心定位

编排能力归属会话型 **CEO 主 Agent**：唯一对话入口与声音，也是团队规划大脑。用户是老板；CEO 受雇掌管团队、对其负责——关键岔路请示、收尾汇报。确需团队时经 `delegate` 下达子任务，执行引擎调度 worker，CEO **用自己的声音**收尾。

CEO 是**管理者**，也是判断者：要不要拉人由他判（像主程序员决定要不要开子代理）。主要持只读 / 检索工具。对话、方案、判断、读少量文件默认自己做；生产 / 变更一律 `delegate`（档 2.5：CEO 无写权）。成规模取证按自然缝编制（能 1 人则 1 人，真并行才多人），禁止自己连搜收齐结论；人数不按话题类型套默认。已装配 `terminal` 时，可对工作区长驻进程做启/停/读（纯启服轻量例外；云桌 guest / 本机桌面）。

底线：对用户呈现**一个 CEO 声音**；轻量 / 讨论 / 单点只读直答与纯启服（零编排开销）；组团 / 动手 / 成规模取证按需触发。

### 职责边界

| ✅ CEO 做 | ❌ CEO 不做 |
|---|---|
| 与用户对话、来回澄清；对话 / 方案 / 判断自己开口 | 持有写 / 改 / 删 / 移文件、Git 写入、跑代码等变更工具 |
| 轻量 / 单点只读直答（一两处文件 / 一条事实）；讨论对齐时读设计文档 | 亲自串行跑成规模取证 |
| 已装配时纯启服 / 重启 / 看长驻是否活着（`terminal`，云桌/本机） | 用 `host(action=shell)` 启长驻；改码 / 装依赖后仍假装自己动手 |
| 开工前只读探路（定位入口，不收集结论）；团队跑完写简短概览 | 为简单对话支付规划税；自己摸完整场 |
| 理解意图、拆任务、定角色与依赖（`depends_on`） | 复述各 worker 全文（细节由前端 run / 图视图展示） |

**探路 vs 摸底**（提示词纪律，不是引擎硬闸）：探路只回答「从哪几个入口进」，默认 0～1 轮；讨论 / 判断读设计文档 = 自己做，不是摸底。成规模取证 = 摸底：禁止自己搜完再整理，编制按自然缝自选（能 1 人则 1 人，真并行才多人）；禁止因话题像盘点 / 架构就默认 ≥2。**派工先判要不要**（必须=改产物 / 应该=成规模取证），**再判拉几人**（真并行才多人）；讨论不因「未落盘」而必须派。**组队靠提示词（CEO 自判）**；引擎不剥调查工具、不丢闸后长文。探路纪律仍是 0～1 轮，不因长文还在窗口就接着自己取证。引擎不扫用户原文猜意图。

工具结构分界：`approval=NEVER` → CEO 持有；`GRANTABLE` schema → 仅 worker——**GRANTABLE 例外**：① **`browser(action=navigate|click|type|scroll|snapshot|console)`**（GRANTABLE · CEO+worker · `browser_class` · 有 Bridge/gVisor 才装配；captain 直调跳过审批；**`screenshot` 仍仅 worker**）。另：**`terminal`**（云桌 guest / 本机桌面）与 **`host`** 均为 schema `NEVER`、CEO 可持，运行时按 action 升审批（`terminal start` / `host` 的 GRANTABLE action 走 `host` 轴；worker-only action 拒并 `delegate`；禁 kickoff 静默授）——纯启服 / 短命令 / 本机观测，非改产物。动作表 → [工具 · Host](/docs/03-AI核心/工具与能力系统.md)。自研编排（否决 LangGraph / CrewAI 等）：编排是核心壁垒，须完全掌控。聊天优先 + 按需编排（否决「编排器唯一入口」——每条消息付编排税）。

**档位取舍**：档 2.5 = 结构取档 2（CEO 只读 + 窄例外；否决档 1 全能 CEO、档 3 纯编排 CEO）+ 路由由 CEO 自判（讨论自己做；改产物必须派；成规模取证应该派；真并行才多人）。档 1 污染上下文、弱化团队心智；档 3 给高频轻量只读 / 纯启服加委派税。

**档 2.5 维持，否决给 CEO 开写权。** 小改动也走派单：贵的是多出的 `delegate` / `handoff` / 本轮写结尾，不是 worker 冷开。CEO 直写省下的是一次性秒数，上下文污染是复利（文件进 CEO 窗口后此后每回合都付）。**否决**通用 `file_write` / `file_move` 给 CEO（语义太宽，会被拿去干别的）。若仍要开，只能是语义封死的 CEO-only 窄口（只作用于已 accepted 产物、进 `file_products` 台账、可 diff 可回滚）——**否决**再立搬家工具当收口仪式 → [术语表 · 成品归位](/docs/01-产品/术语表.md)。CEO 持 `terminal` / `host(action=shell)` 已能让字节落盘且不进台账、不可 diff 回滚：先埋观测看真实流量，**禁止**直接提硬拦（→ `intercept-discipline`）。系统收口第二轮把同一结论重讲一遍已删 → [执行引擎 · 团队终态](/docs/03-AI核心/执行引擎架构设计.md)。

## 路由 / 团队 / 认知分工

发问优先：先判是否**挡路**（无答复则不能负责任推进），再判规模。挡路 → `ask_user` 短澄清（可穿插探路）；能按合理默认续聊/推进 → 不当检查点。产品原则全文 → [检查点与开工卡 · §一 · 挡路拍板](/docs/03-AI核心/检查点与开工卡.md)；无开场提案/场面硬账。信息齐了再判自己做 vs 交团队。**规格已齐立刻派**与**载体/手段顾问**正交——见下节。

| 判据 | 结论 |
|---|---|
| **直答** | 对话 / 方案 / 判断、单点确认、读已知少量文件、纯问答 / 闲聊、聊天里短文或短改写（未要求存文件）、开工前探路（只定位入口）；**本回合能力问答对照能力行**（已装配则 `consult`/短探，禁止用邻格未装配写否决论文；权威 → [上下文工程](/docs/03-AI核心/上下文工程.md)）；**已装配时纯启服 / 重启 / 看长驻是否活着**（`terminal`，云桌/本机；勿为此派 `runtime_ready` 批） |
| **委派** | **必须**（工具边界）：实质交付物（代码 / 应用 / 要求落盘的成篇文字，哪怕一行）——CEO 无写权；**应该**（规模）：成规模取证（横扫多来源、自己会连搜收齐）。讨论不因「未落盘」而必须派。几人见下行 |
| **团队形态** | 按活的自然缝拆、能少则少；可独立并行才多派；点名对比 N 个对象 → 至少 N 人（同一讨论多个切面 ≠ N）；单 worker 能胜任 → 派 1 人。跨域合成流水线常见 1～2 人，勿默认每人一种专长。广度调查按缝编制（能 1 人则 1 人，真并行才多人），task 点明「回报精炼结论」。**结局分层**：先定桌上结果再组队——一起弄懂/多路摸清（未明示成文；「论文/开源」当资料 ≠ 成文）→ 编制自选（1 人 / 真并行才 `map_fanout`：方向笔记一页地图→CEO 对话综述；人数跟缝走；方向一句目标、不列书单）；明示报告/论文/落盘成文且需正式长文/可提交 → `cite_write_review`（提纲→撰稿→审校）；点选成文但主题大/形态未定 → 先短摸底或提纲过目，勿立刻满编；普通构想不默认学术审校。**未明示成文 = 禁止成文产线，不是禁止组队**（禁双专家 `form=files` / `cite_write_review` 满编；真并行摸清才 `map_fanout`）；规格已齐的构建或卡已结算 → 立刻派，勿把「答完澄清」做成默认 `end_turn`。→ 见代码: `runtime/resolve/prompt/ceo_core.py`（桌上结果脊柱）。**讨论类开场卡**：仅当可能变成成文/落盘且形态挡住编制时短问——默认推荐「对话对齐」，次选「写成文档保存」；桌上结果已是对话本身则不发卡、不写盘（CEO 自己开口）；选项只写桌上结果、不写编制（原则 → [检查点 · 挡路拍板](/docs/03-AI核心/检查点与开工卡.md)）。公共事件多维研判 → `lens_crosscheck`；点名开辩 → `debate`。「多角度 / 多 Agent」≠成文产线。上游注入下游由引擎保真（有文件递路径），**不**影响回到 CEO 的内容。**立刻派 ≠ 立刻全量**：方向/方案选定后仍立刻派，默认 MVP 或设计/API 契约切片；强耦合 UI / **多屏 UI / 单文件大原型**默认真两段（1 人两段=同人结构续派）或 wave1=`form=files`；**真两段≠同 task 文案两阶段**（须 wave 拆开 / `depends_on` / `continue_from_run_id`）；Electron/桌面壳可 `playbook=none` 但禁首 grant「设计+主进程/渲染/Agent核+可跑闭环」；禁首 grant「完整可玩 N 屏」（用户明示一次做完除外）；规格已齐≠全量；单页/落地页仍可一人整页（→ 见代码: `runtime/resolve/prompt/`）。**交付档（一等）**：先定桌上结果再填 `playbook_args.intensity`（结构槽，非意图分类器 / 禁扫用户原文）。建议 ask `label`：MVP 主流程可点 / 模块流水线一次做完 / 只改一处。映射：MVP→`build_app`+`lean`（默认）；模块流水线→`full`+显式 `modules`；只改一处→手写/`diagnose_fix_verify`。建站 / 落地页手写一人整页（禁具名建站套餐、禁编制档）；糊说「做个网站」只消歧展示页 / 工具壳 / 业务应用。编制由 intensity 分支拓扑仅约束绿场（→ 见代码: `runs/build_app.py`） |
| **认知分工** | 约束归 CEO、专业方案归专家；task 只写【目标·约束·验收】；编制按自然缝自选、不按话题套默认人数；摸底方向一句目标，不列书单/大纲；`contract` 是验收契约非结构蓝图；审查类「重点关注」进 `team_brief` 或由审查员 `post_note`(heads_up)，勿写进 task 替 worker 作答 |

短文分界：未要求存文件的短文 / 短改写 / 讨论判断 → 回复直写；成规模取证才派；明确落盘 → 派 1 人。CEO 绝不为省委派把整份代码贴进正文。

### 载体/手段顾问

目标跟用户；**手段**可由 AI 纠偏——点名载体/手段且（能力不够或明显次优）时，不默默锁死次优路径，也不擅自换载体开做。先一次短 `ask_user`：推荐更好手段 + 明确选项「仍按你点的做」；用户坚持 → **零摩擦**按点名开做。触发含格式载体 / 本机路径与「框架别动 / 按模板 / 只换内容」类复刻约束。

**与「规格已齐立刻派」正交**：**内容齐 ≠ 手段已核**——风格 / 站点类型 / 交付档等桌上规格已齐 → **立刻派**；但用户**已点名手段**且该手段能力不够或明显次优（含会明显损害可读 / 可扫 / 可编辑）→ **载体顾问优先**，规格已齐不得吞掉顾问；用户写死该手段只进「仍按你点的做」。勿把载体顾问扩成开场场面账；合理点名仍立刻派。

仅提示词 / Skill（拦截阶梯 1；→ 见代码: `runtime/resolve/prompt/` · `ask_user_kickoff`）；**禁**硬闸、**禁**意图分类器扫文猜「该不该换载体」、**禁**复活 `format_options` / 场面格式硬账（场面账权威 → [检查点与开工卡 · §一](/docs/03-AI核心/检查点与开工卡.md)）。提示词只写可复用原则，**禁**为单次失败 case 写话术剧本（具体回归放 conformance）。

**部分材料明示范围**：用户附材料并收窄为本轮附件 / 工作区已有产物时，须先对照动手（缺口分析或改一版）；缺整仓只说明局限与单点缺件——禁止整轮只催源码。与打开本地项目正交（开项目=换工程面，非开工前置）。

**实证（一行）**：team 价值是同预算更便宜 / 更稳过硬性判据，非「更聪明」；跨域整合组队全面溃败 → 产品收窄为「按缝拆、跨域合成少派」。数据 → `apps/server/eval-out/`；跑法 → [本地开发 · evals](/docs/02-架构/本地开发.md)。

## `delegate` / `replan`

`delegate` 默认**非终态**：worker 跑完交回 CEO，CEO 写简短概览收尾（否决独立 SYNTHESIS 合稿节点；单 worker 成功亦然）。曾有 `finalize=true` 单人直出（HANDOFF 当回合答复、省合成轮），与「一个 CEO 声音」冲突，已撤。图由 CEO 在 ReAct 循环里增量声明——非外部一次性 JSON 计划。**参数主路**：默认手写顶层 `tasks`；具名 `playbook` = 固化流水线快捷套餐（与 tasks XOR，禁同时有内容）。准入与现行名单 → 下文「具名 playbook」。

**跨文件夹（✅）**：跨已登记文件夹一律 `delegate` 各填 `target_folder_id`（写不写盘由 write_scope/grant 正交）；CEO 的 `list_folder_dir` / `read_folder_file` 仅派前轻量认桌；均不改会话 `folder_id`。裸聊建桌、剥壳、Composer 三选、本机传统 → [工作区 · §五、绑定：文件夹即工作区 · 双通道入口](/docs/02-架构/双模式工作区.md)。

| 动作 | 语义 |
|---|---|
| 一次塞 N 个 task | 全景计划（一批声明完整分工） |
| 同回合再调 `delegate` | 并入**【同一张】**协作图（同 `execution_id`）；协调中经 `live_plan`、本回合上一张图经自动合入，均可在 `build_run_plan` 前解析宿主节点；`depends_on` 可填本批 / 宿主声明 `id`、无歧义角色名。**同回合开辩同理**：一条消息里先 MLR 再 `debate` 合入本图 `acts[]` 加一幕（`act-2`，`anchor` = 合成节点），不铸第二张图——判据是 `same_turn`（宿主 `message_id` 是否就是本条消息），由 `host_graph_binding` 单点消费 |
| 跨回合延续 | 仍由模型显式表达「接着上一支团队干」（`append_to_execution_id` **只填 `"latest"`**，引擎解析到最近一张；模型侧拿不到图 id——`<recent_team_graph>` 与工具回显都不打印，只给队员名册 + `run_id`），语义是**新开一张锚在本回合的图 + 系统写 `prev_execution_id` 链回去**，不把新人塞进旧图。**判据是回合边界，不是上一张图死没死**：上一张仍在后台跑时同样新开——`adopt_active_execution` 只把本回合绑上那条 live execution 供 `wait` / `cancel_worker` / 插话路由，派单落图一律读本回合 mint 的 `execution_id`；让 adopt 顺带决定图归属，等于「上一轮碰巧还没跑完」就静默改变新人去哪张图。**已废**复用旧图继续生长（**跨回合**的辩论幕挂宿主同废；同回合开辩仍是同图加幕，见上行）——生长回流到已收口的旧宿主会让图锚错回合、进度分母吃旧节点、journal 因宿主回合已死被丢。**否决**「无参数自动链上一张」：同对话里毫不相干的新团队会被误判成延续。团队延续读图链，**否决**为此另建 team 实体（roster 仍只管同人续派的现场） |
| 并行度 | 由节点 `depends_on` 数据声明（无依赖即同波并行），非靠模型并行 tool call |
| 任务 `id` | flat / DAG 均保留声明 `id`（铸 `{prefix}_{raw}`）；未声明 flat 仍用序号。跨批依赖靠声明 id 或角色名，勿臆造未声明短 id |

**`replan`**（波边界续跑，与 `delegate` 正交）：队员 `escalate kind=scope`、或内部/套餐留下的待定稿步就绪时，调度器在决策边界让出；CEO 定稿 / 纠偏后续跑**同一张 DAG**。手写「下游还没想好」→ 先跑上游，再 `replan(add)` / 再 `delegate`，不在派工表上留坑。

| 参数 | 要点 |
|---|---|
| `binds` | 据上游产出把占位节点定稿（role / task / deliverable） |
| `steers` | 给尚未运行的下游追加操舵；已完成步骤不可操舵 |
| `add` | 追加计划外新节点（拓扑校验；未知依赖 / 成环等整批拒绝） |
| `stop` | 未跑步骤 SKIPPED，已完成产出交回 CEO 收尾 |

`binds+steers+add` 先全量校验，任一非法 → 整批拒绝、暂停计划零改动。否决把 `delegate` 重载成「续跑旧计划」入口；带现场续派另见 [多轮编排与同人续派](/docs/03-AI核心/多轮编排与同人续派.md)。

**成品归位工具已撤。** 收口再搬一次是空桌仪式。过程稿默认 `工作稿/`；用户要拿走的文件在派单时用 `form=workspace` / 显式 `artifacts` 直接写入工作区；打开入口是终稿路径可点与工作区树。`delivery_status.promoted` 仅兼容历史事件。→ [术语表 · 成品归位](/docs/01-产品/术语表.md)。

协调模式（根 CEO；**含单 worker**）：默认后台跑、CEO 继续 ReAct——CEO **不可**再勾阻塞。同步阻塞只剩引擎路径：嵌套 lead / 成篇套餐提纲把关 / 画布人工把关。单 worker 也进协调，是为让用户插话在派单期可达（阻塞路径下 CEO 把执行权交给了 worker，插话读到也无从响应）并让 CEO 手上有 `cancel_worker`；`team_preview` 产品位已拆（编制到即开跑），单 worker 的零摩擦外观不因此变重。结构跟着证据走：调研成篇用 `depends_on` 把「定结构」摆到调研之后，提纲把关走 `cite_write_review` 或先派再问。委派后用团队产出写综述（提示强化，非硬禁只读）；根 CEO 探路成功的 list/read/grep/code_search 经路径筛选后摘要注入 worker 开局。worker 协作通道 → [Agent 协作模式](/docs/03-AI核心/Agent协作模式.md)。协调 `wait` 在用户侧热审批/授权未决时禁止空等假装推进，应报告阻塞（等你允许、点名工具）后听团，队还在；引擎不得把未决允许卡收成整队取消 → 见代码 `has_hot_user_pending`。用户显式停止 / regenerate 会 orphan 热交互并写入 journal（取活 turn 的 `message_id` 作 `turn_id`，非路径上的用户消息 id）。**例行成功完成不叫醒 CEO**（调度与协作图已自己往下走）；**失败立刻叫醒**（还可补人），跳过/取消挂在失败或全员完成/整队取消上，不单独一轮。**协调期 CEO 可见面纪律**（提示/工具 schema）：图在转无新结论时可静默；禁止用用户可见 content 复述「谁还在跑」类进度（协作图是进度真相）；开口仅请示 / 报告阻塞与选项 / 宣布阶段结论；插话须先回用户句；`update_synthesis` 禁纯进度播报；协调态进度旁白经 `deliverable_only` 不进终稿 `messages.content`（过程仍进 process）。

### 批次入闸 vs 回合收敛（边界）

`drive` / `replan` **开工前**的批次门控（`post_close_gate`：收口后冷开整团重派硬拒；`channel_dead_gate`：通道死且需写桌则硬拒，任何 `force` scope 都不逃生；`completion` / `supervised`：能力·冷启动软检与补跑合入）与 engine 回合内收敛门控**分轨**——权威总述 → [执行引擎 · 治理门控双轨](/docs/03-AI核心/执行引擎架构设计.md#治理门控双轨)。新闸：能拒整批开工的挂 delegate；只影响 ReAct 继续/丢稿的挂 engine。

### 同队续派 = 一等入口 {#同队续派一等入口}

批次一收口，协调会话不再活跃、`replan` 也不在（它只活在受监督态），「给同一支团队补跑 / 接着干」于是只剩会被冷开闸拦下的冷派——**缺的是入口，不是模型服从度**。**否决**一键 `force=true`（会同时关掉收口后冷开 / 通道死写桌 / 同构 / 触顶换马甲）。

同队续派是一等入口：闸**按结构路由**，不要求模型改写参数才能过。`post_close_gate` 先把批次拆三堆（只看结构字段，**不扫 task 自由文、不做意图分类**）：

| 堆 | 判据 | 入闸 |
|---|---|---|
| **续派** | `continue_from_run_id` 指向非缺口 run | 不进闸、**不限条数**（名册与作者链 `recall_count` 自然兜底；目标现场存不存在由续派执行层逐节点如实拒） |
| **补缺口** | `replaces_run_id`，或 `continue_from` 指向 FAILED/SKIPPED | 仍按 `MAX_GAP_FILL_ADDS` 限流（与同图 `replan` 补跑闸同判定） |
| **冷开** | 两者皆无 | 只对**这一堆**判 substantial 大扇出并拒 |

配套：`<recent_team_graph>` 事实行补出 `run_id=`（不给 run_id，点名入口就是空头支票——这正是旧实现把模型逼向 `force` 的一环）。**否决**为此新增工具 / 新增与 `append_to_execution_id` 语义重叠的参数：入口就是 `delegate.tasks[]` 上的两个既有结构字段。→ 见代码: `runtime/delegate/team_continuation.py`

#### `force` = 逐闸开关，不跨调用

`force` 收一个**闸名数组**（`post_close` / `isomorphic` / `thrash` / `seat_overlap`），每道闸只问自己那一格；**没有「全开」值**，历史布尔 `force=true` 解析成空集（记一条 info，不再放行任何闸）。各闸拒绝正文报出**自己的** scope 名，不再互相顺手打开。

`force` 不是工具实例上的长命标记：`execute` / `replan` **各自在入口无条件重解析**——一次冷派的 `force` 不得漏进随后的 `replan`（否则座位重叠闸静默失守）。→ 见代码: `runtime/delegate/force_scopes.py`

**触顶换马甲闸的记忆有窗**：闸给的唯一出路是对那个 run 设 `continue_from_run_id` 带现场续派，而现场活在留人名册里，所以闸的对话级记忆与名册**同寿**——按记录入册时间过 idle TTL 即失效，进程内再按对话 LRU 淘汰。**否决**进程内永驻名册（会把谁也用不上的旧记录一直拦住同主题新人）。回收只做减法：判据、相似度口径与拒绝文案一概不动，过期只让闸**更少**开火。→ 见代码: `runtime/coordination/thrash.py`

### 执行写路径 vs 进度读视图

| 面 | 职责 | 禁止 |
|---|---|---|
| **`drive*`**（`drive` / `drive_coordinated` + setup/preview/finalize/terminal/redirect） | 派发与执行**写路径**：建图、跑批、收口记账 | 为「好看」改写协作图投影语义冒充执行真相 |
| **`CoordinationSession.live_plan`** | 协调态执行真相（由 supervised / host / session 恢复写入） | 经只读投影回写 |
| **`pipeline_view`** | 只读进度投影，注入 CEO 可见面 | 当作第二写路径 |
| **`isomorphic`（+ thrash）** | **drive 入闸**（拒同构再派 / 拒触顶换马甲），各认各的 `force` scope，不是 UI fold | 与前端图折叠混为一谈；一道闸的放行顺手开另一道 |
| **前端协作图** | SSE → `projectExecution` 等**读投影** | 反向充当执行权威 |

**不把** drive 事件流合成进协作图通道（合成列观察项）。写只走 drive / session；读视图与 UI 只派生。本表是该分工的**唯一权威**（协作模式 / 协作图 UX 只留短指针）→ [协作图 UX](/docs/04-前端/协作图与双视图UX.md)

收尾：先对账拼图边（4b：冲突 / 缺口 / 重复）→ 核验原始目标（4a：完工判定）→ 写概览；未达成就续派 / `replan`，别假装收工。`playbook`：绿场软件(`build_app`)推荐具名形状（不再硬拒 `none`/手写）；建站 / 工具台 / 点名对比 / 局部单功能一律手写（单页一人；控制台别套营销皮；落盘网页自动跑静态质检，DESIGN 合同仍跟内部 `web_quality_scan` 旗标）——**已删**具名 `build_website` / `build_website_verify` / `compare_options` / `build_feature`；`build_app` 默认 `intensity=lean`（三节点），`full`=五阶段+模块扇出——已确认 MVP /「先…以后再说」禁默升 `full`。多角摸清按缝编制（真并行才 `map_fanout`；讨论对齐默认 CEO 自己开口），正式长文成文专线 `cite_write_review`（点选成文≠立刻满编；普通构想不默认学术审校），代码审计 `code_audit`（单缝只 `scope`；探路见 ≥2 可并行产品缝则填 `modules`，先 2–3、能少则少；**2 路无主管、≥3 路才主管速览**；目录细拆仅当探路证明真并行且单缝扛不住；折叠顶 8；playbook **不**从 scope 自动拆、禁把多目录拼进 scope）。名单与准入 → 下文「具名 playbook」。**Agent/自动化**不靠场面账三档硬闸；缺形态且挡住编制时 `ask_user` 短问（可能成文/落盘则默认推对话对齐；桌上结果已是对话本身则不发卡；糊「做个网站」须消歧展示页/工具壳/业务应用，禁编制档 / 禁具名建站套餐），由模型自洽选择交付路径 → [检查点与开工卡 · §一 · 挡路拍板](/docs/03-AI核心/检查点与开工卡.md)。对抗性多视角另走 `debate` → [辩论编排设计](/docs/03-AI核心/辩论编排设计.md)。

提示词怎么写（宪法非法例、一层一所有者、事故入场闸；工作纪律分层见所有者表）→ [上下文工程 · 提示词设计原则](/docs/03-AI核心/上下文工程.md#提示词设计原则)。禁止为读规则再派 worker。**跨回合交付账本 one-shot**：上轮 journal 的 `delivery_status` 为 `partial`/`blocked` 且含 blocking gaps 时，下轮 CEO 易变尾注入一次性可忽略 `<prior_delivery_gaps>`（与 `<prior_delegate_retry>` 互斥、缺口优先；真源仅上一回合 journal，勿粘 conversation 全局 latest）；不 emit / 不 stamp verdict。→ 见代码: `runtime/delegate/prior_delivery_gaps.py`。**跨回合同一动作徒劳 one-shot**：上轮 journal 的 `tool_call.cross_turn_retry=futile`（未知/缺失/`not_futile` 不收）时，下轮 CEO 易变尾注入一次性可忽略 `<prior_futile_retries>`（真源仅上一其它回合 journal；空则产出空串、assembler 丢段以保住 prefix cache）；提示信息、不拦截。→ 见代码: `runtime/delegate/prior_futile_retries.py`。

## 具名 playbook {#具名-playbook}

> 服务主循环「**组**」：老板不选流程、不填表单。CEO 默选手写编制；只有冻死还值的流水线才进名单。

**主路**是手写顶层 `tasks`。具名 `playbook` 与 `tasks` XOR：点名一本 + 填槽，引擎展开成 `build_run_plan` 已能消费的 tasks 数组——同一条执行管线，不加子系统。不是通用模板引擎，也不是老板的流程菜单。工具箱官方模板是 **`PLAYBOOKS` 的精选子集**（现 `map_fanout` / `cite_write_review` / `build_app`），不是 1:1；窄协议 / 恢复 / 续派形状留在 CEO 运行时词汇。

### 准入（加本必须同时满足；过不了 → 手写或技能）

1. **拓扑固定**：除同类扇出超限折叠外，不按调用改节点数 / 角色。`build_app.intensity=lean|full` 是**绿场唯一允许的结构槽**（不拆成两个 playbook 名）；其它分叉退回手写。
2. **内部合同手写抄不便宜**：结构闸、引用时序、命题卡、必填验收槽。只有 task 文案不同 → 不算。
3. **高频，且 CEO 手写经常写错形状**（漏审校、漏 `verify`、一人包办 N 对象）。
4. **与现有名字正交**：同拓扑只差默认槽 / 落盘文案 → 合并进槽，或删较弱的那本。
5. **不拿场景当名字**：「调研 / 审计 / 修码」是桌上结果或技能；playbook 名的是流水线形状。

**否决**：按场景加本（建站、对比选型、单功能交付）；把 brief / 对比 / 多透镜合成一本再加 `kind=` 槽（分叉藏进一名）；扫用户原文猜该用哪本；把 `debate` 收进 `PLAYBOOKS`（它已是另一条确定性骨架）。

### 现行名单

id 标流水线形状，不标桌上结果。**不设别名**，旧名与未知名同处理。

| 名字 | 形状 |
|---|---|
| `cite_write_review` | 取证→提纲→成文→独立审校；成篇硬门只认此名 |
| `code_audit` | 五章 + `*.audit.json` 结构闸；名的是发现台账，不泛化成任意 ledger |
| `diagnose_fix_verify` | 无先验调查批的单症状三波；`verify` 必填 |
| `build_app` | 绿场高频；`intensity` 见准入第 1 条 |
| `lens_crosscheck` | 异质透镜交叉核验；默认透镜 + `motion_card` |
| `map_fanout` | N 路并行一页地图、无合成/审校节点；防误升成文专线 |

**已删**：`compare_options`（与手写「点名对比 ≥N 人」同拓扑，路由金标也不推它）；`build_feature`（便签墙 + 前后端并行 = 技能「契约共享面」，且 `include[]` 分叉）；更早的 `build_website` / `build_website_verify`（建站手写一人整页）。升墙只认非空 `team_brief` / 种子便签，不再因具名本默认建墙。

→ 见代码: `runtime/runs/playbooks/` · `workflows/playbook_templates.py`

## 关键字段语义（摘要）

| 字段 / 概念 | 语义要点 |
|---|---|
| `depends_on` | 并行 / 串行的唯一开关；空 = 可立即并行；调度器据依赖定并行度。同回合二次委派解析范围 = 本批 ∪ 宿主图（活跃 `live_plan` 或本回合上一张图）；失败回执列可用节点 + 可执行下一步（角色名 / id） |
| ~~`require_upstream`~~ | ✅ **CEO 不填**。默认 ≥1 上游成功即跑；缺席标前置缺席；零成功才跳过。严格级联仅内部/多余键。 |
| ~~`result_handling`~~ | ✅ **CEO 不填**。有落盘递路径；散文默认全文、过长引擎裁。不作用于 CEO 综述。→ [Agent 协作模式 · handoff](/docs/03-AI核心/Agent协作模式.md) |
| ~~`complexity_hint`~~ | ✅ **CEO 不填**。单人只交文字时引擎可自判轻（不建墙）；不映射 worker token/超时 |
| ~~`coordination`~~ | ✅ **CEO 不填**。便签墙缺省无；非空 `team_brief` 升墙。权威 → [Agent 协作模式](/docs/03-AI核心/Agent协作模式.md) |
| `deliverable` | **✅ 派活单三档**：`form` = `prose`（看）/ `files`（存文档，默认 `工作稿/`，漏填即此）/ `workspace`（改工程，吞 `workspace_native`）；可选 `artifacts`（钉路径，不扫 task 自由文）。节点上永远有 Deliverable。`form=prose` 不得同时声明非空 `artifacts`（硬拒）。CEO/`replan` schema 只暴露 `form`+`artifacts`；章节 / JSON / `strict` / `artifact_dir` / 引用闸 / 审计闸 / `web_quality_scan` 等仍可解析、不进填参面。**`form` 只表交付形态，不再代理探索期「别乱写工程」、也不再硬卸写工具**。零写 / 路径对不上是 **soft warnings**（非 `strict` 仍 COMPLETED）。✅ 任何成功写盘都算产品落盘（含 `research/` 等中间笔记；`artifacts` 不再门控是否计入）→ 见代码: `runtime/runs/landing_product.py`。**数据文件整理且无执行**：完整交付 = 原件结构报告 + 待跑变换脚本（`artifacts` 写死这两份）+ 一句「运算环境暂时不可用，稍后再试」——这是完成态，不是「表的缺口」；禁止手抄 csv 顶替、禁止让用户绑本机文件夹。硬缺口 `no_exec_table` 的前提是本回合存在 worker 无法可靠解析的源数据文件（附件 / 工作区源文件的类型信号，不扫正文、不靠文件名）；数据内联在消息里、无此类源文件时落 csv/xlsx 不是缺口。否决加闸扫收口话术。→ skill `data_file_landing`。不再有按验收 kind 的队形闸。已删 `requires_files` / `name` / `must_contain` / `min_length` / `must_contain_soft`（见下节） |
| `write_scope` ✅ | worker 本批可写范围：`none` / `explore_memory`（仅 `AgentCore/` 约定记忆与探索笔记）/ `project`（用户工程树，默认满权限批次）。探索硬挡 pending 时上限 `explore_memory`；越权在**写工具层**拒，不在 `delegate` 入口因 `form=files` 拒整批。否决：explore 专用 playbook 分叉、pending 时静默把 files 改成 prose |
| ~~`completion_criteria`~~ | **已删**——见下节；工具面 `test_run`↔`code_execute` 分流仍在（与验收标签正交） |
| ~~`requires_files` / `name` / `must_contain` / `min_length` / `objective` / `playbook_none_reason`~~ | ✅ **已删**——见下节 |
| `continue_from_run_id` | 带现场续派；权威 → [多轮编排与同人续派](/docs/03-AI核心/多轮编排与同人续派.md) |
| worker 模型 ✅ | 每 worker **节点/run** 可选显式模型身份（与辩论辩手身份同族）；**省略** = Worker 槽（空则 follow 主）。CEO/`tasks[]` 节点显式仍有效；无开工卡确认面（产品位已拆），人不在卡上改模。定案全文 ↓ |

嵌套委派：硬上限 `depth≤3`（合法链 CEO → depth1 → depth2 → depth3 叶子；depth&lt;3 默认获 `delegate`，`replan` 在已有子计划后挂上），无 `can_delegate` 字段。worker 工具集缺省全量（内部装配）；CEO **不必也不应**手填 `tasks[].tools` 收窄（填了也不生效）。depth=1 captain 对路径 B 形 brief（成果级目标·约束·验收、本轮无结构钉成单切片）→ **优先**先再 `delegate` 补编制再整合（优先级 nudge，非硬流程、非「未嵌套禁写」）；豁免：单文件 / 已钉薄壳 / 强耦合同 run 切片 / 小修·机械单步。整里程碑 M0 / 空仓多模块骨架不在豁免内。**禁止**「凡大活必嵌套」；失败只回退提示词，不升级闸。父节点已盖 `code_audit_gate` 时，手写嵌套 tasks **继承**收工纪律（盖 gate、补 `*.audit.json`、注入一次交接短提示、补与 playbook 同字面的 `required_sections`——含独立「设计如此」栏）——不重跑整本 `code_audit` playbook，以免单点子审被扩成多模块团；普通非审计嵌套不误挂。 CEO 手写路（不传 playbook）对已声明 `reviews/` 落盘的审计节点同样盖上该章节契约（不扫 role/task）。

### 交付验收：无 kind 枚举、无按 kind 硬挡 {#交付验收}

schema **没有** `completion_criteria` 字段。引擎不按验收标签硬判完成。错收工接盘 = CEO 提示词复盘 + deliverable / contract / 落盘 soft + 人审。省略即常态。工具面 `test_run`↔`code_execute` 分流仍在，与验收标签正交。

**否决**领域 kind 扩表；**否决**边删边加新启发式完成硬闸；**否决**把「验码绿」等再伪装成新 kind。→ 见代码: `runtime/delegate/completion.py` + `tools/builtin/delegate/schema.py`

### 交付契约：CEO 填参面不含的字段

CEO/`replan` 可见契约**没有**：`playbook_none_reason`、`deliverable.name`、`tasks[].objective`、`must_contain`、`min_length`、`requires_files`、`must_contain_soft`。**禁止回潮**。

**写盘只认** `form=files` / `form=workspace` / 非空 `artifacts`（漏填=`files`）；目标语义并入 `task`。

**成篇硬门**：只认具名 `playbook=cite_write_review`；**否决**字数结构腿与扫 task 自由文补门。handoff 正文地板 = 非空（不暴露 CEO 字数旋钮）。

**`code_audit` 报告纪律**（与成篇硬门正交）：具名 `playbook=code_audit`；Markdown + 配套 `*.audit.json`；Phase A 结束即落 JSON 骨架与五章空壳，边查边填，禁止读完再一次性成文；章节契约区分属实缺陷 / 设计如此（模块 docstring 或设计文档已写明的目标形态，独立成栏、不进 N）/ 观察与工程债；手写路经同一继承函数盖上同字面 `required_sections`；字段闭集含验证方式 / 定案 / 严重度（未读全·待核实不得标中+）；`code_audit_gate` 结构闸；写盘通道死时缺 JSON 不硬充结构失败。→ 见代码: `runtime/runs/playbooks/audit.py` · `code_audit_gate.py`

**边界**：playbook 内部仍可留 `artifact_dir` / `strict` / `required_sections` / `output_format` / `citation_mode` / `code_audit_gate` / `web_quality_scan`；`write_scope` 不变。CEO 填参面是三档 + `artifacts`。→ 见代码: `tools/builtin/delegate/schema.py` + `runtime/runs/types.py` + `runtime/runs/contract.py`

### 交付物派活单三档

CEO 派活只声明写意图，不设计验收单。队员看不见用户原话，**禁止**靠自判决定产物地位。

**CEO / 画布可见**（仅此）：`form` = `prose`（看 / 纯文字）/ `files`（存文档，默认 `工作稿/`）/ `workspace`（改用户工程树，吞掉 `workspace_native`）；可选 `artifacts`（钉具体路径，不扫 task 自由文）。画布交付形式是三选一（纯文字 / 文档 / 改工程），默认文档。

**省略**：无对象 / 空对象 / 漏填或非法 `form` → `files`（落工作稿）。`prose` / `workspace` 必须显式。打招呼等「只看」靠 CEO 勾 `prose`。入参 `workspace_native` 且非 prose → 升 `workspace`；prose 清 native。`workspace` leftover `artifact_dir` 清掉，不拧进工作稿。

**离开 CEO 填参、固定流程内部可留**：章节验收 / JSON 形态 / `strict` / `artifact_dir` 常量 / 两阶段引用 / 审计结构闸 / `web_quality_scan` 与占位豁免。

**`write_scope` 不在本契约改。** 闲聊第一次真写再给桌子。

**否决**：派活单不声明、队员自判 + 收工对账；扫 role·task 自由文猜看/存；把质检字段再露回 CEO schema；为漏填 `prose` 去扫用户原话补档；**连 playbook 内部验收一并拆掉**（见下「砍更狠」）；把零写升硬失败。→ 见代码: `tools/builtin/delegate/schema.py` · `runtime/runs/types.py` · `runtime/runs/builder.py` · `runtime/runs/artifact_dir.py` · `runtime/runs/executor/identities.py` · 画布 `WorkflowNodeInspector.tsx`

**设计原则**（Why；跟代码对不上的才写在这里）：编排者只声明**产物形态和落点**；结构化验收写在具名流水线 / 合同代码里，不交给经理模型现场设计 QA 表。工人看不见用户原话，所以「看 / 存文档 / 改工程」必须是结构字段——这是我们和行业的唯一硬差别，不能学别人把形态留给工人自判。

**行业对照**（钉原则，不是跟风清单）：CrewAI 任务是人写的 `description` + 一段 `expected_output` 自然语言，可选 `output_file`（≈ 我们的 `artifacts`）；`output_json` / Pydantic / guardrail 是**开发者代码**，不是经理 Agent 填的验收对象。LangGraph 节点写进开发者定的 typed state。Cursor / Claude Code / Codex 任务是自然语言，真相是磁盘文件 + 测试 / 人审。OpenAI Agents 的 `output_type` 是开发者预置的响应 schema。共同点：**验收旋钮不进编排 LLM 的工具参数**。三档就是把我们的 CEO 填参面对齐到这一点；playbook 内部闸对应他们的「人写的流水线 / guardrail」。

**否决「砍更狠」**（把 `required_sections` / `code_audit_gate` / `citation_mode` / `strict` 等内部验收也拆成纯提示词）：更狠会伤 `code_audit` 五章结构闸和成篇两阶段引用时序——那些是开发者写死的流水线，不是 CEO 填参噪音。行业也不是「零验收」，而是验收不住在经理的参数表里。砍的边界停在**填参面**，不停在合同能力。

**网页质检（静态扫描跟落盘；不进 CEO schema）**：两套检查都跟「做出来的网页好不好」有关，但不是派活单的事。落盘含 HTML/CSS/JS/SVG 即跑 `web_quality_scan` 静态扫描（烂标签 / 假电话 / anti-slop），不打开浏览器。DESIGN.md / 风格 id / 散色合同仅当 deliverable 仍盖 `web_quality_scan=true`（loop/continuation 的 DESIGN 注入跟这面旗标，不跟自动扫）。`visual_critic`：真截图 + 视觉模型；**仍默认关**。无旗标不跑 DESIGN 硬闸。→ 见代码: `runtime/runs/web_quality_scan.py` · `runtime/runs/contract.py` · `website_visual_critic.py`

### 交付契约：无死读兼容位

无运行时消费的键不保留：`must_contain_soft`（只解析、全仓无读点）；`completion.plan_suggests_exec_office_deliverable` / `append_sibling` 对已删 `deliverable.name` 的 `getattr`。不删 `placeholder_hard_exempt*`（仍能少几条骨架 soft 警告）。旧 JSON 多带未知键仍能加载（未知键丢弃）。→ 见代码: `runtime/runs/types.py` · `runtime/runs/builder.py` · `runtime/delegate/completion.py` · `runtime/coordination/append_sibling.py`

### 派单填参面

CEO 可见契约**没有**：顶层 `seed_notes`、`coordinate`、`coordination`、`complexity_hint`；`tasks[].force_continue` / `checkpoint_after` / `bind_after_deps` / `result_handling` / `require_upstream`。工具描述与编排 skill **不提**已删参数名（禁负面清单）。**禁止回潮**。

**语义去哪**：全队共识只走 `team_brief`（非空升墙并按行物化开局便签；**不**把 `seed_notes` 填回 CEO 可见 schema；`run_plan.note_wall` 给看面区分「墙已升」与缺省无墙）；≥2 篇完整成稿且共享口径未进 brief、无短规格 → 先 brief 或短规格岗，墙不代替 `depends_on`（不新套餐、不扩 `consumer_deps` 猜两段）；审查线索进 brief 或审查员 `post_note`(heads_up)；零上游成功补人走 `replaces_run_id`；根 CEO **默认非阻塞**（阻塞仅嵌套 lead / 成篇套餐提纲把关 / 画布人工把关）；用户明文看提纲 → `cite_write_review` 或先派再问；下游未定 → 先跑再 `replan(add)` / 再 `delegate`；轻/标准由引擎自判（单人 prose 可 auto-light）；上游保真有文件递路径、散文默认全文；扇入默认宽松（≥1 上游成功即跑，不让经理勾 AND/OR）。

**边界**：运行时仍解析多余键；playbook / 画布仍可写 `checkpoint_after`；`RunSpec.bind_after_deps` 槽位保留。测试仍可向 `execute` 传旧键。棘轮 retired 含这些名字；skill / 常驻核搜不到填参名。→ 见代码: `tools/builtin/delegate/schema.py`

### 不扫角色名改写 deliverable

不匹配 role 名正则/子串，也不据此**静默改写** deliverable（抬 `form=files` / 塞 `reviews/` artifacts / 追加纪律文案）。

审校落盘纪律**仅当** playbook 或 deliverable **已声明** `form=files` / 非空 `artifacts`（或等价结构 flag）时施加；`cite_write_review` 等在 playbook **写死**审校员 files 契约。成篇硬门只认具名 `playbook=cite_write_review`（无字数结构腿）。**不加**新 `completion_criteria` kind（见上节）。

删猜测入口优于保留误伤面；否决「降软但仍扫角色名」。能力回退用 playbook 结构补，不靠旁路正则。名叫「审校/review」但未声明 files 的轻角色不被抬契约。→ 见代码: `runtime/runs/research_quality.py`（结构谓词）+ `runtime/runs/playbooks/research.py`（审校 files 契约）

### consumer_deps 软提示

`check_consumer_missing_depends` 命中时，软警告进入 **CEO 可见**的委派工具结果（尾巴/note）。仍**不拒收、不自动补 `depends_on`、不改图**；一次性软提示，**禁止**因「软无效」升级硬拒或累计计数。cue 保持窄（队友/上游产出指称）；误伤大则收窄正则，不扩面。漏边是真 DAG 危害；否决「只留观测日志当终态」。净负则删闸，不加硬闸。→ 见代码: `tools/builtin/delegate/tool.py`（tails）+ `runtime/delegate/consumer_deps.py`

### design_impl_same_grant 软提示

单 task / 单 grant 同时塞设计+实现（artifacts 设计类+代码类，或 task 文案「阶段 A」+「阶段 B」）且未结构拆开时，软警告进入 **CEO 可见**委派结果尾。仍**不拒收、不自动拆 tasks、不改图**；一次性软提示，**禁止**升级硬拒 / 累计计数 / 扫用户长文意图。已拆两波（`depends_on` / `checkpoint_after`）、仅设计、仅代码、轻量单文件小改不命中。混装是真波次危害；否决硬拒与自动改图。净负则删闸。→ 见代码: `tools/builtin/delegate/tool.py`（tails）+ `runtime/delegate/design_impl_slice.py`

### root_slice_honesty 软提示

根侧 `depth=0` 单节点手写写工程且无结构钉本轮切片时，软警告进入 **CEO 可见**委派结果尾——「立刻派 ≠ 立刻全量」是通用能力，非场景特例。

**命中**（可证明结构）：无具名 playbook ∧ 恰好 1 task ∧ 显式写工程（只认 `form=workspace`；`form=files` / 省略不算）∧ 无切片钉。

**切片钉白名单**（任一豁免）：非空 `artifacts` / `artifact_dir` / 非空 `required_sections` / 本 task `checkpoint_after`。

**路径**：根多节点 / 具名 playbook / deliverable 钉边界（A）与 **单 lead 嵌套扇出**（B）等价合法；软文案须明示嵌套可用。路径 B 与整锅入口同构 → **接受软提示对 B 亦响**（nudge，非拒）。路径 B 责任落 **lead**：接到成果级且无结构钉时 **先招人再整合**（captain 常驻短判决 + skill 旋钮；非硬流程、非「未嵌套禁写」），非强制 CEO 改平铺、亦非「凡大活必嵌套」。

**编排自主（提示/技能，非硬编码 playbook）**：范围大或拆缝不清时，CEO/lead 可自判 **摸底波→专班**（同批 `depends_on` 或再 `delegate`/`replan`）与路径 A/B 并列；通用于审计/摸仓/大改等，**禁止**写成「凡 X 必两拨人 / 必嵌套」。**单点展示 / 单缝审查** 1 人，探路已钉文件写进 task 当边界（禁父目录通读、禁按端/层凑工种）；全面摸底按编制自选，禁止因「整仓」默认两人（冷启动建档除外）。CEO **不知轻重时禁止猜「一人能扛整座成果」**——缝不清先短摸底再专班，缝已在文档/目录则按块派（不必先称每块有多重）；任务里写「先组队 / 你可以组队」**不算**已拆编制。交 lead 只写目标·约束·验收，禁止「你去执行整个里程碑」口吻。路径 B 下 lead：成果级无钉 → 先招再整合；**已钉薄切片却读出整仓** → `escalate kind=scope`，不默默扩编。「不要为委派而委派」只约束本来就小的活。真两段结构 OK；同 task 假两段仍禁。不扩软闸、不按读轮次催招。→ `skills`「编排自主·摸底波 / 专班 / 嵌套」· CEO `【立刻派 ≠ 立刻全量】` · captain `_WORKER_CAPTAIN_INTRO`

仍**不拒收、不改图**；不做硬拒；不扫用户/task 长文；不用 `write_scope`（非 grant 槽）。阶梯沿用 `design_impl` 先例（提示词后直接软提示）。`form=files` / 省略 / 具名 playbook / 有 artifacts 等 → 不告警。→ 见代码: `tools/builtin/delegate/tool.py`（tails）+ `runtime/delegate/root_slice_honesty.py`

### Per-worker 模型覆盖 {#per-worker-模型覆盖abc-同一功能}

每个 worker run 可绑与队友不同的大模型。省略 = 组合 Worker 槽（空则 follow 主）；CEO 在 `delegate`/`tasks[]` 可填节点显式。开工卡等**产品确认面不提供**人手改模。

**边界**：解析优先级 **节点显式 > 组合 Worker 槽 > follow 主模型**。wire 可保留 `model_overrides` 契约，**不**写产品确认面用法。续派 / 同人续跑：默认 **继承该 run 已解析模型**；本次 payload 显式改则覆盖。候选与组合槽、辩论身份对齐（统一目录 + BYOK 手填；platform allowlist）；非法配置 **硬失败**，禁 silent 回退。协作图与用量须能看见该队员用了哪个模型。CEO/`replan` `tasks[]` 目录身份（`@platform/…` / `@byok/…` 或提及）→路由键→`RunSpec.model`（`runtime/delegate/task_models.py`）；跨 provider 窄接 extras。sidecar inference proxy 认节点路由键。图 peek / 用量详情经 `run_completed.model`。凭据 / sidecar / 跨 origin → [平台 LLM 接入](/docs/05-平台与运维/平台LLM接入.md)。

能力开放（含 CEO 可选填）优先于「怕选不好就关能力」；确认面藏人改模 UI，**不**删后端节点显式 / 契约字段。**否决**角色名猜模、质量档矩阵、账号级角色→模型主设置、无可见性的暗箱路由。旧 `ModelTier` / `model_preference` 档位体系仍废，不复活。

## 冷启动探索幕

**触发**走软硬分层——**触发条件表不在本文**：闸看的全是记忆态（画像空否 / `explore_workspace_key` / 指纹），权威 → [记忆 · 探索触发与挡请求](/docs/03-AI核心/Agent记忆与知识系统.md)。本文只管**开幕之后怎么编排**。

一句话记住分层：软幕（仅空画像）不挡请求；硬挡三因（换绑 / 用户点名 / 空画像+工程信号）先探索再继续；指纹漂移不挡，走脏标记 + 旁路刷新。

**硬挡流程**：注入 `<cold_start_explore>`（换绑 / 点名刷新 / 空画像+工程信号；指纹与「仅空画像」**不**进此块）→ 先轻量探路（0～1 轮，只定位入口，禁止自己摸完整仓；同轮并行多工具只计 1 轮）→ `delegate` 组调研队（**≥2 角并行**，禁止 1 人包办整仓；不再挂开工卡）→ 收尾经 `update_folder_profile` 合并写文件夹 **画像 + 导航.md**，记录 `workspace_key` 与指纹；主题软顶 5 / 总数受 `memory_max_topic_files` → **立刻继续原请求**。pending 期间允许 `form=files`，但写盘不得出 `explore_memory` 根。**结构化例外**：本回合 `create_folder`（及裸聊自动建桌的首次铸造）得到的 folder id，点名该 id 的 worker 用 `write_scope=project`——空新桌没有要保护的已有工程，填文件即任务；当前会话出生文件夹仍走 `explore_memory`。厚背景资料（`主题/` 条目）不在本幕写。产物谁写 → [记忆 · 产物谁写](/docs/03-AI核心/Agent记忆与知识系统.md)。点名硬闸与 pending 同级。**resume 与开场同源**：空画像软降级走 `resolve_hard_explore_reason`，禁止 resume 把「仅空画像」误硬拦。

**强制 / 豁免**：点名强制开幕（合并更新）。旧画像无 key → 不因缺 key 硬开。裸聊 / 纯闲聊 / 空工作区不自动开幕、不写假画像/导航。对已有工程「继续开发 / 全面摸底」按自然缝自选人数（禁止因话题像架构 / 盘点就默认两路）；冷启动建档仍宜 ≥2 角（提示词纪律；人数硬拒已拆，组队不靠引擎逼）。引擎**不**按探路轮数剥调查工具、**不** `content_reset` 丢稿逼 delegate；组队靠提示词（CEO 自判、探路≠摸底、编制按缝；讨论默认自己做，不因话题像架构而开组）。冷启动 pending 时亦不因 delegate 节点数 <2 硬拒。成篇形状 / 修码选型 / 跑·修·打开验证终向 / 点名对比扇出靠提示词与结构验收，不靠意图分类器。成篇审计硬门只认成文专线 `playbook=cite_write_review`（无字数结构腿）；`map_fanout` / 普通多角摸底不进硬门（软闸亦同）；硬门**不**扫 task/角色自由文。审校落盘靠 playbook/结构声明，不扫角色名。审后默认向用户收口，同轮 `continue_from_run_id` 修订非默认路径。

**边界**：不新建 Explore 原语；指纹 = 顶层树 + 关键清单（不以纯天数 / commit 为唯一闸）。产物只落 `AgentCore/`（记忆；厚背景资料走 `主题/` 按需条目，且不在探索 pending 批）。**权威分层**：触发条件 / 产物谁写 / 主题上限 → [记忆 · 探索触发](/docs/03-AI核心/Agent记忆与知识系统.md)；`delegate` 组队纪律 / 收尾 → **本文**。

**否决（本定案）**：pending 时按 `form=files` 拒整批；pending 时按 delegate 节点数硬拒单 worker（现状组队靠提示词，人数硬拒已拆）；为冷启动单开 prose 版调研 playbook；delegate 入口静默改写 playbook/tasks XOR。

## 失败与否决（一行）

| 场景 / 方案 | 处理或否决理由 |
|---|---|
| `delegate` 参数非法 | 非终态回 CEO，改参重试 |
| 单 worker 失败 | 按 `on_failure`；宽松扇入默认放行，不必拖垮整 DAG |
| 无需团队 | 不调 `delegate` = 单 Agent 直答 |
| 纯路由器替换 CEO / 前置分类器 / Worker 直连 / 取消 CEO 综述 | 规划壁垒、编排税、不可观测、丧失「一个声音」 |
| 累计 N 次只读软提醒护栏 | A/B 净负已移除；靠提示词边界 + 失控硬兜底 |
| 空交 / 零声明清单整轮失败 | ✅ **已撤**（实测误伤；未声明 `file_write` 仍算有产出；空交接风暴 / 空 handoff 硬拒已拆） |
| 整合员必须 `file_write` 硬闸 | **否决**（派单点名上游路径 + 同一人 `continue_from`；不拦口头综述） |
| 默认加硬闸/软闸「优化服从度」 | **否决**；阶梯见 `.cursor/rules/intercept-discipline.mdc`（提示词→观测→一次性软提示→结构化硬拒） |
| 扩大收口姿势 A / 完成话术正则冒充近零误报 | **否决** → [执行引擎 · 可用性诚实性](/docs/03-AI核心/执行引擎架构设计.md) |
| 未派工靠禁语表 / 完成话术正则拦正文 | ✅ **已撤**（诚实性走 `team_batch` 结构面）→ 同上 |
| 无对账卡时拦同条 A∪C | ✅ **已撤**（无卡不拦正文）→ 同上 |
| 零写落盘声称扫词硬回炉（答完清气泡） | ✅ **已撤**（解释禁语误伤）→ 同上 |
| 领域 kind 扩表 / 边删 kind 边加启发式完成硬闸 | **否决**；接盘见上节 |
| `validate_criteria_kind_fit` 扫 task 拟合硬闸 | **已撤** |
| `host(action=shell)` fuse 改可批可跑（B）/ 仅改文案当终案（D） | **否决** → [安全 · 熔断方案 C](/docs/05-平台与运维/安全权限与治理.md) |
| 扫角色名静默改写 deliverable | **已删**（见上节） |
| 扫 role·task 自由文正则决定产物落 `research` 还是 `reviews` | **净删除**：意图分类器形态，且误判对用户不可见；落点只认显式来源，其余进 `工作稿/` → [工作区 §四](/docs/02-架构/双模式工作区.md#四约定文档目录约定) |
| 产物地位（成品 vs 过程材料）靠路径推断 / 派单预判 / worker 自判 | **否决**（worker 无自贬动机）；落点在派单时用显式路径 / `form=workspace` 钉死，默认 `工作稿/`（仅 `files`），打开走终稿路径 / 工作区树。收口再搬的 `promote_product` **已撤销** → [术语表 · 成品归位](/docs/01-产品/术语表.md) |
| 手写 `min_length` 字数腿 / 扫自由文补成篇硬门 | **否决**（成篇硬门只认 `cite_write_review`；见 CEO 填参面） |
| 裸 `requires_files` 第三写盘开关 | **已删**（写盘只认 `form=files` ∪ `form=workspace` ∪ `artifacts`；漏填=`files`） |
| `must_contain_soft` 空兼容位 | **已删**（见死字段清理） |
| 连 playbook 内部验收字段一并拆成纯提示词 | **否决**（砍更狠；见派活单三档） |
| 按场景加 playbook（建站 / 对比选型 / 单功能交付） | **否决**；已删 `compare_options` / `build_feature`（及更早建站本）；点名对比 / 局部功能改手写 |
| 用桌上结果当 playbook 名（调研 / 审计 / 修码） | **否决**（准入第 5 条；名形状；不设旧 id 别名） |
| 把 brief / 对比 / 多透镜合成一本再加 `kind=` | **否决**（分叉藏进一名，比分本更难路由） |
| 把网页质检再露回 CEO schema | **否决**（静态扫描跟落盘扩展名，DESIGN 合同跟内部旗标；不进填参面） |
| `consumer_deps` 软警告只打日志 | **已接通**（见上节） |
| 两篇成稿无依赖并肩靠扩 `consumer_deps` / 扫长文猜该不该两段 | **否决**；skill「两篇成稿·先口径」；墙不代替 `depends_on` → [协作模式](/docs/03-AI核心/Agent协作模式.md) |
| `design_impl_same_grant` 设计+实现同 grant | **已接通**软提示（见上节）；禁硬拒 / 自动改图 |
| `root_slice_honesty` 根单节点手写写工程无切片钉 | **已接通**软提示（见上节）；路径 B 嵌套合法且可响；禁硬拒 / 扫长文 / 用 `write_scope` |
| 载体/手段纠偏靠硬闸 / 意图分类器 / 复活 `format_options` | **否决**；定案为提示词/Skill 顾问短问（见上节 · 载体/手段顾问） |
| 账号级角色→模型矩阵 / `ModelTier{fast,strong}` 质量档 / 自动降级 / silent 回退野模型 | **否决**（与 per-run 显式覆盖正交；见上节 Per-worker） |
| 无 UI 的 CEO 暗箱选模（有字段但图/用量不可见） | **否决** |

## 开场卡 / 检查点

`ask_user` 通用澄清、成篇套餐 / 画布人工把关的波间停顿 → 全文见 [检查点与开工卡](/docs/03-AI核心/检查点与开工卡.md)，本文不复述。`team_preview` 产品位已拆。
