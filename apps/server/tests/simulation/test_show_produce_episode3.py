"""第 3 期离线生产 + 导播编译验收。"""

from __future__ import annotations

import json
from pathlib import Path

from agentcore.simulation.show.catalog import load_from_produce_dir, reset_catalog, submit_quiz
from agentcore.simulation.show.director import GOLDEN_TOP_KEYS, load_episode3_fixture
from agentcore.simulation.show.models import QuizSubmission
from agentcore.simulation.show.produce import FIXED_EP3_RUN_ID, FIXED_EP3_SEED, produce_episode


def test_produce_episode3_shape_matches_fixture(tmp_path: Path):
    produced = produce_episode(episode_no=3, seed=FIXED_EP3_SEED, run_id=FIXED_EP3_RUN_ID)
    produced.write(tmp_path)

    fixture = load_episode3_fixture()
    manifest = produced.manifest

    assert set(manifest.keys()) >= GOLDEN_TOP_KEYS
    assert set(fixture.keys()) <= set(manifest.keys()) | {"tagline", "rule_line"}
    assert manifest["version"] == fixture["version"]
    assert manifest["episode_no"] == 3
    assert manifest["run_id"] == FIXED_EP3_RUN_ID
    assert manifest["season"] == fixture["season"]
    assert len(manifest["segments"]) == len(fixture["segments"])
    assert {s["kind"] for s in manifest["segments"]} == {s["kind"] for s in fixture["segments"]}
    assert len(manifest["highlights"]) == 3
    assert manifest["quiz"]["focus"] == fixture["quiz"]["focus"]
    assert manifest["quiz"]["answer"] == fixture["quiz"]["answer"]
    assert len(manifest["reveal"]["steps"]) == 6

    run = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert run["events"] == []
    # 陆野零票（ep3 剧本）走赛制状态 / 清单，不再发 sim.show.* 事件。
    assert produced.season.zero_vote_streak.get("luye", 0) >= 1


def test_produce_same_seed_reproducible():
    a = produce_episode(episode_no=3, seed=FIXED_EP3_SEED, run_id="r1")
    b = produce_episode(episode_no=3, seed=FIXED_EP3_SEED, run_id="r1")
    assert a.manifest["quiz"]["answer"] == b.manifest["quiz"]["answer"]
    assert [e["type"] for e in a.events] == [e["type"] for e in b.events]
    assert a.season.model_dump() == b.season.model_dump()


def test_catalog_quiz_settlement(tmp_path: Path):
    reset_catalog()
    produced = produce_episode(episode_no=3, seed=FIXED_EP3_SEED)
    produced.write(tmp_path)
    meta = load_from_produce_dir(tmp_path, publish_status="published")
    wrong = submit_quiz(
        QuizSubmission(episode_id=meta.episode_id, user_id="u1", guess="luye")
    )
    assert wrong.correct is False
    right = submit_quiz(
        QuizSubmission(episode_id=meta.episode_id, user_id="u2", guess="xieheng")
    )
    assert right.correct is True
