from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentcore.workspace.protocol import WorkspaceBackend

_PROFILE_MAX_COMMANDS = 5


@dataclass(frozen=True)
class WorkspaceProfile:
    """Best-effort workspace code fingerprint (languages / frameworks / commands)."""

    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    monorepo_tool: str | None = None
    vcs: str | None = None
    branch: str | None = None
    test_commands: list[str] = field(default_factory=list)
    typecheck_commands: list[str] = field(default_factory=list)
    build_commands: list[str] = field(default_factory=list)
    run_commands: list[str] = field(default_factory=list)
    agents_md_excerpt: str | None = None


def _js_package_manager(content: str) -> str:
    if '"packageManager"' in content and "pnpm" in content:
        return "pnpm"
    if '"packageManager"' in content and "yarn" in content:
        return "yarn"
    return "npm"


def _format_js_run_command(pm: str, script: str) -> str:
    if pm == "yarn":
        return f"yarn {script}"
    return f"{pm} run {script}"


def _detect_js_run_commands(content: str, pm: str) -> list[str]:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return []
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return []
    commands: list[str] = []
    for name in ("start", "dev"):
        if name in scripts:
            commands.append(_format_js_run_command(pm, name))
    return commands


def _parse_toml(content: str) -> dict:
    try:
        return tomllib.loads(content)
    except TypeError:
        return tomllib.loads(content.encode())


def _detect_python_run_command(content: str, package_managers: list[str]) -> str | None:
    try:
        data = _parse_toml(content)
    except tomllib.TOMLDecodeError:
        return None
    scripts = data.get("project", {}).get("scripts")
    if not isinstance(scripts, dict) or not scripts:
        return None
    first_name = next(iter(scripts))
    pm = "uv" if "uv" in package_managers else "pip"
    return f"{pm} run {first_name}"


async def detect_workspace_profile(backend: WorkspaceBackend) -> WorkspaceProfile:
    """Detect workspace type from files. Best-effort, never raises."""
    languages: list[str] = []
    frameworks: list[str] = []
    package_managers: list[str] = []
    monorepo_tool: str | None = None
    vcs: str | None = None
    branch: str | None = None
    test_commands: list[str] = []
    typecheck_commands: list[str] = []
    build_commands: list[str] = []
    run_commands: list[str] = []
    agents_md_excerpt: str | None = None

    try:
        content = await backend.read("pyproject.toml")
        if content:
            languages.append("python")
            if "fastapi" in content.lower():
                frameworks.append("fastapi")
            if "django" in content.lower():
                frameworks.append("django")
            if "[tool.uv]" in content or "uv" in content:
                package_managers.append("uv")
            elif "[tool.poetry]" in content:
                package_managers.append("poetry")
            else:
                package_managers.append("pip")
            if "pytest" in content:
                test_commands.append("pytest")
            if "mypy" in content:
                typecheck_commands.append(
                    "uv run mypy" if "uv" in package_managers else "mypy"
                )
            py_run = _detect_python_run_command(content, package_managers)
            if py_run:
                run_commands.append(py_run)
    except Exception:
        pass

    try:
        await backend.read("requirements.txt")
        if "python" not in languages:
            languages.append("python")
            package_managers.append("pip")
    except Exception:
        pass

    try:
        content = await backend.read("package.json")
        if content:
            if "typescript" not in languages:
                if '"typescript"' in content or "tsconfig" in content:
                    languages.append("typescript")
                else:
                    languages.append("javascript")
            pm = _js_package_manager(content)
            package_managers.append(pm)
            try:
                pkg = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                pkg = None
            scripts = pkg.get("scripts") if isinstance(pkg, dict) else None
            if isinstance(scripts, dict) and "test" in scripts:
                # Prefer pm test (whitelist argv for test_run); framework comes from
                # scripts.test body (vitest/jest) — never invent jest from key alone.
                test_commands.append("npm test" if pm == "npm" else f"{pm} test")
            elif '"test"' in content:
                # Loose fallback when JSON parse failed but key is present.
                test_commands.append("npm test" if pm == "npm" else f"{pm} test")
            if isinstance(scripts, dict):
                if "typecheck" in scripts or "type-check" in scripts:
                    script = "typecheck" if "typecheck" in scripts else "type-check"
                    typecheck_commands.append(_format_js_run_command(pm, script))
                if "build" in scripts:
                    build_commands.append(_format_js_run_command(pm, "build"))
            else:
                if '"typecheck"' in content or '"type-check"' in content:
                    script = "typecheck" if '"typecheck"' in content else "type-check"
                    typecheck_commands.append(_format_js_run_command(pm, script))
                if '"build"' in content:
                    build_commands.append(_format_js_run_command(pm, "build"))
            run_commands.extend(_detect_js_run_commands(content, pm))
    except Exception:
        pass

    try:
        await backend.read("tsconfig.json")
        if not typecheck_commands:
            typecheck_commands.append("npx tsc --noEmit")
    except Exception:
        pass

    try:
        await backend.read("pnpm-workspace.yaml")
        monorepo_tool = "pnpm workspaces"
    except Exception:
        pass

    try:
        content = await backend.read("turbo.json")
        if content:
            monorepo_tool = "turborepo"
    except Exception:
        pass

    try:
        await backend.read("nx.json")
        monorepo_tool = "nx"
    except Exception:
        pass

    try:
        head_content = await backend.read(".git/HEAD")
        if head_content:
            vcs = "git"
            if head_content.startswith("ref: refs/heads/"):
                branch = head_content.strip().removeprefix("ref: refs/heads/")
    except Exception:
        pass

    for agents_file in ("AGENTS.md", "CLAUDE.md"):
        try:
            content = await backend.read(agents_file)
            if content:
                excerpt = content[:400]
                if len(content) > 400:
                    excerpt += "\n..."
                agents_md_excerpt = excerpt
                break
        except Exception:
            pass

    return WorkspaceProfile(
        languages=languages,
        frameworks=frameworks,
        package_managers=package_managers,
        monorepo_tool=monorepo_tool,
        vcs=vcs,
        branch=branch,
        test_commands=test_commands,
        typecheck_commands=typecheck_commands,
        build_commands=build_commands,
        run_commands=run_commands,
        agents_md_excerpt=agents_md_excerpt,
    )


def render_workspace_profile(profile: WorkspaceProfile) -> str:
    """Render profile as concise text. ≤600 chars.

    Prompt injection does **not** use this — ``build_workspace_overview`` only
    emits a name pointer for ``AGENTS.md`` / ``CLAUDE.md``. ``run_verify`` reads
    :class:`WorkspaceProfile` fields directly.
    """
    if not profile.languages and not profile.vcs:
        return ""

    parts: list[str] = []

    if profile.languages:
        lang_str = ", ".join(profile.languages)
        if profile.monorepo_tool:
            parts.append(f"类型：{lang_str} monorepo（{profile.monorepo_tool}）")
        else:
            parts.append(f"主要语言：{lang_str}")

    if profile.frameworks:
        parts.append(f"框架：{', '.join(profile.frameworks)}")

    if profile.package_managers:
        parts.append(f"包管理：{', '.join(profile.package_managers)}")

    if profile.vcs:
        vcs_str = profile.vcs
        if profile.branch:
            vcs_str += f"（分支 {profile.branch}）"
        parts.append(f"版本控制：{vcs_str}")

    commands = (
        profile.test_commands
        + profile.typecheck_commands
        + profile.build_commands
        + profile.run_commands
    )
    if commands:
        shown = commands[:_PROFILE_MAX_COMMANDS]
        parts.append(f"常用命令：{' · '.join(shown)}")

    result = "\n".join(f"- {p}" for p in parts)

    if profile.agents_md_excerpt:
        result += f"\n- 工程约定摘录：\n  > {profile.agents_md_excerpt[:200]}"

    if len(result) > 600:
        result = result[:597] + "..."

    return result
