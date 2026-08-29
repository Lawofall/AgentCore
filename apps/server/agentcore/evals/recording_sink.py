"""EventSink hook that records tool calls and delegation roster for assertions."""

from __future__ import annotations

import json

from agentcore.runtime.events import EventSink, EventType, SSEEvent

# 协作互动辅指标：SSE 事件类型 → 短标签。只计「事件流里确实有的」协作信号，不做剧场奖励设计。
_COLLAB_EVENT_KEYS: dict[EventType, str] = {
    EventType.RUN_ESCALATION: "escalate",
    EventType.ESCALATION_REQUIRED: "escalate",
    EventType.PLAN_REVISED: "replan",
}


class RecordingSink(EventSink):
    """在现有 :class:`EventSink` 上挂钩，捕获过程事实供断言（其余照常入 journal/queue）.

    - ``tool_calls``：从 ``tool_use_start`` 取 ``(name, args_json)``，按发生顺序；
    - ``roster``：从 ``run_plan`` 的 ``agents[*].role`` 取委派计划期的**语义角色**（去重、保序）。
      不取 ``run_completed.role``——那是成本台账类目（member/captain），非语义角色；也不取
      ``run_started``（其载荷无 role）。
    - ``plan_runs``：从 ``run_plan.runs`` 保留完整计划图（过滤 captain/CEO），供形状匹配度；
    - ``plan_type``：最近一次 ``run_plan`` 的类型（``multi_agent`` / ``debate`` / …）；
    - ``collab_interactions``：升级 / replan / 续派等协作事件计数。
    """

    def __init__(self) -> None:
        super().__init__()
        self.tool_calls: list[tuple[str, str]] = []
        self.roster: list[str] = []
        self.plan_runs: list[dict] = []
        self.plan_type: str | None = None
        self.collab_interactions: dict[str, int] = {}

    def emit(self, event: SSEEvent) -> bool:
        if event.type == EventType.TOOL_USE_START:
            self._record_tool_call(event.payload)
        elif event.type == EventType.RUN_PLAN:
            self._record_roster(event.payload)
            self._record_plan(event.payload)
        elif event.type == EventType.RUN_STARTED:
            self._record_continue(event.payload)
        elif event.type in _COLLAB_EVENT_KEYS:
            self._bump(_COLLAB_EVENT_KEYS[event.type])
        return super().emit(event)

    def _bump(self, key: str) -> None:
        self.collab_interactions[key] = self.collab_interactions.get(key, 0) + 1

    def _record_tool_call(self, payload: dict) -> None:
        name = payload.get("tool_name", "")
        args = payload.get("arguments")
        if isinstance(args, str):
            args_json = args
        else:
            try:
                args_json = json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                args_json = "{}"
        self.tool_calls.append((name, args_json))

    def _record_roster(self, payload: dict) -> None:
        for agent in payload.get("agents", []) or []:
            role = (agent or {}).get("role")
            if role and role not in self.roster:
                self.roster.append(role)

    def _record_plan(self, payload: dict) -> None:
        """从 ``run_plan`` 抽出过滤后的计划图节点（形状数据源；不读 logs）。"""
        plan_type = payload.get("plan_type")
        # debate 优先保留：嵌套 multi_agent 子计划不应覆盖顶层辩论类型。
        if plan_type and self.plan_type != "debate":
            self.plan_type = str(plan_type)

        agents = payload.get("agents", []) or []
        role_by_id: dict[str, str] = {}
        for agent in agents:
            if not agent:
                continue
            aid = agent.get("id")
            role = agent.get("role")
            if aid and role:
                role_by_id[str(aid)] = str(role)

        # 保序 dict：已有节点在前，同 id 覆盖，新 id 追加（嵌套 lead 的二次 run_plan 会并入）。
        by_id: dict[str, dict] = {str(r["id"]): r for r in self.plan_runs if r.get("id")}
        for run in payload.get("runs", []) or []:
            if not run:
                continue
            if run.get("kind") == "captain":
                continue
            agent_id = str(run.get("agent_id") or "")
            role = role_by_id.get(agent_id, "")
            if role == "CEO":
                continue
            rid = str(run.get("id") or "")
            if not rid:
                continue
            by_id[rid] = {
                "id": rid,
                "role": role,
                "task": str(run.get("task") or ""),
                "depends_on": [str(d) for d in (run.get("depends_on") or []) if d],
                "parent_run_id": run.get("parent_run_id"),
            }
        self.plan_runs = list(by_id.values())

    def _record_continue(self, payload: dict) -> None:
        """``run_started`` 带 ``continues_run_id`` = 同人续派 / 热修。"""
        if payload.get("continues_run_id"):
            self._bump("continue")
