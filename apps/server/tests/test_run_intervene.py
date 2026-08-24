"""按人干预的受理判定（够不着就不入队，也不许回「已受理」）。

覆盖 :mod:`agentcore.runtime.runs.drive_reach` 的登记/注销与
:mod:`agentcore.runtime.runs.intervene` 的三种回话。
"""

import pytest

from agentcore.runtime.runs.drive_reach import (
    drive_reach,
    register_drive,
    reset_drive_registry,
    unregister_drive,
)
from agentcore.runtime.runs.intervene import accept_run_redirect, accept_run_stop
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.redirect_queue import peek_redirect_count, take_redirects
from agentcore.runtime.runs.stop_queue import peek_stop_count, take_stops
from agentcore.runtime.runs.types import RunSpec

EID = "exec-1"
CID = "conv-1"


def plan_of(*run_ids: str) -> RunPlan:
    return RunPlan(nodes=[RunSpec(run_id=rid, task="t") for rid in run_ids])


@pytest.fixture(autouse=True)
def _clean():
    reset_drive_registry()
    take_stops(EID)
    take_redirects(EID)
    yield
    reset_drive_registry()
    take_stops(EID)
    take_redirects(EID)


def test_reach_is_false_without_a_live_drive():
    assert drive_reach(EID, "r1") == drive_reach(EID, None)
    assert not drive_reach(EID, "r1").driving


def test_reach_answers_both_questions_off_the_live_plan():
    register_drive(EID, plan_of("r1", "r2"))

    assert drive_reach(EID, "r1").reachable
    # 驱动活着，但这个 run 不在它的计划里——两件事分开答。
    absent = drive_reach(EID, "r9")
    assert absent.driving and not absent.in_plan
    # 停全部：不指名就没有「在不在计划里」可问。
    assert drive_reach(EID, None).reachable


def test_reach_sees_nodes_the_captain_appends_mid_run():
    # 登记的是活的 plan 对象本身，故冷回落追加进来的节点立刻够得着。
    plan = plan_of("r1")
    register_drive(EID, plan)
    plan.nodes.append(RunSpec(run_id="r1#2", task="t"))

    assert drive_reach(EID, "r1#2").reachable


def test_unregister_drops_reach_and_is_per_drive():
    # 嵌套子团队与父团队共用 execution_id：任一活着即够得着，各自摘各自的。
    token_parent = register_drive(EID, plan_of("r1"))
    token_child = register_drive(EID, plan_of("s1"))

    unregister_drive(EID, token_parent)
    assert not drive_reach(EID, "r1").in_plan
    assert drive_reach(EID, "s1").reachable

    unregister_drive(EID, token_child)
    assert not drive_reach(EID, "s1").driving


def test_stop_refused_when_the_drive_is_gone():
    ack = accept_run_stop(execution_id=EID, conversation_id=CID, run_id="r1")

    assert not ack.accepted
    assert ack.reason == "no_live_drive"
    assert ack.detail
    # 够不着就一个都不入队：队列没人排干，入进去只会永远躺着。
    assert ack.queued == 0
    assert peek_stop_count(EID) == 0


def test_stop_refused_for_a_run_outside_the_live_plan():
    register_drive(EID, plan_of("r1"))

    ack = accept_run_stop(execution_id=EID, conversation_id=CID, run_id="ghost")

    assert (ack.accepted, ack.reason, ack.queued) == (False, "unknown_run", 0)
    assert peek_stop_count(EID) == 0


def test_stop_accepted_and_enqueued_when_reachable():
    register_drive(EID, plan_of("r1"))

    ack = accept_run_stop(execution_id=EID, conversation_id=CID, run_id="r1")

    assert (ack.accepted, ack.reason, ack.queued) == (True, "queued", 1)
    assert [item.run_id for item in take_stops(EID)] == ["r1"]


def test_stop_all_needs_only_a_live_drive():
    register_drive(EID, plan_of("r1"))

    ack = accept_run_stop(execution_id=EID, conversation_id=CID)

    assert ack.accepted
    assert [item.run_id for item in take_stops(EID)] == [None]


def test_redirect_refused_when_unreachable_and_accepted_when_not():
    refused = accept_run_redirect(
        execution_id=EID, run_id="r1", feedback="换数据源", conversation_id=CID
    )
    assert (refused.accepted, refused.reason) == (False, "no_live_drive")
    assert peek_redirect_count(EID) == 0

    register_drive(EID, plan_of("r1"))
    ack = accept_run_redirect(
        execution_id=EID, run_id="r1", feedback="换数据源", conversation_id=CID
    )

    assert (ack.accepted, ack.reason, ack.queued) == (True, "queued", 1)
    assert [(i.run_id, i.feedback) for i in take_redirects(EID)] == [("r1", "换数据源")]


def test_refusals_carry_a_user_facing_line_per_action():
    # 三端照抄服务端这句，故停 / 改两条路不能共用一句含糊话。
    register_drive(EID, plan_of("r1"))
    stop = accept_run_stop(execution_id=EID, conversation_id=CID, run_id="ghost")
    redirect = accept_run_redirect(
        execution_id=EID, run_id="ghost", feedback="f", conversation_id=CID
    )

    assert stop.detail != redirect.detail
    assert "改" in redirect.detail


def test_stop_logs_queued_when_the_drive_is_live():
    from structlog.testing import capture_logs

    register_drive(EID, plan_of("r1"))
    with capture_logs() as logs:
        accept_run_stop(execution_id=EID, conversation_id=CID, run_id="r1")

    hits = [e for e in logs if e.get("event") == "run_stop.queued"]
    assert len(hits) == 1
    assert hits[0]["conversation_id"] == CID
    assert hits[0]["execution_id"] == EID
    assert hits[0]["run_id"] == "r1"
    assert hits[0]["queued"] == 1


def test_stop_logs_unreachable_when_the_drive_is_gone():
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        accept_run_stop(execution_id=EID, conversation_id=CID, run_id="r1")

    hits = [e for e in logs if e.get("event") == "run_stop.unreachable"]
    assert len(hits) == 1
    assert hits[0]["reason"] == "no_live_drive"
    assert hits[0]["run_id"] == "r1"
