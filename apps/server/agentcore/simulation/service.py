"""Simulation orchestration service (M1 closed loop)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from agentcore.config import settings
from agentcore.core.errors import ValidationError
from agentcore.core.logging import get_logger
from agentcore.db.repositories.simulation import SimulationRepository
from agentcore.runtime.events import EventSink, EventType, SSEEvent
from agentcore.runtime.events.types import RETIRED_EVENT_TYPE_VALUES
from agentcore.simulation.agents.activation import (
    ActivationContext,
    AgentActivationStrategy,
    apply_schedule_fallback,
    default_activation_strategy,
)
from agentcore.simulation.agents.memory import apply_tick_memories
from agentcore.simulation.agents.models import SimPersona
from agentcore.simulation.agents.reflection import apply_reflections
from agentcore.simulation.agents.scripted import (
    drain_scripted_pending,
    run_scripted_demo_pulse,
    run_scripted_ticks,
)
from agentcore.simulation.agents.social import apply_social_updates
from agentcore.simulation.agents.tick_batch import TickBatchOptions, run_agent_ticks_batch
from agentcore.simulation.agents.tick_runner import AgentTickOutcome
from agentcore.simulation.cost_guards import ensure_under_max_ticks, slice_personas_for_run
from agentcore.simulation.experiment.manifest import RunManifest, build_run_manifest
from agentcore.simulation.interaction import (
    InteractionBus,
    InteractionTickContext,
    SimInteractionPayload,
)
from agentcore.simulation.llm import (
    SimDecisionKind,
    SimLlmNotConfigured,
    SimModelRouter,
    SimModelRoutingConfig,
    build_sim_provider,
    default_routing_config,
    resolve_text_mode,
)
from agentcore.simulation.observe.metrics import MetricsAggregator
from agentcore.simulation.observe.types import TickMetrics
from agentcore.simulation.scenarios.town.config import TOWN_PERSONAS, seed_town_world
from agentcore.simulation.stream_registry import (
    SimulationStreamRegistry,
    default_sim_stream_registry,
)

logger = get_logger(__name__)
from agentcore.simulation.types import (
    SimAgentActionPayload,
    SimAgentStatePayload,
    SimTickEndedPayload,
    SimTickSnapshot,
    SimTickStartedPayload,
    SimWorldEventPayload,
    WorldEventWire,
    WorldModifiersWire,
)
from agentcore.simulation.world.engine import WorldEngine
from agentcore.simulation.world.events import (
    EventScheduler,
    build_preset_event,
    parse_pending_injections,
)
from agentcore.simulation.world.persistence import persist_tick


class SimulationService:
    def __init__(
        self,
        repo: SimulationRepository,
        *,
        stream_registry: SimulationStreamRegistry | None = None,
        text_mode: bool | None = None,
        activation_strategy: AgentActivationStrategy | None = None,
    ):
        self._repo = repo
        self._streams = stream_registry or default_sim_stream_registry
        # None = auto: native tools on DeepSeek, text-JSON fallback on other upstreams.
        self._text_mode = text_mode
        self._activation = activation_strategy or default_activation_strategy()
        self._tick_db_lock: asyncio.Lock | None = None

    async def create_run(
        self,
        *,
        user_id: str,
        scenario: str = "town",
        seed: int = 0,
        scripted: bool = False,
        manifest: RunManifest | None = None,
    ):
        if manifest is not None:
            scenario = manifest.scenario
            seed = manifest.seed

        # Opt-in scripted: body.scripted | SIMULATION_SCRIPTED | manifest.scripted.
        # Missing DeepSeek at advance_tick still auto-falls back (see _resolve_tick_mode).
        want_scripted = (
            scripted
            or settings.simulation_scripted
            or (manifest is not None and manifest.scripted)
        )

        routing: SimModelRoutingConfig | None = None
        config: dict = {}
        try:
            _, llm_cfg = await build_sim_provider(self._repo._session, user_id)
            routing = (
                manifest.model_routing
                if manifest is not None and manifest.model_routing is not None
                else default_routing_config(llm_cfg.model)
            )
            config = {"model_routing": routing.model_dump()}
        except SimLlmNotConfigured:
            if manifest is not None and manifest.model_routing is not None:
                routing = manifest.model_routing
                config = {"model_routing": routing.model_dump()}
            elif want_scripted:
                logger.info(
                    "simulation.create_scripted",
                    reason="no_deepseek_opt_in",
                    user_id=user_id,
                )

        if manifest is not None:
            run_manifest = manifest.model_copy(
                update={
                    "created_at": datetime.now(UTC),
                    "scripted": want_scripted or manifest.scripted,
                }
            )
        else:
            run_manifest = build_run_manifest(
                scenario=scenario,
                seed=seed,
                model_routing=routing,
                scripted=want_scripted,
                created_at=datetime.now(UTC),
            )
        personas = slice_personas_for_run(
            tuple(run_manifest.personas) or TOWN_PERSONAS,
            max_agents=settings.max_agents,
        )
        if list(run_manifest.personas) != list(personas):
            logger.info(
                "simulation.persona_slice",
                requested=len(run_manifest.personas) or len(TOWN_PERSONAS),
                kept=len(personas),
                max_agents=settings.max_agents,
            )
            run_manifest = run_manifest.model_copy(update={"personas": list(personas)})
        config["manifest"] = run_manifest.model_dump(mode="json")
        if run_manifest.scripted:
            config["scripted"] = True

        run = await self._repo.create_run(
            user_id=user_id, scenario=scenario, seed=seed, config=config
        )
        if scenario == "show":
            from agentcore.simulation.scenarios.show.config import seed_show_world

            world = seed_show_world(personas)
            from agentcore.simulation.show.rules import new_season_state

            config["show"] = new_season_state(
                seed=seed, run_id=run.id, season_id="心动小镇"
            ).model_dump(mode="json")
            await self._repo.update_run_config(run.id, config)
            run.config = config
        else:
            world = seed_town_world(personas)
        for persona in personas:
            agent = world.agents[persona.agent_id]
            await self._repo.add_agent(run.id, persona, agent.to_state())
        await self._streams.get_or_create(run.id)
        return run

    async def pause_run(self, run_id: str, *, user_id: str) -> None:
        run = await self._repo.get_run(run_id, user_id=user_id)
        if run is None:
            raise KeyError("run not found")
        if run.status == "paused":
            return
        if run.status not in ("running", "created"):
            raise ValidationError(f"无法暂停状态为 {run.status!r} 的 run")
        await self._repo.set_run_status(run_id, status="paused")

    async def resume_run(self, run_id: str, *, user_id: str) -> None:
        run = await self._repo.get_run(run_id, user_id=user_id)
        if run is None:
            raise KeyError("run not found")
        if run.status == "running":
            return
        if run.status != "paused":
            raise ValidationError(f"无法恢复状态为 {run.status!r} 的 run")
        await self._repo.set_run_status(run_id, status="running")

    async def advance_tick(self, run_id: str, *, user_id: str) -> SimTickSnapshot:
        run = await self._repo.get_run(run_id, user_id=user_id)
        if run is None:
            raise KeyError("run not found")
        if run.status == "paused":
            raise ValidationError("模拟已暂停，请先恢复后再推进 tick")
        ensure_under_max_ticks(run.current_tick, max_ticks=settings.max_ticks)
        sink = await self._streams.get_or_create(run_id)
        world = await self._load_world(run_id, run.current_tick, scenario=run.scenario)
        engine = WorldEngine(world=world, seed=run.seed)
        next_tick = world.tick + 1
        next_hour = (8 + next_tick) % 24
        await self._emit_tick_started(sink, run_id, next_tick, next_hour)
        await engine.advance()
        tick = world.tick
        hour = world.hour

        event_scheduler = EventScheduler(seed=run.seed)
        event_scheduler.expire_stale(world)
        pending_raw = list((run.config or {}).get("pending_injections") or [])
        pending = parse_pending_injections(pending_raw)
        if pending_raw:
            merged = dict(run.config or {})
            merged["pending_injections"] = []
            await self._repo.update_run_config(run_id, merged)
            run.config = merged

        interaction_bus = InteractionBus()
        world.interaction_bus = interaction_bus
        triggered = event_scheduler.evaluate_tick_start(
            world,
            pending_injections=pending,
            interaction_bus=interaction_bus,
        )
        for world_event in triggered:
            await self._emit_world_event(sink, run_id, tick, world_event, world.modifiers)

        agent_rows = await self._repo.list_agents(run_id)
        personas = [SimPersona.model_validate(row.persona) for row in agent_rows]
        use_scripted, llm, llm_cfg = await self._resolve_tick_mode(run, user_id=user_id)

        self._tick_db_lock = asyncio.Lock()
        metrics: TickMetrics | None = None
        try:
            activation = self._activation.select(
                ActivationContext(world=world, personas=personas, tick=tick, hour=hour)
            )
            for persona in activation.skipped:
                await apply_schedule_fallback(world, persona)
                state = world.agents[persona.agent_id].to_state()
                await self._emit_agent_state(sink, run_id, tick, state)

            outcomes: list[AgentTickOutcome] = []

            async def _on_agent_done(persona: SimPersona, outcome: AgentTickOutcome) -> None:
                await self._emit_agent_action(sink, run_id, tick, outcome)
                state = world.agents[persona.agent_id].to_state()
                await self._emit_agent_state(sink, run_id, tick, state)

            if use_scripted:
                if activation.activated:
                    outcomes = await run_scripted_ticks(
                        world=world, personas=list(activation.activated)
                    )
                    for persona, outcome in zip(
                        activation.activated, outcomes, strict=True
                    ):
                        await _on_agent_done(persona, outcome)
                interaction_bus.collect_from_outcomes(world, outcomes)
                # Scripted path has no LLM protocols — emit deterministic demo
                # pulses (conversation/trade/vote + occasional preset world_event),
                # then drain announcement-queued votes so injects are not dropped.
                demo_interactions, demo_world_events = await run_scripted_demo_pulse(world)
                interaction_results = list(demo_interactions)
                if demo_world_events:
                    event_scheduler.apply_events(
                        world, demo_world_events, interaction_bus=interaction_bus
                    )
                    for world_event in demo_world_events:
                        await self._emit_world_event(
                            sink, run_id, tick, world_event, world.modifiers
                        )
                drained = await drain_scripted_pending(world, interaction_bus)
                interaction_results.extend(drained)
                for result in interaction_results:
                    await self._emit_interaction(sink, run_id, tick, result)
                    state_ids = {result.initiator_id}
                    if result.target_id:
                        state_ids.add(result.target_id)
                    for agent_id in state_ids:
                        agent = world.agents.get(agent_id)
                        if agent is not None:
                            await self._emit_agent_state(
                                sink, run_id, tick, agent.to_state()
                            )
                apply_social_updates(world, outcomes)
                apply_tick_memories(world, outcomes, activation.skipped)
            else:
                assert llm is not None and llm_cfg is not None
                router = SimModelRouter.from_run_config(
                    run.config, fallback=llm_cfg.model
                )
                if not (run.config or {}).get("model_routing"):
                    merged = dict(run.config or {})
                    merged.update(router.to_manifest())
                    await self._repo.update_run_config(run_id, merged)
                text_mode = resolve_text_mode(llm_cfg.base_url, override=self._text_mode)
                turn_model = router.model_for_decision(SimDecisionKind.ROUTINE_TICK)
                interaction_model = router.model_for_decision(SimDecisionKind.INTERACTION)
                reflection_model = router.model_for_decision(SimDecisionKind.REFLECTION)

                if activation.activated:
                    batch = await run_agent_ticks_batch(
                        world=world,
                        personas=activation.activated,
                        llm=llm,
                        run_id=run_id,
                        text_mode=text_mode,
                        turn_model=turn_model,
                        options=TickBatchOptions(
                            max_parallel=settings.max_parallel_agents,
                            timeout_seconds=settings.agent_tick_timeout_seconds,
                        ),
                        on_agent_done=_on_agent_done,
                    )
                    outcomes = list(batch.outcomes)

                interaction_bus.collect_from_outcomes(world, outcomes)

                async def _on_interaction_done(result) -> None:
                    await self._emit_interaction(sink, run_id, tick, result)
                    state_ids = {result.initiator_id}
                    if result.target_id:
                        state_ids.add(result.target_id)
                    for agent_id in state_ids:
                        agent = world.agents.get(agent_id)
                        if agent is not None:
                            await self._emit_agent_state(
                                sink, run_id, tick, agent.to_state()
                            )

                interaction_results = await interaction_bus.process_tick(
                    InteractionTickContext(
                        world=world,
                        personas=personas,
                        llm=llm,
                        model=interaction_model,
                        run_id=run_id,
                        tick=tick,
                        on_result=_on_interaction_done,
                    )
                )

                apply_social_updates(world, outcomes)
                apply_tick_memories(world, outcomes, activation.skipped)
                await apply_reflections(
                    tick=tick,
                    agents=[
                        (p, world.agents[p.agent_id])
                        for p in personas
                        if p.agent_id in world.agents
                    ],
                    llm=llm,
                    model=reflection_model,
                )

            metrics = MetricsAggregator().aggregate(
                world, tick_interactions=interaction_results
            )
            final = await persist_tick(
                self._repo, run_id, world, status="running", metrics=metrics
            )
        finally:
            world.interaction_bus = None
            self._tick_db_lock = None

        await self._emit_tick_ended(
            sink, run_id, tick, hour, len(personas), metrics=metrics
        )
        return final

    async def _resolve_tick_mode(
        self, run, *, user_id: str
    ) -> tuple[bool, object | None, object | None]:
        """Decide LLM vs scripted for this tick.

        Scripted when DeepSeek cannot be resolved **or** the run/env opts in
        (``SIMULATION_SCRIPTED`` / create body ``scripted`` / ``manifest.scripted``).
        Default production path (no opt-in + DeepSeek available) still uses LLM.
        """
        opt_in = self._scripted_opt_in(run.config)
        try:
            llm, llm_cfg = await build_sim_provider(self._repo._session, user_id)
        except SimLlmNotConfigured:
            logger.warning(
                "simulation.scripted_fallback",
                run_id=getattr(run, "id", None),
                reason="no_deepseek",
                opt_in=opt_in,
            )
            return True, None, None
        if opt_in:
            logger.info(
                "simulation.scripted_opt_in",
                run_id=getattr(run, "id", None),
                reason="explicit",
            )
            return True, None, None
        return False, llm, llm_cfg

    @staticmethod
    def _scripted_opt_in(run_config: dict | None) -> bool:
        if settings.simulation_scripted:
            return True
        cfg = run_config or {}
        if cfg.get("scripted"):
            return True
        manifest = cfg.get("manifest") or {}
        return bool(manifest.get("scripted"))

    async def _load_world(self, run_id: str, current_tick: int, *, scenario: str = "town"):
        last_tick = await self._repo.get_tick(run_id, current_tick) if current_tick else None
        if current_tick > 0 and (not last_tick or not last_tick.snapshot):
            raise ValidationError(
                f"Run {run_id} at tick {current_tick} has no persisted snapshot (missing_tick_snapshot)",
            )
        if last_tick and last_tick.snapshot:
            snap = SimTickSnapshot.model_validate(last_tick.snapshot)
            world = self._seed_world(scenario)
            world.load_snapshot(snap)
        else:
            world = self._seed_world(scenario)
        agents = await self._repo.list_agents(run_id)
        for row in agents:
            state = SimulationRepository.agent_state_from_row(row)
            if state.agent_id in world.agents:
                agent = world.agents[state.agent_id]
                agent.mood = state.mood
                agent.goal = state.goal
                agent.money = state.money
        return world

    @staticmethod
    def _seed_world(scenario: str):
        if scenario == "show":
            from agentcore.simulation.scenarios.show.config import seed_show_world

            return seed_show_world()
        return seed_town_world()

    async def inject_event(
        self,
        run_id: str,
        *,
        user_id: str,
        event_type: str,
        payload: dict | None = None,
    ):
        run = await self._repo.get_run(run_id, user_id=user_id)
        if run is None:
            raise KeyError("run not found")
        if run.status == "paused":
            pass  # injections allowed while paused
        event = build_preset_event(event_type, tick=run.current_tick + 1, payload=payload)
        pending = list((run.config or {}).get("pending_injections") or [])
        pending.append(event.model_dump())
        await self._repo.merge_run_config(run_id, {"pending_injections": pending})
        return event

    async def patch_agent(
        self,
        run_id: str,
        agent_id: str,
        *,
        user_id: str,
        mood: float | None = None,
        goal: str | None = None,
        money: float | None = None,
    ):
        run = await self._repo.get_run(run_id, user_id=user_id)
        if run is None:
            raise KeyError("run not found")
        if mood is None and goal is None and money is None:
            raise ValidationError("至少提供一个可修改字段：mood / goal / money")
        state = await self._repo.patch_agent_fields(
            run_id,
            agent_id,
            mood=mood,
            goal=goal,
            money=money,
        )
        sink = await self._streams.get(run_id)
        if sink is not None:
            tick = run.current_tick
            await self._emit_agent_state(sink, run_id, tick, state)
        return state

    async def get_tick_snapshot(
        self, run_id: str, tick_number: int, *, user_id: str
    ) -> SimTickSnapshot | None:
        run = await self._repo.get_run(run_id, user_id=user_id)
        if run is None:
            return None
        if tick_number < 1 or tick_number > run.current_tick:
            return None
        row = await self._repo.get_tick(run_id, tick_number)
        if row is None:
            return None
        return SimTickSnapshot.model_validate(row.snapshot)

    async def get_run_manifest(self, run_id: str, *, user_id: str) -> RunManifest | None:
        run = await self._repo.get_run(run_id, user_id=user_id)
        if run is None:
            return None
        raw = (run.config or {}).get("manifest")
        if not raw:
            return None
        return RunManifest.model_validate(raw)

    async def list_run_metrics(self, run_id: str, *, user_id: str) -> list[TickMetrics] | None:
        run = await self._repo.get_run(run_id, user_id=user_id)
        if run is None:
            return None
        return await self._repo.list_tick_metrics(run_id)

    async def replay_ticks(
        self,
        run_id: str,
        *,
        user_id: str,
        from_tick: int,
        to_tick: int,
    ) -> list[SSEEvent]:
        run = await self._repo.get_run(run_id, user_id=user_id)
        if run is None:
            raise KeyError("run not found")
        if from_tick < 1:
            raise ValidationError("from 必须 >= 1")
        if to_tick < from_tick:
            raise ValidationError("to 必须 >= from")
        if from_tick > run.current_tick:
            raise ValidationError(
                f"from ({from_tick}) 超出 run 当前 tick ({run.current_tick})"
            )
        if to_tick > run.current_tick:
            raise ValidationError(
                f"to ({to_tick}) 超出 run 当前 tick ({run.current_tick})"
            )

        events = await self._repo.list_events_in_range(run_id, from_tick, to_tick)
        ticks = await self._repo.list_ticks_in_range(run_id, from_tick, to_tick)
        snapshots = {row.tick_number: row.snapshot for row in ticks}

        replay: list[SSEEvent] = []
        for tick_number in range(from_tick, to_tick + 1):
            for row in events:
                if row.tick_number != tick_number:
                    continue
                event_type = _replay_event_type(row.event_type)
                if event_type is None:
                    continue
                replay.append(SSEEvent(type=event_type, payload=row.payload))
            snap_raw = snapshots.get(tick_number)
            if snap_raw:
                replay.append(
                    SSEEvent(
                        type=EventType.SIM_TICK_FRAME,
                        payload={
                            "run_id": run_id,
                            "tick_number": tick_number,
                            "snapshot": snap_raw,
                        },
                    )
                )
        return replay

    async def stream_sink(self, run_id: str) -> EventSink | None:
        return await self._streams.get(run_id)

    async def _persist_and_emit(
        self,
        sink: EventSink,
        run_id: str,
        tick_number: int,
        event_type: EventType,
        payload: dict,
    ) -> None:
        sink.emit(SSEEvent(type=event_type, payload=payload))
        if self._tick_db_lock is not None:
            async with self._tick_db_lock:
                await self._repo.append_event(
                    run_id,
                    tick_number=tick_number,
                    event_type=event_type.value,
                    payload=payload,
                )
            return
        await self._repo.append_event(
            run_id,
            tick_number=tick_number,
            event_type=event_type.value,
            payload=payload,
        )

    async def _emit_tick_started(self, sink: EventSink, run_id: str, tick: int, hour: int) -> None:
        payload = SimTickStartedPayload(run_id=run_id, tick=tick, hour=hour).model_dump()
        await self._persist_and_emit(sink, run_id, tick, EventType.SIM_TICK_STARTED, payload)

    async def _emit_tick_ended(
        self,
        sink: EventSink,
        run_id: str,
        tick: int,
        hour: int,
        agent_count: int,
        *,
        metrics: TickMetrics | None = None,
    ) -> None:
        payload = SimTickEndedPayload(
            run_id=run_id,
            tick=tick,
            hour=hour,
            agent_count=agent_count,
            metrics=metrics,
        ).model_dump()
        await self._persist_and_emit(sink, run_id, tick, EventType.SIM_TICK_ENDED, payload)

    async def _emit_agent_state(self, sink: EventSink, run_id: str, tick: int, state) -> None:
        payload = SimAgentStatePayload(
            run_id=run_id, tick=tick, state=state
        ).model_dump()
        await self._persist_and_emit(sink, run_id, tick, EventType.SIM_AGENT_STATE, payload)

    async def _emit_agent_action(
        self, sink: EventSink, run_id: str, tick: int, outcome: AgentTickOutcome
    ) -> None:
        payload = SimAgentActionPayload(
            run_id=run_id, tick=tick, action=outcome.action
        ).model_dump()
        await self._persist_and_emit(sink, run_id, tick, EventType.SIM_AGENT_ACTION, payload)

    async def _emit_world_event(
        self, sink: EventSink, run_id: str, tick: int, event, modifiers
    ) -> None:
        wire = WorldEventWire(
            event_id=event.event_id,
            kind=event.kind.value,
            event_type=event.event_type,
            title=event.title,
            description=event.description,
            payload=dict(event.payload),
            tick_started=event.tick_started,
            duration_ticks=event.duration_ticks,
            source=event.source,
        )
        mods = WorldModifiersWire(**modifiers.model_dump())
        payload = SimWorldEventPayload(
            run_id=run_id,
            tick=tick,
            event=wire,
            modifiers=mods,
        ).model_dump()
        await self._persist_and_emit(sink, run_id, tick, EventType.SIM_WORLD_EVENT, payload)

    async def _emit_interaction(self, sink: EventSink, run_id: str, tick: int, result) -> None:
        payload = SimInteractionPayload(
            run_id=run_id, tick=tick, interaction=result
        ).model_dump()
        sink.emit(SSEEvent(type=EventType.SIM_INTERACTION, payload=payload))
        if self._tick_db_lock is not None:
            async with self._tick_db_lock:
                await self._repo.append_event(
                    run_id,
                    tick_number=tick,
                    event_type=result.kind,
                    payload=payload,
                )
            return
        await self._repo.append_event(
            run_id,
            tick_number=tick,
            event_type=result.kind,
            payload=payload,
        )


def simulation_enabled() -> bool:
    return settings.simulation_enabled


_INTERACTION_EVENT_TYPES = frozenset({"conversation", "trade", "vote"})
_RETIRED_SIM_SHOW_PREFIX = "sim.show."


def _replay_event_type(raw: str) -> EventType | None:
    """Map a persisted sim_event.event_type onto a live SSE EventType.

    Retired ``sim.show.*`` overlay names and retired interaction event names may
    still sit on historical rows; skip them. Unknown names still raise.
    """
    if raw.startswith(_RETIRED_SIM_SHOW_PREFIX) or raw in RETIRED_EVENT_TYPE_VALUES:
        return None
    if raw in _INTERACTION_EVENT_TYPES:
        return EventType.SIM_INTERACTION
    for event_type in EventType:
        if event_type.value == raw:
            return event_type
    raise ValidationError(f"未知回放事件类型: {raw}")
