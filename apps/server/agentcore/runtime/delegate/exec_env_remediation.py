"""Honest remediation copy when the execution class is withheld.

Industry baseline for agent sandboxes: isolate (gVisor), default-deny egress,
and **disclose the real gap** — never prescribe a fix that contradicts the
session's already-bound workspace location.

Cloud session + ``code_execute`` withheld → sandbox / host unhealthy (or
config off), **not** 「工程还在本机、请再导入到云」.
"""

from __future__ import annotations

from typing import Any, Literal

RemediationKind = Literal[
    "delivery_action",
    "capability_run",
    "capability_office",
]


def _is_local_backend(backend: Any) -> bool:
    return getattr(backend, "location", None) == "local"


def cloud_sandbox_failure_hint() -> str | None:
    """Short operator-facing reason from the boot probe, if any."""
    try:
        from agentcore.tools.sandbox.cloud_health import cloud_sandbox_health_failure
    except ImportError:  # pragma: no cover — tests / partial imports
        return None
    failure = cloud_sandbox_health_failure()
    if not failure:
        return None
    reason, detail = failure
    if detail:
        return f"{reason}（{detail}）"
    return reason


def _sidecar_engine() -> bool:
    try:
        from agentcore.sidecar.server_pkg.core import is_sidecar_process
    except ImportError:
        return False
    return is_sidecar_process()


def exec_env_remediation_zh(
    *,
    backend: Any,
    kind: RemediationKind,
) -> str:
    """User/CEO-facing remediation for a missing execution class.

    ``kind`` selects the sentence frame; location selects the fix path.
    """
    local = _is_local_backend(backend)
    failure = cloud_sandbox_failure_hint()
    failure_clause = f"（探测：{failure}）" if failure else ""

    if kind == "delivery_action":
        if local:
            return (
                "本回合执行环境未装配：请在本机授权命令执行后再跑验证，"
                "或改用云协作（Composer「导入到云」）后重试。"
            )
        if _sidecar_engine():
            return (
                "当前是本机传统回合坐到了云文件夹：云端隔离执行只在云协作回合可用，"
                "不会在这台电脑上假装起云沙箱。"
                "拷文件请用文件工具；要在云上跑代码请新开云协作对话。"
                "**不要**再引导「导入到云」，也不要等本机沙箱恢复。"
            )
        return (
            "本回合已是云端会话，但云端执行沙箱未装配"
            f"{failure_clause}——**不要**再引导「导入到云」。"
            "可选：① 稍后重试（待宿主 gVisor/沙箱恢复）；"
            "② 有产物时 export_to_local 后在本机 npm/pip 运行；"
            "③ 本机传统打开本地文件夹（合法非默认，≠离线）。"
        )

    if kind == "capability_office":
        # 仅针对无确定性导出器的 Office 目标（.pptx/.xlsx/.odt/.rtf）。
        # Word/PDF 有 md_to_docx / md_to_pdf，与沙箱正交——别顺手一起报做不到。
        docx_clause = (
            "【注意·别扩大缺口】`.docx` / `.pdf` **不在**本缺口内："
            "确定性导出器 `md_to_docx` / `md_to_pdf` 与执行沙箱无关"
            "（装配态见 `<工作区>` 的 `产物格式：` 行），"
            "落盘 `.md` 后导出即可当场交付真 Word / PDF；"
            "【禁止】把 Word/PDF 一并说成本回合做不到。"
        )
        if local:
            return (
                "[能力提示] 本回合执行环境未装配（无 run），"
                "需执行才能生成的 Office 目标（.pptx/.xlsx 等）无法在本回合生成。"
                f"{docx_clause}"
                "【禁止】再派「写脚本 / 跑脚本」空转，也【禁止】再 claim run=已装配；"
                "请立即发 ask_user 卡说明缺口，并**推荐**引导 Composer「导入到云」"
                "或诚实收口并标缺口（脚本仅备本机运行，目标 Office 文件未生成）。"
                "本机传统三件套合法可教、非默认（≠离线）。"
            )
        if _sidecar_engine():
            return (
                "[能力提示] 本机传统回合坐到了云文件夹，执行环境未装配"
                "（无 run），"
                "需执行才能生成的 Office 目标（.pptx/.xlsx 等）无法在本回合生成。"
                f"{docx_clause}"
                "【禁止】再派「写脚本 / 跑脚本」空转；【禁止】再引导「导入到云」；"
                "【禁止】声称本机沙箱马上会好。"
                "请立即发 ask_user：拷文件用文件工具，要跑代码请新开云协作对话，"
                "或诚实收口标缺口。"
            )
        return (
            "[能力提示] 本回合已是云端会话，执行环境未装配"
            f"{failure_clause}（无 run），"
            "需执行才能生成的 Office 目标（.pptx/.xlsx 等）无法在本回合生成。"
            f"{docx_clause}"
            "【禁止】再派「写脚本 / 跑脚本」空转，也【禁止】再 claim 已装配；"
            "【禁止】再引导「导入到云」。"
            "请立即发 ask_user 说明沙箱不可用，并给 export_to_local / 本机传统 /"
            "稍后重试，或诚实收口标缺口。"
        )

    # capability_run
    if local:
        return (
            "[能力提示] 本回合执行环境未装配（无 run）："
            "任务文案涉及「运行 / 启动 / 生成二进制或可播放产物」，worker 只能写脚本 / 文件，"
            "无法真正运行或生成此类产物。收尾时请如实标缺口，或 ask_user 并**推荐** "
            "Composer「导入到云」后重派。"
            "本机传统三件套合法可教、非默认（≠离线）。"
        )
    if _sidecar_engine():
        return (
            "[能力提示] 本机传统回合坐到了云文件夹，执行环境未装配"
            "（无 run）："
            "worker 只能写脚本 / 文件，无法真正运行。"
            "【禁止】再引导「导入到云」；【禁止】再派 run。"
            "拷文件请用文件工具；要跑代码请新开云协作对话。收尾如实标缺口。"
        )
    return (
        "[能力提示] 本回合已是云端会话，执行沙箱未装配"
        f"{failure_clause}（无 run）："
        "worker 只能写脚本 / 文件，无法真正运行或生成需执行才能产出的产物。"
        "【禁止】再引导「导入到云」。收尾如实标缺口，或 ask_user："
        "稍后重试 / export_to_local 本机跑 / 本机传统（合法非默认）。"
    )


def cloud_exec_unavailable_delivery_action(backend: Any) -> dict[str, str]:
    """Wire action for delivery_status when cloud (or local) lacks execution.

    Wire ``kind`` stays ``bind_local_folder`` for client compatibility; product
    copy no longer defaults to 「再导入到云」 when already on a cloud desk.
    """
    return {
        "kind": "bind_local_folder",
        "description": exec_env_remediation_zh(
            backend=backend, kind="delivery_action"
        ),
    }
