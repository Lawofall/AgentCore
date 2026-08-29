"""软件交付类 playbook：diagnose_fix_verify."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.runs.playbooks._common import clean_str, clean_str_list


def diagnose_fix_verify(args: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """patch（短诊断后就地改）→ verify：【无先验调查批】的单症状修码协议。

    已有多角调查/审查批且用户确认按结论修 → 勿套本 playbook；手写 tasks +
    continue_from_run_id。硬形状禁止「单人包圆触顶后再换马甲从零读」——两波分职，
    验证失败应 escalate / 同人续派，勿新开巡读 worker。须在 playbook_args 写清 verify
    （CLI 命令或页面/UI 复现说明）；白屏/挂载类优先 browser 证据，勿用慢 typecheck 冒充。
    """
    problem = clean_str(
        args.get("problem") or args.get("error") or args.get("bug") or args.get("issue")
    )
    if not problem:
        return [], ["diagnose_fix_verify 需要 slot『problem』（运行时错误 / 缺 export 等症状）"]
    verify = clean_str(
        args.get("verify_command") or args.get("verify") or args.get("acceptance")
    )
    if not verify:
        return [], [
            "diagnose_fix_verify 需要 slot『verify』（怎么算修好：CLI 命令或页面/UI 复现说明；"
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
            "id": "patch",
            "role": "修补员",
            "task": (
                f"短诊断后就地修补【{problem}】。{target_hint}"
                "运行时空白/挂载/渲染复现：先 browser 证据（browser(action=navigate) + "
                "browser(action=console) + snapshot），再 ≤少数目标文件；无栈时可组件二分，"
                "勿空等用户 F12。"
                "最多读少数相关文件 / grep；先写出可消费短文——根因一句话 + 拟改路径与改法"
                "（勿空话一两句交差）；"
                "handoff：key_points 写根因与拟改路径；"
                "next_steps 须写清拟改是压住表面还是根因在结构上"
                "（要动契约/数据模型、同一根因改多层、或靠新增兜底/对账/自愈才能过）——"
                "没有就写「小修即可」；有则写清缺口。"
                "本批照修，不 escalate、不问用户。"
                "然后用 str_replace 就地改（已有非空代码禁骨架整文件重写）；"
                "改完用内环 code_diagnostics / 写盘回执中的诊断自检；"
                "禁止全量 typecheck/`test_run`（本批无 test_run）；"
                "禁止重新从零巡仓。"
                "交付：已修补的代码文件须落盘。"
            ),
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
