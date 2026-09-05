"""Capability catalog: the 能力图鉴 the desktop renders.

One aggregate endpoint over the platform's agent capabilities — every tool (CEO +
worker, annotated with who may call it), the system Skills registered at runtime
(platform + deployment-enabled packs; identical for every user), deployment-listed
capability packs as a display catalog, the CEO system-prompt template, and worker
identity templates. Skills / packs are derived from the SAME sources the runtime
wires so the 图鉴 never drifts from the live turn.
"""

from fastapi import APIRouter

from agentcore.api.dependencies import AuthUser
from agentcore.api.schemas import (
    CapabilitiesResponse,
    CapabilityGuidelines,
    CapabilityPack,
    CapabilitySkill,
    CapabilityTool,
)
from agentcore.runtime.capability_packs import enabled_packs, listed_packs
from agentcore.runtime.resolve.prompt import (
    assemble_system_prompt,
    compose_ceo_chat_prompt,
    derive_ceo_addon,
)
from agentcore.runtime.runs.executor.identities import (
    build_worker_identity_catalog,
)
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.tools.catalog import AVAILABLE_TO_CEO, build_capability_catalog

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("", response_model=CapabilitiesResponse)
async def get_capabilities(_user: AuthUser) -> CapabilitiesResponse:
    """The complete capability picture: tools, runtime skills, listed packs,
    and the CEO / worker system-prompt templates — the data behind 工具箱 → 能力图鉴."""
    catalog = build_capability_catalog()
    tools = [
        CapabilityTool(
            name=entry.schema.name,
            description=entry.schema.description,
            category=entry.schema.category,
            approval=entry.schema.approval,
            parameters=entry.schema.parameters,
            available_to=list(entry.available_to),
        )
        for entry in catalog
    ]

    packs_on = enabled_packs()
    skill_registry = build_system_skill_registry(enabled_packs=packs_on)
    skills = [
        CapabilitySkill(name=skill.name, summary=skill.summary, body=skill.body)
        for skill in skill_registry.list_all()
    ]

    packs = [
        CapabilityPack(
            id=pack.id,
            name=pack.name,
            summary=pack.summary,
            skills=[
                CapabilitySkill(name=s.name, summary=s.summary, body=s.body)
                for s in pack.skills()
            ],
        )
        for pack in listed_packs()
    ]

    # Templates, not per-turn prompts: CEO compose uses the catalog's CEO tool names
    # so the 按需目录 reflects the full repertoire; worker identities share the live
    # ``<身份>`` builder (form HOW is per-turn 交付物规格, not catalogued here).
    # Memory / attachments stay out — this is the deployment-wide blueprint.
    ceo_tool_names = {
        entry.schema.name for entry in catalog if AVAILABLE_TO_CEO in entry.available_to
    }
    shared_base = assemble_system_prompt()
    ceo = compose_ceo_chat_prompt(
        shared_base,
        skill_registry=skill_registry,
        ceo_tool_names=ceo_tool_names,
    )
    guidelines = CapabilityGuidelines(
        shared_base=shared_base,
        worker_leaf=build_worker_identity_catalog(captain=False),
        worker_captain=build_worker_identity_catalog(captain=True),
        ceo_addon=derive_ceo_addon(shared_base, ceo),
        ceo=ceo,
    )

    return CapabilitiesResponse(
        tools=tools, skills=skills, packs=packs, guidelines=guidelines
    )
