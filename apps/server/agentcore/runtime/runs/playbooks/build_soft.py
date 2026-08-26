"""软件交付类 playbook：build_feature / repair_code / build_app."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.runs.build_app import _build_app
from agentcore.runtime.runs.playbooks._common import clean_str, clean_str_list


def build_feature(args: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """后端接口 →（前端页面 ‖ 测试）并行依赖接口；接口契约经便签墙对齐.

    The doc's recurring 登录 example, and a direct consumer of the just-shipped 4b 拼图边对账 —
    the parallel 页面 / 测试 share the api's broadcast interface contract."""
    feature = clean_str(args.get("feature"))
    if not feature:
        return [], ["build_feature 需要 slot『feature』（要实现的功能）"]
    stack = clean_str(args.get("stack"))
    stack_hint = f"（技术栈：{stack}）" if stack else ""
    include = clean_str_list(args.get("include"), cap=2)
    want_ui = (not include) or ("ui" in include)
    want_test = (not include) or ("test" in include)

    tasks: list[dict[str, Any]] = [
        {
            "id": "api",
            "role": "后端工程师",
            "task": (
                f"实现【{feature}】的后端接口{stack_hint}。先把接口契约（路径 / 方法 / 入参 / "
                "返回结构 / 错误形状）用 post_note(kind=decision) 广播到团队便签墙，再实现；"
                "务必用 file_write 把代码写进工作区。"
                "交付：可用的后端接口 + 已广播的接口契约。"
            ),
            "deliverable": {"form": "workspace"},
        }
    ]
    if want_ui:
        tasks.append(
            {
                "id": "ui",
                "role": "前端工程师",
                "task": (
                    f"实现【{feature}】的前端页面{stack_hint}，严格对接 api 步骤广播的接口契约"
                    "（路径 / 字段 / 返回）。发现契约对不上就按最新契约对齐、"
                    "必要时 post_note 提醒；"
                    "务必用 file_write 把代码写进工作区。"
                    "交付：可用的前端页面，对接后端接口。"
                ),
                "depends_on": ["api"],
                "deliverable": {"form": "workspace"},
            }
        )
    if want_test:
        tasks.append(
            {
                "id": "test",
                "role": "测试工程师",
                "task": (
                    f"为【{feature}】写测试，按便签墙上 api 广播的接口契约"
                    "覆盖正常 + 边界 + 错误形状；"
                    "务必用 file_write 把测试文件写进工作区。"
                    "交付：覆盖接口契约的测试。"
                ),
                "depends_on": ["api"],
                "deliverable": {"form": "workspace"},
            }
        )
    return tasks, []


def repair_code(args: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """diagnose(短) → patch → verify：【无先验调查批】的单症状修码协议。

    已有多角调查/审查批且用户确认按结论修 → 勿套本 playbook；手写 tasks +
    continue_from_run_id。硬形状禁止「单人包圆触顶后再换马甲从零读」——三角色分波，
    验证失败应 escalate / 同人续派，勿新开巡读 worker。须在 playbook_args 写清 verify
    （CLI 命令或页面/UI 复现说明）；白屏/挂载类优先 browser 证据，勿用慢 typecheck 冒充。
    """
    problem = clean_str(
        args.get("problem") or args.get("error") or args.get("bug") or args.get("issue")
    )
    if not problem:
        return [], ["repair_code 需要 slot『problem』（运行时错误 / 缺 export 等症状）"]
    verify = clean_str(
        args.get("verify_command") or args.get("verify") or args.get("acceptance")
    )
    if not verify:
        return [], [
            "repair_code 需要 slot『verify』（怎么算修好：CLI 命令或页面/UI 复现说明；"
            "亦接受 verify_command / acceptance），"
            "例：verify=\"pytest tests/test_app.py -q\" 或 "
            'verify="打开 /app 白屏消失+snapshot 可见主内容"'
        ]
    target = clean_str(args.get("target") or args.get("file") or args.get("path"))
    target_hint = f"优先路径：`{target}`。" if target else "先定位最小相关文件，禁止全仓通读。"
    artifacts = clean_str_list(args.get("artifacts"), cap=4)
    if target and target not in artifacts:
        artifacts = [target, *artifacts]
    patch_deliverable: dict[str, Any] = {"form": "workspace"}
    if artifacts:
        patch_deliverable["artifacts"] = artifacts

    tasks: list[dict[str, Any]] = [
        {
            "id": "diagnose",
            "role": "诊断员",
            "task": (
                f"短诊断【{problem}】。{target_hint}"
                "运行时空白/挂载/渲染复现：先 browser 证据（browser(action=navigate) + "
                "browser(action=console) + snapshot），再 ≤少数目标文件；无栈时可组件二分，"
                "勿空等用户 F12。"
                "最多读少数相关文件 / grep；输出：可消费短文——根因一句话 + 拟改路径与改法"
                "（勿空话一两句交差）；"
                "handoff：key_points 写根因与拟改路径（给修补员接力）；"
                "next_steps 须写清拟改是压住表面还是根因在结构上"
                "（要动契约/数据模型、同一根因改多层、或靠新增兜底/对账/自愈才能过）——"
                "没有就写「小修即可」；有则写清缺口。"
                "本批照修，不挡修补、不 escalate、不问用户。"
                "禁止全仓 list、禁止大范围通读、禁止在本步改文件。"
                "多已知问题 / 已有调查批确认要修 → 勿套 repair_code，改用手写 tasks +"
                "continue_from_run_id。"
            ),
            "max_rounds": 4,
            "deliverable": {"form": "prose"},
        },
        {
            "id": "patch",
            "role": "修补员",
            "task": (
                f"按诊断结果修补【{problem}】。{target_hint}"
                "用 str_replace 就地改（已有非空代码禁骨架整文件重写）；"
                "改完用内环 code_diagnostics / 写盘回执中的诊断自检；"
                "禁止全量 typecheck/`test_run`（本批无 test_run）；"
                "禁止重新从零巡仓。"
                "交付：已修补的代码文件须落盘。"
            ),
            "depends_on": ["diagnose"],
            "max_rounds": 6,
            "deliverable": patch_deliverable,
        },
        {
            "id": "verify",
            "role": "验证员",
            "task": (
                f"验证【{problem}】修补是否生效。约定验收：`{verify}`——"
                "若约定是 CLI 命令 → 外环用 test_run（check=command，command 填该约定命令；或 "
                "check=test/typecheck/build）跑通且 exit 0；"
                "若约定是页面/UI 复现（白屏/挂载/渲染）→ "
                "优先 browser(action=navigate) + snapshot 取证"
                "（需截图证据才 browser(action=screenshot)）；"
                "【禁止】用慢 typecheck / 全仓 tsc 冒充白屏修好；"
                "【不要】把慢 build/全量 tsc 塞进 code_execute；"
                "全量 typecheck/build/`tsc -b` 仅当约定本就是 CLI 验绿时"
                "由本验收员独占执行（外环），"
                "勿与 fix 批并行全仓；内环 code_diagnostics 不能代替本步验绿；"
                "纯 prose 交卷不算过门；验证结果须写清通过或失败证据（勿空话交差）。"
                "失败则 escalate 说明缺口，"
                "禁止新开巡读或换马甲从零读仓库；禁止无产出反复空跑同一失败命令。"
            ),
            "depends_on": ["patch"],
            "max_rounds": 4,
            "deliverable": {"form": "prose"},
        },
    ]
    return tasks, []


# Re-export build_app builder under the soft-delivery module (lives in build_app.py).
build_app = _build_app
