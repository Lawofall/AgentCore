"""离线录播生产：固定种子跑出一期 run + EpisodeManifest。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentcore.simulation.scenarios.show.cast import (
    JIANGYU,
    LUYE,
    SHENWAN,
    XIEHENG,
    XUANAN,
    ZHOUKE,
)
from agentcore.simulation.scenarios.show.config import SHOW_CONFIG, seed_show_world
from agentcore.simulation.show.director import (
    GOLDEN_TOP_KEYS,
    compile_episode_manifest,
    manifest_shape_keys,
)
from agentcore.simulation.show.models import ShowSeasonState
from agentcore.simulation.show.orchestrator import (
    apply_day_positions,
    apply_night_positions,
    plan_episode,
)
from agentcore.simulation.show.rules import (
    apply_scripted_picks,
    new_season_state,
    resolve_ceremony,
)

# Episode 3 scripted ballot (§3.10 / Web demo 定稿).
EPISODE_3_PICKS: dict[str, str] = {
    LUYE: SHENWAN,
    SHENWAN: XIEHENG,
    XUANAN: XIEHENG,
    JIANGYU: XUANAN,
    ZHOUKE: JIANGYU,
    XIEHENG: ZHOUKE,
}

# Prior sealed history so ep3 zero-vote streak for 陆野 is meaningful in narrative
# (streak starts from ep3 ceremony itself; ep1–2 history optional for produce).
# Prior sealed history so ep3 narrative (陆野零票告急苗头) is consistent without
# anyone hitting 连续两期零票 departure before ep3.
EPISODE_1_PICKS: dict[str, str] = {
    LUYE: SHENWAN,
    SHENWAN: ZHOUKE,
    XUANAN: LUYE,
    JIANGYU: XUANAN,  # ensure C gets a vote
    ZHOUKE: XIEHENG,
    XIEHENG: JIANGYU,
}
EPISODE_2_PICKS: dict[str, str] = {
    LUYE: SHENWAN,
    SHENWAN: ZHOUKE,
    XUANAN: LUYE,
    JIANGYU: LUYE,
    ZHOUKE: JIANGYU,
    XIEHENG: ZHOUKE,
}

FIXED_EP3_SEED = 42
FIXED_EP3_RUN_ID = "show-episode-3-seed42"


@dataclass
class ProducedEpisode:
    run_id: str
    seed: int
    episode_no: int
    season: ShowSeasonState
    events: list[dict[str, Any]] = field(default_factory=list)
    tick_snapshots: dict[int, dict[str, Any]] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)

    def write(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "run.json").write_text(
            json.dumps(
                {
                    "run_id": self.run_id,
                    "seed": self.seed,
                    "scenario": "show",
                    "episode_no": self.episode_no,
                    "current_tick": self.season.episodes[-1].tick_span_end
                    if self.season.episodes
                    else 0,
                    "events": self.events,
                    "season": self.season.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (out_dir / "episode-manifest.json").write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _run_prior_episodes(season: ShowSeasonState, *, through: int) -> None:
    """Fast-forward ceremonies for episodes 1..through-1 so streaks/pairs are consistent."""
    for ep in range(1, through):
        plan = plan_episode(season, ep)
        picks = {1: EPISODE_1_PICKS, 2: EPISODE_2_PICKS}.get(ep)
        if picks is None:
            # Neutral safe picks within allowed set.
            picks = {v: allowed[0] for v, allowed in plan.allowed_picks.items() if allowed}
        apply_scripted_picks(season, picks, episode_no=ep)
        resolve_ceremony(
            season,
            episode_no=ep,
            quiz_focus=plan.quiz_focus,
            tick_start=plan.tick_start,
            tick_end=plan.tick_end,
        )


def produce_episode(
    *,
    episode_no: int = 3,
    seed: int = FIXED_EP3_SEED,
    run_id: str | None = None,
    prior_history: bool = True,
    standalone_ticks: bool = True,
) -> ProducedEpisode:
    """Offline scripted production — no LLM credentials required.

    ``standalone_ticks=True`` (default): remap the episode's tick window to
    ``0 .. ticks_per_episode-1`` so a single-episode run aligns with the
    EpisodeManifest fixture tape (ep3 golden uses 0–119).
    """
    rid = run_id or (
        FIXED_EP3_RUN_ID
        if episode_no == 3 and seed == FIXED_EP3_SEED
        else f"show-ep{episode_no}-seed{seed}"
    )
    season = new_season_state(seed=seed, run_id=rid, season_id=SHOW_CONFIG.season_title)
    events: list[dict[str, Any]] = []
    ticks: dict[int, dict[str, Any]] = {}

    if prior_history and episode_no > 1:
        _run_prior_episodes(season, through=episode_no)

    plan = plan_episode(season, episode_no)
    if standalone_ticks and plan.tick_start != 0:
        offset = plan.tick_start
        remapped_gates = {
            gate: (start - offset, end - offset) for gate, (start, end) in plan.gates.items()
        }
        plan = plan.model_copy(
            update={
                "tick_start": 0,
                "tick_end": plan.tick_end - offset,
                "gates": remapped_gates,
            }
        )

    world = seed_show_world()

    for gate, (start, _end) in plan.gates.items():
        if gate == "day":
            apply_day_positions(world, plan)
        elif gate in ("night", "ceremony", "reveal", "quiz", "epilogue"):
            apply_night_positions(world, plan)
        ticks[start] = world.snapshot().model_dump(mode="json")

    # Scripted ballot for ep3; otherwise first allowed target.
    if episode_no == 3:
        picks = EPISODE_3_PICKS
    else:
        picks = {
            voter: targets[0]
            for voter, targets in plan.allowed_picks.items()
            if targets
        }
    apply_scripted_picks(season, picks, episode_no=episode_no)

    record = resolve_ceremony(
        season,
        episode_no=episode_no,
        awkward_kind=plan.awkward_kind,
        quiz_focus=plan.quiz_focus,
        tick_start=plan.tick_start,
        tick_end=plan.tick_end,
    )

    ticks[plan.tick_end] = world.snapshot().model_dump(mode="json")

    manifest_model = compile_episode_manifest(
        run_id=rid,
        season=season,
        plan=plan,
        record=record,
    )
    manifest = manifest_model.model_dump(mode="json", by_alias=True)
    # Ensure shape parity with golden fixture.
    missing = GOLDEN_TOP_KEYS - manifest_shape_keys(manifest_model)
    if missing:
        raise RuntimeError(f"EpisodeManifest missing keys: {sorted(missing)}")

    return ProducedEpisode(
        run_id=rid,
        seed=seed,
        episode_no=episode_no,
        season=season,
        events=events,
        tick_snapshots=ticks,
        manifest=manifest,
    )


def produce_episode3_cli(out_dir: str | Path | None = None) -> ProducedEpisode:
    """Acceptance entry: fixed seed episode 3 → run + EpisodeManifest on disk."""
    produced = produce_episode(episode_no=3, seed=FIXED_EP3_SEED, run_id=FIXED_EP3_RUN_ID)
    target = Path(out_dir) if out_dir else Path("eval-out") / "show-episode-3"
    produced.write(target)
    return produced
