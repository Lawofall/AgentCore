"""工具 schema 体积棘轮——拦住悄悄回潮；新语义抬顶。

## 为什么有这道棘轮

工具 schema 是**每一轮**都重发的输入：低频工具（含 git）改按需后开场表已瘦一截，但常驻面
（delegate / ask_user …）仍坐在 prefix 前段——一改就是全量 miss。它的膨胀方式
几乎总是同一种：同一条约束在工具描述、参数描述、兄弟工具里各抄一份，或者字段删了、负面
清单还留着。每份副本单看都「只多几十字」，没人拦就月月长。

所以这里按**字符数**（不是 token 估算——``approx_tokens`` 的 chars/token 常量会随口径调整）
钉住已经去过重的那几个面：``measure_openai_tool_chars`` 量的就是真正发给模型的那份 JSON。

## 红了怎么办

- **超了上限**：先问是不是又抄了一份别处已有的话。同一条约束只留一处：
  取值语义留在参数描述，跨工具路由 / 审批策略留在工具描述，HOW 默认留在 skill / consult；
  写参当轮必见的合同可上收进该工具（现：delegate 编制、ask_user 填卡）。
  确实是**新增**的有效语义 → 把这里的数字调上去，并在 PR 里说清多出来的是什么。
- **远低于上限**（比如又砍了一批）：把数字调下来，棘轮才继续咬合。

数字 = 当次实测值向上取整到十位。新语义同一次改动里抬顶，并写明多出来的是什么；抄写回潮删副本。远低于上限才把数字调下来。改措辞时不为保住旧数字删别的句子。
"""

from __future__ import annotations

from agentcore.core.types import ToolApproval, ToolFace
from agentcore.runtime.events import EventSink
from agentcore.runtime.resolve.ceo_surface import measure_openai_tool_chars
from agentcore.tools.builtin.ask_user.tool import AskUserTool
from agentcore.tools.builtin.browser import (
    _MUTATION_VERIFY_TAIL,
    BROWSER_TOOL_CLASSES,
    BrowserTool,
)
from agentcore.tools.builtin.debate.schema import (
    DEBATE_DESCRIPTION,
    DEBATE_PARAMETERS,
)
from agentcore.tools.builtin.delegate.schema import (
    DELEGATE_DESCRIPTION,
    DELEGATE_PARAMETERS,
)
from agentcore.tools.builtin.folders import FoldersTool
from agentcore.tools.builtin.host import HostTool
from agentcore.tools.builtin.replan import _REPLAN_DESCRIPTION, _REPLAN_PARAMETERS
from agentcore.tools.builtin.run import RunTool
from agentcore.tools.protocol import ToolSchema

# 桌面 CEO 回合会同时挂上的那一份（ask_user 桌面与 web 同形：开夹不进按钮）。
# host / terminal / browser 按需进表后仍每轮重发，钉住短触发 + HOW→consult。
# 2026-08-28 debate 形态：删 schema 复述（用/不用、过闸禁令、挂号、启服手册、action 表）。
# debate 实测 1640 作短触发锚；ask_user 桌面 2588→2590、web 1892→1900。
# 2026-08-28 grant_attach_folder：本机传统附加可写根（新路由脊柱，ask_user 桌面 2655）。
# 2026-08-29 C：何时用 ask_user 从核下沉 description（挡路才问）。桌面 2668、web 1905。
# 2026-08-29 delegate 删事故反例（引擎已拒 playbook+tasks 同传）。实测 2779。
# 2026-08-29 工作流：description 加「探路够了再派」（停手 when-to-use）。实测 2798。
# 2026-08-29 装配侧：host action 去 OS 命令名/政策复述（Get-WinEvent 归 consult）；
# git 硬拒收成「禁项见失败回执」；delegate task 下沉凭据填 env（实测仍 2600，不抬）。
# host 2800→2630（实测 2622）；git 2530→2430（实测 2425）。
# 同日主审查：补回 pull/push 合同句（ff-only / 恒确认），非新语义抬顶。git 2430→2490。
# 2026-08-30 delegate：brief 改为有共享口径才写；task 不再把开局口径赶进 brief。实测 2547。
# 2026-08-30 debate：产品入口只认正反，schema 不再广告三种形态。实测 1584。
# 2026-09-01 debate：form.enum 广告子集只留 debate；description 不再抄入口分流。实测 1380。
# 2026-08-30 delegate.task 收未装配 ≠ 切口（从核搬家）。当次实测 2457。cap 降到 2460。
# 2026-08-30 派前可见打算从 CEO 核搬进 description。当次实测 2469。cap 2470。
# 2026-08-30 档 1：description 补成篇/可运行应用 + 有写权≠超规模自己做完。
# 当次实测 2510。cap 2510（抬顶=when-to-use 补漏，非回潮）。
# 2026-08-31 ask_user：云桌不再广告 attach_rw，本机不广告 open/bind；实测 2425。
# 2026-08-31 delegate：when-to-use 改默认用、探路停手写进 description。
# 当次实测 2537。cap 2540（抬顶=极性与停手，非回潮）。
# 2026-09-01 schema 同层去重（手册出按钮）：browser 1329、host 2567、run 1004、
# delegate 2409、ask_user 桌面 2235 / web 1686；git 政策表仍只在 description（2430）。
# 2026-09-01 已确认约束填法收进 task 参数、deliverable 不再复述。实测 delegate 2380。
# 2026-09-08 删 deliverable.form 三档：schema 只留 artifacts。实测 delegate 2153。cap 2340→2160。
# 2026-09-02 run：when-to-use 补进 description（验证直接跑 / dev 后台 / action 管已有进程）。
# 省略 wait_for 则起来就返回（不再注入默认就绪信号）。cap 1030。
# 2026-09-06 ask_user：撤 grant_* 模型面广告（区外改走 file_* 运行时授权）。桌面云 1565、web 1400。
# 2026-09-06 delegate.task：自包含 ≠ 逐步改法/改哪些文件/章节骨架（根可见面补对比边界）。
# 实测 2305。cap 2300→2310（抬顶=新语义，非回潮）。
# 2026-09-06 delegate.task：点名路径用工作区相对 POSIX（与工具 path 同形）。
# 实测 2335。cap 2310→2340（抬顶=新语义，非回潮）。
# 2026-09-08 ask_user HOW 同时指 ask_kickoff + ask_midtask（废名 asking_the_user 拆本）。
# 桌面实测 1582。cap 1570→1590。web 实测 1417。cap 1400→1420。
# 抬顶=HOW 指针多一本，不是把提问百科抄回按钮。
# 2026-09-08 撤 desks skill：换桌对照下沉 target_folder_id（实测 delegate 2337，仍 ≤2340）；
# list_folders / resolve_folder / create_folder 去掉 HOW→consult(desks) 指针。
# 实测 207 / 335 / 474。cap 240→210、370→340、510→480。
# 2026-09-10 delegate：读侧极性「不知读哪 ≠ 自己连搜」（与「有写权 ≠」对偶）。
# 实测 2168。cap 2160→2170（抬顶=when-to-use 补漏，非回潮）。
# 2026-09-10 delegate.task：已确认约束不装改法/现状；点名入口或成品路径
# （收掉「≠改哪些文件」，避免和点名打架）。换字不抬顶。
# 2026-09-10 delegate.depends_on：空=同波并行 + 本字段 ≠ task 里写先后
# （对比边界，切开「把流水线写进 task」替身）。实测 2196。cap 2170→2200。
# 2026-09-10 波 2：参数描述只留取值。delegate.task / escalate / ask_user 卡片去
# 补集与判例；git 参数不再复述政策/闸。实测 delegate 2022、ask_user 桌面 1406 /
# web 1238、git 2410。cap 2200→2030、1590→1410、1420→1240、2430→2410。
# 2026-09-10 工具面对照行业：废名/补集/自指 HOW/审批轴/判例出按钮。
# 实测 browser 1274、git 2371、host 2435、str_replace 552。
# cap 1330→1280、2410→2380、2570→2440、610→560。
# 2026-09-10 文案五条：git 政策出按钮（回执纠偏）；delegate.task 只留自包含对比，
# 填法 HOW 回 staffing / lead_subteam。实测 git 2220、delegate 1938。
# cap 2380→2220、2030→1940。
# 2026-09-10 兼容续：Swiss-army 不去 oneOf（Go 网关拒 root oneOf）；host/browser
# 参数 HOW 进 consult。实测 browser 1077、host 2234。cap 1280→1080、2440→2240。
# 2026-09-10 双写尾巴：session_id / Audiosrv / git 政策 / run 例句 / 填卡 HOW
# 出按钮。实测 browser 1036、host 2209、git 2189、run 929、ask_user 1380。
# cap 1080→1040、2240→2210、2220→2190、1030→930、1410→1380。
# 2026-09-14 瑞士军刀参数：enum / min / max / default 出 description。
# 实测 browser 983、host 2045。cap 1040→990、2210→2050。
# 2026-09-14 delegate：when-to-use 从场面表收成信息极性（切开 / 不该进会话窗）。
# 实测 1968。cap 1940→1970（抬顶=when-to-use 换原语，非回潮抄写）。
# 2026-09-15 delegate：根/嵌套共用 DELEGATE_WHEN + 窗绑定分叉。换字未抬顶。
# 2026-09-16 update_folder_profile 退出 CEO 开场表 / 能力图鉴，棘轮不再钉它。
# 2026-09-15 ask_user：一次一张卡进 description（可先检索再问；两张卡仍拒）。
# 实测桌面 1407、web 1239。cap 1420→1410、1250→1240。
# 2026-09-16 ask_user：一次一张卡 / 可先检索再问出按钮进 consult；description 收回 debate 短触发。
# 实测桌面 1373、web 1205。cap 1410→1380、1240→1210。
# 2026-09-16 开场收口：delegate 场面表出按钮进 staffing；resolve_folder 匹配序出按钮。
# 实测 delegate 1929、resolve_folder 274。cap 1970→1930、340→280。
# 2026-09-18 delegate：when-to-use 从口号换成信息判据四问（路由尺出核归按钮）。
# 实测 1994。cap 1930→2000（抬顶=新语义，非回潮抄写）。
# 2026-09-18 delegate：编制 HOW 从 staffing / lead_subteam 上收到按钮（写参当轮必见）。
# 实测 2236。cap 2000→2240（抬顶=新语义，非回潮抄写）。
# 2026-09-18 delegate.task：开局窗事实换掉「看不到完整历史」真空自包含。
# 实测 2254。cap 2240→2260（抬顶=新语义，非回潮抄写）。
# 2026-09-16 git：enum / default / min / max 出 description（同 09-14 host/browser）。
# 实测 2129。cap 2190→2130。
# 2026-09-16 删 run/browser.purpose（审批旁白，执行忽略；标题由 command/action 派生）。
# 实测 browser 903、run 846。cap 990→910、930→850。
# 2026-09-16 删 debate.form / sides.is_subject、host.service、run.name
# （无选择常量或显示名；入口写死正反 / Audiosrv；进程列表用 command）。
# 实测 debate 1242、host 1953、run 782。cap 1380→1250、2050→1960、850→790。
# 2026-09-16 删 ask_user.options.detail、host.device_id
# （整理第二行从 op 派生；切音箱只填友好名）。
# 实测 host 1861、ask_user 桌面 1295 / web 1127。cap 1960→1870、1380→1300、1210→1130。
# 2026-09-16 删 ask_user.message（每张卡至少一道 question；卡头用 prompt）。
# 实测 ask_user 桌面 1251 / web 1083。cap 1300→1260、1130→1090。
# 2026-09-16 冻 host os_log max_entries / max_bytes（执行硬默认 40/24k；桌面 80/48k 仍为病理阀）。
# 实测 host 1628。cap 1870→1630。
# 2026-09-18 git 收内环 PR 面（11 子命令）并改按需：砍 show/blame/branch/stash/merge
# /rebase/cherry-pick/tag/remote/init_baseline 与 action/object/ref。实测 1590。cap 2130→1590。
# 2026-09-18 questions[] 卡形与 escalate 共用；label 取值语义去「用户选」。
# 实测 ask_user 桌面 1244 / web 1076。cap 1260→1250、1090→1080。
# 2026-09-18 删 debate.thorough（点名开辩即认真档；轻量挑刺/多视角走 delegate）。
# 实测 debate 1166。cap 1250→1170。
# 2026-09-19 ask_user：填卡合同从 consult skill 上收到按钮（写参当轮必见）。
# 实测桌面 1286 / web 1118。cap 1250→1290、1080→1120（抬顶=新语义，非回潮抄写）。
# 2026-09-19 倾向只留 label「（推荐）」；模型面删 questions[].default。
# 实测桌面 1185 / web 1017。cap 1290→1190、1120→1020。
# 2026-09-19 删 ask_user.browser_login。实测桌面 1104。cap 1190→1110。
# 2026-09-19 ask_user when 收成挡路+标假设+已钉/未钉；choice 下沉 label。
# 实测桌面 1065。cap 1110→1070。
# 2026-09-19 ask_user when/填卡再收：挡路口号并进猜错；已钉≠自拟；prompt/label 对比句。
# 实测桌面 1046。cap 1070→1050。
# 2026-09-20 delegate：编制 HOW 切开切面仍是一块 / 点名对比才按人数加（合同进按钮）。
# 实测 2279。cap 2260→2280（抬顶=新语义，非回潮抄写）。
# 2026-09-20 删「只报告默认 1 人」编制句（与人数不是优化目标冲突；不催写已在 artifacts 省略）。
# 实测 2262。cap 2280→2270。
# 2026-09-20 编制句从「切面=1 人 / 才按人数加」改成切面 ≠ 与对比下限（非回潮抄写）。
# 实测 2273。cap 2270→2280。
# 2026-09-20 delegate：开场复述与「一块」例子出按钮（参数已有骨架；原则留下）。
# 实测 2207。cap 2280→2210。
# 2026-09-20 delegate.task：砍骨架复述/行格式/未装配；验收并一句。
# 实测 2127。cap 2210→2130。
# 2026-09-20 delegate.depends_on：跨回合复述出按钮（append_to_execution_id 已有）。
# 实测 2108。cap 2130→2110。
# 2026-09-20 卸具名 playbook / playbook_args。实测 1831。cap 2110→1840。
# 2026-09-20 delegate：删「默认用本工具」（与四问「否→自己做」对打；路由只在四问）。
# 实测 1824。cap 1840→1830。
# 2026-09-20 delegate：编制/WHEN 出按钮事故补丁（审查者≠作者、平铺同名、整表再交、
# WHEN 复述人数）。合同仍是一块 / 切面≠ / 对比 N；续派在参数与闸。实测 1737。cap 1830→1740。
# 2026-09-20 debate.moderator_model：填法只留 sides[].model。
# 实测 1137。cap 1170→1140。
# 2026-09-20 debate：stance 字数出 prose；model 例子出按钮；background HOW 进 skill。
# 实测 1048。cap 1140→1050。
# 2026-09-20 ask_user WHEN 收到短触发（挡路/标假设/已钉≠自拟/载体缺口出按钮）。
# 实测桌面 978。cap 1050→980。
# 2026-09-20 git：探路 glob/grep 出按钮。实测 1575。cap 1590→1580。
# 2026-09-20 host.timeout_seconds：数字出 prose（执行层仍夹紧）。实测 1607。cap 1630→1610。
# 2026-09-20 host：source/level/manager 例子与 OS 对照出按钮。实测 1512。cap 1610→1520。
# 2026-09-20 folders/create_folder：file_list 互指、open_local_project 补集、parent 例子出按钮。
# 实测 folders 395 / create_folder 364。cap 410→400、470→370。
# 2026-09-20 folders.path：路径例子出按钮。实测 385。cap 400→390。
# 2026-09-20 folders.path：口述/精确/子串出按钮。实测 369。cap 390→370。
# 2026-09-20 冻填参旋钮：git oneline/remote/max_count；host facets/timeout_seconds；
# glob max_entries；browser dy。执行写死现行默认，不加 leftover 解析。
# 实测 browser 822、git 1305、host 1249、glob 329。
# cap 910→830、1580→1310、1520→1250；glob 450→330。
# 2026-09-20 冻填参二批：run wait_timeout_seconds；host minutes；git create_pr.head；
# include_untracked 极性改 true；set_upstream 恒 -u。执行写死，不加 leftover 解析。
# 实测 git 1070、host 1140、run 699。cap 1310→1070、1250→1140、790→700。
# 2026-09-20 run：background/action HOW 出按钮（consult + 参数已有）；
# browser：实现地理出按钮。实测 run 634 / browser 792。cap 700→640、830→800。
# 2026-09-20 同名 HOW→consult 出按钮（目录+consult 已有）。实测 run 617 / host 1122 / browser 771。
# cap 640→620、1140→1130、800→780。
# 2026-09-20 debate：产出形状/非终结出按钮（手册已有）。实测 1023。cap 1050→1030。
# 2026-09-20 delegate：默认主路/验收菜谱/续派例子出按钮；debate.cross_model 收到填参。
# 实测 delegate 1678 / debate 992。cap 1740→1680、1030→1000。
# 2026-09-20 删 debate_and_review Skill：入口合同只留按钮短句。实测 debate 963。cap 1000→970。
# 2026-09-20 ask_user 不再广告开夹 action（Composer / 交付卡芯片）。实测桌面=web 801。
# cap 970→810；web 仍 810。
# 2026-09-20 browser：url 实现黑话 / ref 例子 / snapshot HOW 出按钮。实测 737。cap 780→740。
# 2026-09-20 ask_user.kind：enum 复述出按钮（默认仍写）。实测 787。cap 810→790。
# 2026-09-21 填参面去铬条：deliverable 压成 artifacts；ask_user 不广告 kind。
# 实测 delegate 1577 / ask_user 690。cap 1680→1580、790→690。
# 2026-09-21 sides[].key 可选。实测 949。cap 970→950。
# 2026-09-21 可抄骨架出按钮；必填/省略复述出取值说明。实测 delegate 1476。cap 1580→1480。
# 2026-09-21 「可选」前缀出取值说明。实测 delegate 1470。cap 1480→1470。
# 2026-09-21 删 append_to_execution_id（跨回合链改由续派/热图机械写 prev）。
# 实测 delegate 1385。cap 1470→1390。
# 2026-09-21 删 host 按需手册；description 收成「这台电脑」。实测 1093。cap 1130→1100。
# 2026-09-21 删 browser 按需手册；description 收成「真实浏览器」。实测 710。cap 740→710。
# 2026-09-22 模型面不再有 git 工具；host 只留 command。实测 214。cap 1100→220。
# 2026-09-23 delegate：根首句改为交回后由你收尾；未读前可先买一次廉价信号再判；
# 收益/成本写成可计算式；task 只写工人拿不到的三样。删「不知读哪 ≠ 自己连搜」。
# 实测 1483。cap 1390→1490（抬顶=决策前移到廉价信号，非回潮抄写）。
# 2026-09-23 delegate：切面 ≠ 独立块，也 ≠ 按人拆。实测 1489。cap 不抬。
# 2026-09-23 delegate：删「有写权 ≠ 自己做完」；编制从「至少 N 人」收成
# 拆开仍成立的对照各是一块。实测 1469。cap 1490→1470。
# 2026-09-23 delegate：收益改「或」；第 1 问改为结论独立成立；「一块」改为不可再切。
# 实测 1466。cap 不降（向上取整到十位仍 1470）。
# 2026-09-23 delegate：同一写面 ≠ 拆开仍成立（只读取证须结论独立才并行；
# 多模块/多文件夹且结论仍独立仍各是一件）。实测 1526。cap 1470→1530
# （抬顶=编制合同新语义，非回潮抄写）。
# 2026-09-23 delegate：编制「1 人只在整件事不可再切」并进
# 「拆开仍成立的对象各是一件，否则整件事 1 人」。实测 1521。
# cap 不降（向上取整到十位仍 1530）。
_CAPS: dict[str, int] = {
    "browser": 710,
    "host": 220,
    "run": 620,
    "delegate": 1530,
    "debate": 950,
    "ask_user": 690,
    "folders": 370,
}
_TOTAL_CAP = sum(_CAPS.values())

# 非桌面（web）态 ask_user：开夹 action 已出按钮，与桌面同形。
# 2026-09-10 填卡 HOW 出按钮。实测 1212。cap 1240→1220。
# 2026-09-16 删 options.detail。实测 1127。cap 1210→1130。
# 2026-09-16 删 message。实测 1083。cap 1130→1090。
# 2026-09-18 卡形共用。实测 1076。cap 1090→1080。
# 2026-09-19 填卡合同上收。实测 1118。cap 1080→1120。
# 2026-09-19 删 questions[].default。实测 1017。cap 1120→1020。
# 2026-09-19 删 browser_login。实测 936。cap 1020→940。
# 2026-09-19 ask_user when 收短。实测 897。cap 940→900。
# 2026-09-19 ask_user when/填卡再收。实测 878。cap 900→880。
# 2026-09-20 WHEN 收短触发。实测 810。cap 880→810。
# 2026-09-20 ask_user 多选默认括号出按钮。实测桌面 969。cap 980→970。
# 2026-09-20 ask_user.kind：enum 复述出按钮。实测 787。cap 810→790。
# 2026-09-21 不广告 kind。实测 690。cap 790→690。
_ASK_USER_WEB_CAP = 690

# Worker-only：escalate / handoff / 写盘三件套曾把身份段或 consult HOW 再抄一遍到按钮上。
# 2026-08-29 escalate blocking：已拒凭据→false 短触发（身份段不进按钮）。当次实测 1698。cap 1690→1700。
# 2026-09-02 便条改收尾轮正文、参数表清空。实测 192。
# 2026-09-02 handoff WHEN 收成一句（有下游必须 / 无下游默认不交）。
# 2026-09-02 便条形状（结论 + 2–4 要点）从空 schema 字段 HOW 挪到 description。
# 实测 253。cap 200→260（抬顶=字段 HOW 无落点，不是别处再抄）。
# 2026-09-02 形状改为「现在什么已成立 / 便条 ≠ 文件说明」，去掉 2–4 条配额。实测 247。cap 260→250。
# 2026-09-01 写盘三件套 / escalate description 去重。实测 write 498 / append 413 /
# str_replace 632 / escalate 1508。
# 2026-09-10 波 2：escalate 卡片去补集；实测 1387。cap 1510→1390。
# 2026-09-16 escalate 填卡 HOW 出按钮（权衡/推荐归 ask_kickoff）。实测 1350。cap 1390→1350。
# 2026-09-18 questions[] 与 ask_user 共用卡形。实测 1349。cap 保持 1350。
# 2026-09-19 删 questions[].default。实测 1273。cap 1350→1280。
# 2026-09-19 删 escalate.browser_login。实测 1152。cap 1280→1160。
# 2026-09-20 escalate：例子与 questions 填法出按钮。实测 1102。cap 1160→1110。
# 2026-09-20 escalate.blocking：默认括号出按钮。实测 1090。cap 1110→1090。
# 2026-09-20 escalate：blocking HOW 出按钮（参数已有）。实测 1063。cap 1090→1070。
# 2026-09-20 escalate：reason 三选一替换 blocking/kind。实测 1040。cap 1070→1040。
# 2026-09-20 escalate.kind：enum 复述出按钮（与 ask_user 共用卡形）。实测 1026。cap 1040→1030。
# 2026-09-20 str_replace：质量菜谱 / 空串复述 / 默认括号 / 唯一性 HOW 出按钮。
# 实测 503。cap 560→510。
# 2026-09-08 撤 long_form_landing：写工具 description 去掉 HOW→consult。实测 write 334 /
# str_replace 601。cap write 500→340、str_replace 640→610。
# 2026-09-01 常驻文件面：回收站/扁平化手册出按钮，恢复路径留回执。实测
# delete 353 / read 766 / grep 938 / move 328 / copy 375 / glob 684 /
# list 404 / mkdir 223。
# 2026-09-01 mkdir：when-to-use 从 CEO-only skill 下沉到工具 description
#（结构目录 vs 套应用名当工程根）。实测 321。cap 230→330（抬顶=漏层补 when-to-use）。
# 2026-09-01 code_search：索引手册出按钮。实测 search 626。
# 2026-09-01 协调套件：解析失败候选 / 空 wait 审批手册出按钮。实测
# wait 271 / cancel_worker 337 / resolve_escalation 480 /
# queue_user_message 339。
# 2026-09-02 wait：开口闭集补插话，非回潮。实测 305。cap 280→310。
# 2026-09-15 replan：字段 HOW 出按钮（让出简报/参数已有）；空 consult 指针删。
# 实测 1954。cap 1960。
# 2026-09-16 撤空位晚绑定：replan 去掉 binds。实测 1286。cap 1960→1290。
# 2026-09-20 wait/cancel_worker/replan：协调互指出按钮。
# 实测 wait 269 / cancel_worker 305 / replan 1262。cap 310→270、340→310、1290→1270。
# 2026-09-20 resolve_escalation：补集与 via_user 出按钮；replan：让出黑话/非终结出按钮。
# 实测 resolve 449 / replan 1246。cap 480→450、1270→1250。
# 2026-09-20 resolve.via_user 分类器出按钮；replan.add.task 自包含出按钮。
# 实测 resolve 413 / replan 1241。cap 450→420；replan 仍 1250。
# 2026-09-21 wait/cancel reason 出填参面；replan.add 压成 artifacts。
# 实测 wait 200 / cancel 240 / replan 1156。cap 270→200、310→240、1250→1160。
# 2026-09-21 artifacts 取值说明收短。实测 replan 1144。cap 1160→1150。
# 2026-09-21 「可选」前缀出取值说明。实测 replan 1122。cap 1150→1130。
# 2026-09-26 删 queue_user_message：协调插话留在本回合，不再改排。
# 2026-09-26 删 resolve_escalation：对停着的人说话并进 replan.tell。
# 实测 replan 1196。cap 1130→1196（抬顶 = tell 何时用、以及偏好先 ask_user）。
_COORD_CAPS: dict[str, int] = {
    "cancel_worker": 240,
    "replan": 1196,
}
# 2026-09-20 write：DSH file_path 硬切（path→file_path）。实测 311。cap 310→320。
# 2026-09-20 edit.replace_all：补集出按钮。实测 497。cap 510→500。
# 2026-09-21 escalate questions 不广告 kind。实测 929。cap 1030→930。
# 2026-09-21 「可选」前缀出取值说明。实测 escalate 920。cap 930→920。
_WORKER_CAPS: dict[str, int] = {
    "escalate": 920,
    "handoff": 250,
    "write": 320,
    "edit": 500,
}
# 2026-09-06 区外路径改走 file_* 本机路径（运行时挂载）：when-to-use 进 description。
# 2026-09-06 已挂 external/ 写升档：file_copy dest 补已挂路径。实测 file_copy 431。
# 实测 file_read 854 / grep 980 / glob 729 / file_list 472。
# 2026-09-10 开场去重：挂载 HOW 只留 consult(local_desk)；path 补集/判例出按钮。
# 实测 file_read 727 / grep 894 / glob 597 / file_list 292 / code_search 550。
# 2026-09-14 glob/file_read：教程与 PDF 翻页 HOW 出按钮（上限进 schema / 回执）。
# 实测 glob 521 / file_read 689。cap 600→530、730→690。
# 2026-09-15 grep：正则脚枪出按钮留回执；mkdir 例子与补集出按钮。
# 实测 grep 801 / mkdir 238。cap 900→810、330→240。
# 2026-09-16 文件教程出按钮：offset 开窗 / grep glob 前缀 / glob 例与 path 别名 /
# file_write 扁平。实测 file_read 671 / grep 763 / glob 459 / file_write 303。
# cap 690→680、810→770、530→460、340→310。
# 2026-09-20 file_read：目录/定位互指出按钮。实测 646。cap 680→650。
# 2026-09-20 file_read.offset：省略从第 1 行出按钮。实测 636。cap 650→640。
# 2026-09-16 grep：max_results 出按钮，执行冻默认 50。实测 665。cap 770→670。
# 2026-09-20 grep/glob/file_list：检索互指出按钮。实测 grep 634 / glob 443 / file_list 303。
# cap 670→640、460→450、320→310。
# 2026-09-20 grep：目录行方言、glob 例子、bool 默认括号出按钮。实测 583。cap 640→590。
# 2026-09-20 mkdir：补集禁止句出按钮。实测 214。cap 240→220。
# 2026-09-18 file_write：用户规则写 AgentCore/规则/*.md 进 description。实测 327。cap 310→330。
# 2026-09-20 file_write：创建/覆盖出说明（名字与 str_replace 切开）。实测 306。cap 330→310。
# 2026-09-19 用户规则条目地址改 `.agentcore/规则`：file_list / file_delete when-to-use。
# 实测 file_list 314 / file_delete 380。cap 300→320、360→380。
# 2026-09-19 file_batch 吸收 file_move / file_copy（单条也走 operations 一项；
# dest 已存在则跳过）。实测 1077。cap 占位 900→1080（抬顶=并入两把笔，非回潮抄写）。
# 2026-09-20 file_batch：单条互指出按钮。实测 776。cap 1080→780。
# 2026-09-20 file_delete：目录行默认可逆出按钮（参数已有）。实测 371。cap 保持 380。
# 2026-09-20 file_delete / file_batch：说明收到「这是什么」+ 用户规则路径；
# 上限/部分失败留参数与回执。实测 file_delete 337 / file_batch 753。cap 380→340、780→760。
# 2026-09-20 冻 glob.max_entries（执行硬默认 50）。实测 329。cap 450→330。
# 2026-09-20 file_read 说明收到「这是什么」；file_list 去探索分类器；
# glob 硬拒进 pattern（一层列举切开）。实测 file_read 561 / file_list 290 / glob 357。
# cap 640→570、310→290；glob 330→360（抬顶=填参合同，非回潮抄写）。
# 2026-09-20 read.file_path：绝对路径教法出按钮；grep.files_only 补集出按钮。
# 实测 read 543 / grep 567。cap 570→550、590→570。
# 2026-09-20 同层去重：glob path 默认/nameless 硬拒出按钮（回执仍有）；
# file_list / grep JSON default 不再中文复述；file_delete 可逆默认出按钮；
# file_batch op 枚举不再复述。实测 glob 314 / file_list 274 / grep 561 /
# file_delete 324 / file_batch 728。cap 360→320、290→280、570 仍 570、
# 340→330、760→730。
# 2026-09-21 用户规则目录英文化 `.agentcore/规则` → `.agentcore/rules`：
# write / file_list / file_delete description +3（换字，cap 未动）。
# 2026-09-21 file_delete / file_batch 不广告 permanent。实测 244 / 631。cap 330→250、730→640。
# 2026-09-21 grep.glob 「可选」前缀出按钮。实测 558。cap 570→560。
_FILE_CAPS: dict[str, int] = {
    "file_delete": 250,
    "read": 550,
    "grep": 560,
    "file_batch": 640,
    "glob": 320,
    "file_list": 280,
}
# 2026-09-09 query 定位（唯一命中打开 / 多场列出）。
# 实测 search_conversations 857 / read_conversation 825。
# 2026-09-10 读对话：输出宪法出按钮（基座已有）。实测 read 801。cap 830→810。
# 2026-09-10 分页协议出按钮（truncated / next_cursor 只在回执与 cursor 参数）。
# 实测 read 730。cap 810→730。
# 2026-09-15 search_conversations：手册出按钮，when-to-use 一句 + 相邻 ≠。
# 实测 757。cap 860→760。
# 2026-09-16 对话日志：default / min / max 进 schema，中文不再复述；跨工具指针只留 search。
# 实测 search 744 / read 625。cap 760→750、730→630。
# 2026-09-16 日志旋钮出按钮：hours / archived / global_chats / max_chars。
# 实测 search 534 / read 534。cap 750→540、630→540。
# 2026-09-16 search 对齐 Cursor：query 必填 + AND/引号教法。实测 582。cap 540→590。
# 2026-09-20 search_conversations：打开互指与 1–2 词 HOW 出按钮（回执仍有）。实测 540。cap 590→540。
# 2026-09-20 search_conversations：limit 出按钮，执行冻 SEARCH_DEFAULT_LIMIT。实测 441。cap 540→450。
# 2026-09-20 search_conversations：「过往事实」同义复述出按钮。实测 435。cap 450→440。
# 2026-09-21 scope 收进 folder_id。实测 339。cap 440→340。
_LOG_CAPS: dict[str, int] = {
    "search_conversations": 340,
    "read_conversation": 540,
}
# 2026-09-20 常驻联网检索补棘轮（按钮未改字；防回潮抄写）。实测 289。cap 290。
# 2026-09-20 web_search：核对原文互指出按钮。实测 273。cap 290→280。
# 2026-09-20 web_search：该不该搜出按钮（模型自判）；说明只留回执形状。实测 254。cap 280→260。
# 2026-09-20 web_fetch：工作区互指出按钮（回执仍改道 read）。实测 223。cap 230。
_WEB_CAPS: dict[str, int] = {
    "web_search": 260,
    "web_fetch": 230,
}


def _delegate_schema() -> ToolSchema:
    """DelegateTool.schema 的等价体（真工具要一整套协作依赖才构得出来）。"""
    return ToolSchema(
        name="delegate",
        description=DELEGATE_DESCRIPTION,
        parameters=DELEGATE_PARAMETERS,
        face=ToolFace.ORCHESTRATION,
        approval=ToolApproval.NEVER,
    )


def _debate_schema() -> ToolSchema:
    """DebateTool.schema 的等价体（真工具要 LLM / sink / registry 才构得出来）。"""
    return ToolSchema(
        name="debate",
        description=DEBATE_DESCRIPTION,
        parameters=DEBATE_PARAMETERS,
        face=ToolFace.ORCHESTRATION,
        approval=ToolApproval.NEVER,
    )


def _replan_schema() -> ToolSchema:
    return ToolSchema(
        name="replan",
        description=_REPLAN_DESCRIPTION,
        parameters=_REPLAN_PARAMETERS,
        face=ToolFace.ORCHESTRATION,
        approval=ToolApproval.NEVER,
    )


def _ask_user_schema(*, desktop: bool) -> ToolSchema:
    return AskUserTool(
        sink=EventSink(),
        conversation_id="c1",
        timeout_seconds=30.0,
        advertise_bind_local_folder=desktop,
    ).schema


def _measured() -> dict[str, int]:
    sizes = {
        cls().schema.name: measure_openai_tool_chars(cls().schema)
        for cls in BROWSER_TOOL_CLASSES
    }
    sizes["host"] = measure_openai_tool_chars(HostTool().schema)
    sizes["run"] = measure_openai_tool_chars(RunTool().schema)
    sizes["delegate"] = measure_openai_tool_chars(_delegate_schema())
    sizes["debate"] = measure_openai_tool_chars(_debate_schema())
    sizes["ask_user"] = measure_openai_tool_chars(_ask_user_schema(desktop=True))
    sizes["folders"] = measure_openai_tool_chars(FoldersTool().schema)
    return sizes


def _measured_worker() -> dict[str, int]:
    from agentcore.tools.builtin.escalate import EscalateTool
    from agentcore.tools.builtin.file_ops.mutate import (
        FileWriteTool,
        StrReplaceTool,
    )
    from agentcore.tools.builtin.handoff import HandoffTool

    return {
        "escalate": measure_openai_tool_chars(EscalateTool().schema),
        "handoff": measure_openai_tool_chars(HandoffTool().schema),
        "write": measure_openai_tool_chars(FileWriteTool().schema),
        "edit": measure_openai_tool_chars(StrReplaceTool().schema),
    }


def _measured_coord() -> dict[str, int]:
    from agentcore.runtime.coordination.tools import CancelWorkerTool

    return {
        "cancel_worker": measure_openai_tool_chars(CancelWorkerTool().schema),
        "replan": measure_openai_tool_chars(_replan_schema()),
    }


def _measured_file() -> dict[str, int]:
    from agentcore.tools.builtin.file_ops import (
        FileBatchTool,
        FileDeleteTool,
        FileListTool,
        FileReadTool,
        GlobTool,
    )
    from agentcore.tools.builtin.grep import GrepTool

    return {
        "file_delete": measure_openai_tool_chars(FileDeleteTool().schema),
        "read": measure_openai_tool_chars(FileReadTool().schema),
        "grep": measure_openai_tool_chars(GrepTool().schema),
        "file_batch": measure_openai_tool_chars(FileBatchTool().schema),
        "glob": measure_openai_tool_chars(GlobTool().schema),
        "file_list": measure_openai_tool_chars(FileListTool().schema),
    }


def test_per_tool_schema_chars_within_cap():
    sizes = _measured()
    assert set(sizes) == set(_CAPS), f"棘轮覆盖面漂了：{sorted(set(sizes) ^ set(_CAPS))}"
    over = {
        name: (chars, _CAPS[name]) for name, chars in sizes.items() if chars > _CAPS[name]
    }
    assert not over, f"工具 schema 变胖（实测, 上限）：{over}"


def test_total_ceo_tool_schema_chars_within_cap():
    total = sum(_measured().values())
    assert total <= _TOTAL_CAP, f"这批工具合计 {total} 字符 > 上限 {_TOTAL_CAP}"


def test_ask_user_web_surface_matches_desktop():
    """开夹不进按钮后 web / 桌面同形。"""
    web = measure_openai_tool_chars(_ask_user_schema(desktop=False))
    desktop = measure_openai_tool_chars(_ask_user_schema(desktop=True))
    assert web <= _ASK_USER_WEB_CAP, f"web 态 ask_user 变胖：{web}"
    assert web == desktop


def test_delegate_top_level_parameter_keys():
    """delegate 顶层参数钉现行集合（键从 schema.py 读出，不预埋废字段）。"""
    assert set(DELEGATE_PARAMETERS["properties"]) == {
        "tasks",
        "team_brief",
    }


def test_shared_mutation_tail_does_not_repeat_per_tool_receipts():
    """共用尾巴不点名 typed / clicked；回执字段在结果里，不进 action 参数。"""
    assert "typed.matched" not in _MUTATION_VERIFY_TAIL
    assert "clicked.was_disabled" not in _MUTATION_VERIFY_TAIL
    action_desc = BrowserTool().schema.parameters["properties"]["action"]["description"]
    assert "typed.matched" not in action_desc
    assert "clicked.was_disabled" not in action_desc


_GO_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {"oneOf", "anyOf", "allOf", "$ref", "$defs", "if", "then", "else"}
)


def _schema_keys(node: object) -> list[str]:
    keys: list[str] = []
    if isinstance(node, dict):
        keys.extend(node.keys())
        for value in node.values():
            keys.extend(_schema_keys(value))
    elif isinstance(node, list):
        for value in node:
            keys.extend(_schema_keys(value))
    return keys


def test_swiss_army_schemas_stay_flat_for_go_gateway():
    """host / browser 保持单名 + 扁平 object；禁止 oneOf 过 Go 网关。"""
    for schema in (HostTool().schema, BrowserTool().schema):
        params = schema.parameters
        assert params.get("type") == "object"
        assert "properties" in params
        hits = _GO_UNSUPPORTED_SCHEMA_KEYS.intersection(_schema_keys(params))
        assert not hits, f"{schema.name} 含 Go 拒收关键字 {sorted(hits)}"


def test_run_description_is_one_command_face():
    desc = RunTool().schema.description
    assert "command" in desc.lower() or "命令" in desc
    assert "subcommand" not in desc
    assert "HOW→consult(run)" not in desc


def test_on_demand_faces_point_how_to_consult():
    """同名手册不在按钮复指；debate 入口合同写在 description。"""
    assert "HOW→consult(host)" not in HostTool().schema.description
    assert "HOW→consult(run)" not in RunTool().schema.description
    assert "HOW→consult(browser)" not in BrowserTool().schema.description
    assert DEBATE_DESCRIPTION == "不主动启动仅推荐：结构化正反辩论。"
    assert "HOW→consult" not in DEBATE_DESCRIPTION
    assert "决策简报" not in DEBATE_DESCRIPTION
    assert "非终结" not in DEBATE_DESCRIPTION
    assert "HOW→consult" not in DELEGATE_DESCRIPTION
    from agentcore.tools.builtin.delegate.schema import (
        DELEGATE_STAFF_HOW,
        DELEGATE_WHEN,
        NESTED_DELEGATE_DESCRIPTION,
        NESTED_STAFF_HOW,
    )

    assert DELEGATE_STAFF_HOW in DELEGATE_DESCRIPTION
    assert DELEGATE_STAFF_HOW in NESTED_DELEGATE_DESCRIPTION
    assert NESTED_STAFF_HOW in NESTED_DELEGATE_DESCRIPTION
    assert NESTED_STAFF_HOW not in DELEGATE_DESCRIPTION
    assert DELEGATE_WHEN in DELEGATE_DESCRIPTION
    assert DELEGATE_WHEN in NESTED_DELEGATE_DESCRIPTION
    assert "成篇落盘" not in NESTED_DELEGATE_DESCRIPTION
    host_cmd_desc = HostTool().schema.parameters["properties"]["command"]["description"]
    assert "Get-WinEvent" not in host_cmd_desc
    from agentcore.runtime.resolve.prompt import capability_how_suffix

    assert capability_how_suffix({"host"}) == ""
    assert capability_how_suffix({"browser"}) == ""
    host_cmd = HostTool().schema.parameters["properties"]["command"]["description"]
    assert "PowerShell" not in host_cmd
    text_desc = BrowserTool().schema.parameters["properties"]["text"]["description"]
    assert "密码" in text_desc
    sid_desc = BrowserTool().schema.parameters["properties"]["session_id"]["description"]
    assert "缺省解析" not in sid_desc
    assert not sid_desc.startswith("可选")
    assert set(HostTool().schema.parameters["properties"]) == {"command"}
    assert set(BrowserTool().schema.parameters["properties"]) == {
        "action",
        "url",
        "ref",
        "text",
        "snapshot_version",
        "session_id",
    }
    assert set(_ask_user_schema(desktop=True).parameters["properties"]) == {
        "questions",
    }
    assert set(RunTool().schema.parameters["properties"]) == {
        "command",
        "cwd",
        "background",
        "wait_for",
        "action",
        "process_id",
    }
    assert set(DEBATE_PARAMETERS["properties"]) == {
        "motion",
        "sides",
        "cross_model",
        "background",
        "moderator_model",
    }
    assert set(DEBATE_PARAMETERS["properties"]["sides"]["items"]["properties"]) == {
        "key",
        "name",
        "stance",
        "model",
    }
    wait_desc = RunTool().schema.parameters["properties"]["wait_for"]["description"]
    assert "省略" in wait_desc
    assert "默认就绪" not in wait_desc
    assert not wait_desc.startswith("可选")
    cwd_desc = RunTool().schema.parameters["properties"]["cwd"]["description"]
    assert not cwd_desc.endswith("可选。")
    assert not cwd_desc.startswith("可选")
    task_props = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]
    assert not task_props["id"]["description"].startswith("可选")
    assert not task_props["model"]["description"].startswith("（可选）")
    add_props = _REPLAN_PARAMETERS["properties"]["add"]
    assert not add_props["description"].startswith("可选")
    assert "必填" not in add_props["description"]
    assert not add_props["items"]["properties"]["id"]["description"].startswith("可选")
    assert not add_props["items"]["properties"]["depends_on"]["description"].startswith(
        "可选"
    )
    assert not _REPLAN_PARAMETERS["properties"]["tell"]["description"].startswith(
        "可选"
    )
    assert not _REPLAN_PARAMETERS["properties"]["stop"]["description"].startswith("可选")
    key_desc = DEBATE_PARAMETERS["properties"]["sides"]["items"]["properties"]["key"][
        "description"
    ]
    assert not key_desc.startswith("可选")
    assert not DEBATE_PARAMETERS["properties"]["background"]["description"].startswith(
        "可选"
    )
    from agentcore.tools.builtin.grep import GrepTool

    glob_desc = GrepTool().schema.parameters["properties"]["glob"]["description"]
    assert not glob_desc.startswith("可选")
    # description 不复述 action 表（取值语义留在 action 参数；enum 不再抄进 description）。
    assert "navigate/click/type" not in BrowserTool().schema.description
    for schema in (BrowserTool().schema,):
        action = schema.parameters["properties"]["action"]
        desc = action["description"]
        names = sorted(action["enum"])
        assert " / ".join(names) not in desc
        assert "|".join(names) not in desc


def test_worker_tool_schema_chars_within_cap():
    sizes = _measured_worker()
    assert set(sizes) == set(_WORKER_CAPS), (
        f"worker 棘轮覆盖面漂了：{sorted(set(sizes) ^ set(_WORKER_CAPS))}"
    )
    over = {
        name: (chars, _WORKER_CAPS[name])
        for name, chars in sizes.items()
        if chars > _WORKER_CAPS[name]
    }
    assert not over, f"worker 工具 schema 变胖（实测, 上限）：{over}"


def test_coordination_tool_schema_chars_within_cap():
    sizes = _measured_coord()
    assert set(sizes) == set(_COORD_CAPS), (
        f"协调棘轮覆盖面漂了：{sorted(set(sizes) ^ set(_COORD_CAPS))}"
    )
    over = {
        name: (chars, _COORD_CAPS[name])
        for name, chars in sizes.items()
        if chars > _COORD_CAPS[name]
    }
    assert not over, f"协调工具 schema 变胖（实测, 上限）：{over}"


def test_resident_file_tool_schema_chars_within_cap():
    sizes = _measured_file()
    assert set(sizes) == set(_FILE_CAPS), (
        f"常驻文件棘轮覆盖面漂了：{sorted(set(sizes) ^ set(_FILE_CAPS))}"
    )
    over = {
        name: (chars, _FILE_CAPS[name])
        for name, chars in sizes.items()
        if chars > _FILE_CAPS[name]
    }
    assert not over, f"常驻文件工具 schema 变胖（实测, 上限）：{over}"


def _measured_log() -> dict[str, int]:
    from agentcore.tools.builtin.read_conversation import ReadConversationTool
    from agentcore.tools.builtin.search_conversations import SearchConversationsTool

    return {
        "search_conversations": measure_openai_tool_chars(
            SearchConversationsTool().schema
        ),
        "read_conversation": measure_openai_tool_chars(ReadConversationTool().schema),
    }


def test_conversation_log_tool_schema_chars_within_cap():
    sizes = _measured_log()
    assert set(sizes) == set(_LOG_CAPS), (
        f"历史对话棘轮覆盖面漂了：{sorted(set(sizes) ^ set(_LOG_CAPS))}"
    )
    over = {
        name: (chars, _LOG_CAPS[name])
        for name, chars in sizes.items()
        if chars > _LOG_CAPS[name]
    }
    assert not over, f"历史对话工具 schema 变胖（实测, 上限）：{over}"


def _measured_web() -> dict[str, int]:
    from agentcore.tools.builtin.web.search import WebSearchTool
    from agentcore.tools.builtin.web.web_fetch import WebFetchTool

    return {
        "web_search": measure_openai_tool_chars(WebSearchTool().schema),
        "web_fetch": measure_openai_tool_chars(WebFetchTool().schema),
    }


def test_web_search_tool_schema_chars_within_cap():
    sizes = _measured_web()
    assert set(sizes) == set(_WEB_CAPS), (
        f"联网检索棘轮覆盖面漂了：{sorted(set(sizes) ^ set(_WEB_CAPS))}"
    )
    over = {
        name: (chars, _WEB_CAPS[name])
        for name, chars in sizes.items()
        if chars > _WEB_CAPS[name]
    }
    assert not over, f"web_search schema 变胖（实测, 上限）：{over}"
