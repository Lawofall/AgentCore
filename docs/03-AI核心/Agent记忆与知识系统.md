---
status: landed
code: apps/server/agentcore/memory/
related:
  - docs/03-AI核心/上下文传递可视化.md
  - docs/03-AI核心/上下文工程.md
  - docs/02-架构/工作区.md
  - docs/03-AI核心/工具与能力系统.md
  - docs/01-产品/现行信息.md
  - docs/03-AI核心/编排器与CEO主Agent.md
skip_if:
  - 只改 World A/B 提示词架构或 World B 内部工具提示词（读执行引擎 §七）
---

# Agent 记忆与知识系统

> **边界**：交代怎么进回合 = **本文**；通道可视化 → [上下文传递可视化](/docs/03-AI核心/上下文传递可视化.md)；注入侧 Assembler / 按需与写侧配额 → [上下文工程](/docs/03-AI核心/上下文工程.md)；云/本地 Backend → [工作区](/docs/02-架构/工作区.md)；旧对话检索工具面 → [工具与能力 · 跨会话工具边界](/docs/03-AI核心/工具与能力系统.md#四跨会话工具边界)。
>
> → 见代码：`apps/server/agentcore/memory/`
>
> **主循环归属**：交代归「**组**」；旧对话检索归「**说**」。不当第六拍 → [主循环](/docs/01-产品/产品定位与品牌.md)。

### Cursor 从哪进

| 要改… | 去哪 | 勿当入口 |
|---|---|---|
| 交代的内容、落盘、三种带法 | `agentcore/memory/`（包 facade 已 re-export）+ 装配成员名单；本桌文件 `AgentCore/rules` | 账号库勿用文件工具按目录名改 |
| 注入段序 / `ContextAssembler` / 工作区概览 | `runtime/context/` + 回合拼装 `runtime/resolve/`（含 `prompt/memory_rules.py`） | 勿在 assembler 写规则策略或落盘 |
| Run / delegate 执行 | `runtime/runs/` → [执行引擎](/docs/03-AI核心/执行引擎架构设计.md) | **不是**交代域 |

---

## 现状

| 通道 | 现状 | 因 |
|---|---|---|
| **交代** | 每回合都带上 / 碰到某类文件 / 需要时翻开 | 一篇正文；是约束、做法还是资料由文字自己说 |
| **过往事实** | `search_conversations` / `read_conversation` | 旧场原文可查，不靠系统总结 |
| **AI 笔记** | **停写停注入** | 系统不擅自总结；存量留盘当普通稿、不进下一场 |

产品面没有「AI 记忆」活能力：不写画像 / 导航 / 主题，不开探索幕，不情景沉淀。

---

## 交代 {#用户规则}

两处载体，同一套三种带法。没有单独的「规则」类型字段。这篇是约束、做法还是资料，由正文说明。也不和「技能」分成两种存储。文件页已取消「记忆 / 规则 / 文档」三夹。

1. **账号库**（跨桌，对标 Cursor User Rules）：人在工具箱保存的一篇 Markdown。已经存好的篇留在账号库，不迁移、不藏。
2. **本桌文件**（对标 Cursor `.cursor/rules`）：工作区 `AgentCore/rules/**/*.md`。约定目录发现，frontmatter 是带法真源。不扫整棵树。读不到的路径这一回不带。

**入目录**：需要时翻开的那一档，`description` 非空才进 `<按需目录>`。账号库每次保存从正文派生；本桌文件每回合现读。空摘要不进目录，也不占名字挡住别的桌。不回退取正文首行。→ 见代码: `memory/rule_resolve.py` · `memory/workspace_files.py`

**看见范围**：两层。账号里的一篇，这场带哪些、以哪种带法，由装配成员名单决定。这个文件夹里的文档篇，只在坐这张桌时加上，带法写在这篇上，不进装配名单。没写 `apply` 的文档篇不注入。约定目录里缺 `apply` 视为按需。`role` 不是门票。上层文件夹不带。删文件夹不带走账号里的篇。

**注入**：三种，都是这一篇怎么进这一回合。

1. 每回合都带上（工具箱称常驻）：正文进 `<设定>`。
2. 碰到某类文件才带上：全文跟真实路径走。
3. 需要时翻开：目录行是名字加这句摘要，`consult` 取正文并剥掉 frontmatter。

人在这一场 `@` 某篇，正文直接带上（`<钉住条目>`）。`conditional` 否决：没有可证明的触发；碰到某类文件才是可证明的触发。

**正文**：这篇 md 是正文。frontmatter 解析失败不注入。不引 YAML。已知键里参与入目录的是 `description`。

```
---
apply: always
description: 一行摘要
paths: **/*.py
---
```

`ai_maintained` 不进 frontmatter。

**谁写**：账号库由人在工具箱保存和修改；对话 `write` 不能按目录名改那篇。本桌新篇写 `AgentCore/rules/*.md`（`write` / `edit` / 文件页）。产品能力目录、工作区路径不抄进正文 → [现行信息](/docs/01-产品/现行信息.md)。HOW → 系统 Skill `prompts`。

**纠错 UI 已撤**。手写提示词用删除。存量 `disputed_at` 见代码，不当人侧入口。**严禁**扫对话原文猜「用户是否在否认某条规则」。

### 注入 {#注入}

账号篇进不进这场、以哪种带法，由 [装配](/docs/03-AI核心/工具与能力系统.md#装配) 的成员名单决定。当前文件夹的篇叠在后面，只在这张桌。

1. 本场对话历史经 `load_recent_history` 进窗口（CEO / worker 共用）。
2. 每回合都带上的正文进 `<设定>`。碰到某类文件的全文跟路径走。需要时翻开的进 `<按需目录>`。
3. 桌面 sidecar **有 account 票**时：prepare/resume 对账号/文件夹**文档**按需目录**只读进程快照**（miss → 空文档注入、不 await 云 HTTP）；`consult` 取文档正文与目录同一份快照。快照 **300s TTL**；人保存后立刻强制重暖。**detached execution 存活期**按同一 TTL 周期续暖——只 warm 一次则 TTL 到期后文档目录会**静默**全失。空注入仍打 `account.rules_memory_cache_miss`。本桌 `AgentCore/rules` 仍从当前工作区现读，不经这份快照。**无票**仍走本地 DB。账号篇进不进这场、以哪种带法，走 `session` 快照里的成员名单（官方配方为未过滤），不经这份文档 TTL。→ 见代码：`memory/rules_injection.py` · `memory/account_prepare_cache.py` · sidecar `warmAccountRulesMemory` · `runtime/turn/snapshot.py`
4. 需要时翻开的交代与系统 Skill、低频工具合并为单一 `<按需目录>` → `consult`。`<按需目录>` 非空才渲染。
5. **跨文件夹名册**（派生）：**不进** CEO 常驻提示。当前桌只在 `<工作区>`；其它桌用 `folders`。
6. 装配顺序权威 → [执行引擎 §七](/docs/03-AI核心/执行引擎架构设计.md) / `runtime/context/`（`SectionOrder`）。

**删文件夹不带走账号里的篇（✅）**：那些篇不挂在桌子上。彻底删除清掉 `folder_id` 指着这张桌的篇。→ 见代码: `folders/permanent_delete.py`

文件页不挂交代抽屉。存量 `记忆/` · `文档/` 若在盘上当普通子项，不进目录。用户要拿走的文件在派单时写入工作区 → [工作区 §四](/docs/02-架构/工作区.md#四约定文档目录约定)。

---

## 过往事实 {#过往事实}

Worker / CEO 在「完整」开场持 `search_conversations` / `read_conversation`。出厂「标准」关掉「对话」，这两把不在开场表。**过往事实走这里**，不靠笔记。工具箱「对话」关掉同样离开开场表和执行。用户 `@` 一场旧对话仍由服务端塞对话稿，不靠这两把。工具面（query / folder_id / focus / 分页）权威 → [工具与能力 · 跨会话工具边界](/docs/03-AI核心/工具与能力系统.md#四跨会话工具边界)。

对外口径：白话三层——当前会话 / 你写的交代 / 旧场可查；不报工具名。→ 见代码：`core/search_query.py` · `conversation/log_export.py` · `tools/builtin/search_conversations.py`

---

## 否决 {#否决}

| 方案 | 理由 |
|---|---|
| AI 笔记当活产品（写 / 注入画像、导航、主题） | **停写停注入**；系统不擅自总结。存量留盘当普通稿 |
| 闲聊巩固 / 情景沉淀进 prompt / 探索幕写画像 | 一场任务渣进每回合；了解这张桌当场读文件，过往查旧对话 |
| 独立 `user_memory` 表 / 记忆总闸 / 跨用户记忆包 | 与文件树重叠或难懂；编辑/清空规则与删对话已够 |
| 没有可证明触发的 `conditional` | 每回合都带上、碰到某类文件、需要时翻开已经覆盖；再加一档「看着相关再注入」没有判据 |
| 扫整棵工作区当规矩 / 独立登记表盖过文件 frontmatter | 只发现约定目录；带法写在文件上。空 `description` 仍不进按需目录 |
| frontmatter 与 DB 列双写；解析失败猜默认值 | 两个可写副本必然分叉；失败须可见 |
| `ai_maintained` 进 frontmatter | AI 能把系统稿写成用户交代、绕开写侧闸 |
| 扫自由文猜「改规则 / 否认某条」 | `prompt-design.mdc` 点名否决的意图分类器 |
| 把产品能力目录写入交代 | 真源在产品；抄进正文会过时且挤按需目录 |
| 向量 chunk 自动灌 prompt | 与「文件随时变」不合；agentic 自取永远新鲜 |

查看/编辑：跨桌走工具箱交代；本桌走 `AgentCore/rules`。→ 见代码：`tools/PromptCatalog.tsx` · `memory/workspace_files.py`
