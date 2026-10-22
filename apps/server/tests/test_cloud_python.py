"""Cloud sandbox Python list is a single file; consumers must read it.

Hand-copied inventories in Dockerfile / docs
drifted (pillow vs Pillow). These tests lock the wiring: the list file is the
only inventory, and editing a consumer copy in isolation goes red.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentcore.tools.builtin.run import run_description
from agentcore.tools.sandbox.cloud_python import (
    DOCKER_CONTEXT_PATH,
    LIST_FILE,
    format_cloud_python_libs,
    load_cloud_python_packages,
)

_REPO = Path(__file__).resolve().parents[3]
_DOCKERFILE = _REPO / "apps" / "server" / "Dockerfile.guest"
_RUNTIME_DOCKERFILE = _REPO / "apps" / "server" / "Dockerfile"
_RUN_TOOL = (
    _REPO / "apps" / "server" / "agentcore" / "tools" / "builtin" / "run.py"
)
_DEPLOY_DOC = _REPO / "docs" / "05-平台与运维" / "部署拓扑与环境.md"

# Explicitly rejected additions (also in the list-file header). Not an inventory.
_REJECTED = frozenset(
    {
        "camelot",
        "pymupdf",
        "pytesseract",
        "geopandas",
        "scipy",
        "pyarrow",
        "xlrd",
        "py7zr",
    }
)

_PIP_FROM_LIST = re.compile(
    r"pip install\s+[^\n]*-r\s+\S*cloud_python\.txt",
    re.IGNORECASE,
)


def _token_hits(line: str, packages: tuple[str, ...]) -> list[str]:
    return [
        name
        for name in packages
        if re.search(rf"(^|[\s,、\\=/]){re.escape(name)}([\s,、\\]|$)", line)
    ]


def _dockerfile_errors(text: str) -> list[str]:
    errors: list[str] = []
    if DOCKER_CONTEXT_PATH not in text:
        errors.append("Dockerfile does not reference the list file path")
    if _PIP_FROM_LIST.search(text) is None:
        errors.append("Dockerfile pip install does not use -r cloud_python.txt")
    packages = load_cloud_python_packages()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        hits = _token_hits(stripped, packages)
        if hits:
            errors.append(
                f"Dockerfile inlines {hits!r}; package names belong only in the list file"
            )
    return errors


def _literal_errors(source: str, *, where: str) -> list[str]:
    errors: list[str] = []
    for name in load_cloud_python_packages():
        if name in source:
            errors.append(f"{where} hardcodes {name!r}")
    return errors


def test_list_file_is_the_inventory():
    assert LIST_FILE.is_file()
    packages = load_cloud_python_packages()
    assert packages
    assert "pdfplumber" in packages
    assert "python-pptx" in packages
    for spec in packages:
        assert not re.search(r"[=<>~]", spec), spec
        assert spec.lower() not in _REJECTED, spec
    # Comments mention vetoes; requirement lines must not.
    raw = LIST_FILE.read_text(encoding="utf-8")
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert stripped.split("#", 1)[0].strip() in packages


def test_server_description_is_rendered_from_the_list():
    libs = format_cloud_python_libs()
    for name in load_cloud_python_packages():
        assert name in libs
    # Schema is location-only; inventory lives on the formatter.
    assert libs not in run_description("server")
    assert libs not in run_description("local")
    assert libs not in run_description()


def test_dockerfile_installs_from_the_list_file():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert _dockerfile_errors(text) == []


def test_runtime_dockerfile_copies_named_guest_image():
    text = _RUNTIME_DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM guest_image AS guest" in text
    assert "COPY --from=guest /guest-root /opt/agentcore/guest-rootfs" in text
    assert "cloud_python.txt" not in text
    packages = load_cloud_python_packages()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert not _token_hits(stripped, packages), line


def test_run_sources_have_no_inventory_literals():
    src = _RUN_TOOL.read_text(encoding="utf-8")
    assert _literal_errors(src, where="run.py") == []


def test_deploy_doc_points_at_the_list_file():
    text = _DEPLOY_DOC.read_text(encoding="utf-8")
    assert "cloud_python.txt" in text
    # Old slash-separated inventory must not return.
    assert "python-pptx / python-docx / openpyxl" not in text
    assert _literal_errors(
        # Only the 镜像 bullet was the copy; the pptx smoke step may name one lib.
        text.split("**部署后人工验证清单**")[0],
        where="部署拓扑 镜像段",
    ) == []


def test_inline_dockerfile_list_goes_red():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    broken = text.replace(
        'pip install -i "${PYPI_INDEX}" -r /tmp/cloud_python.txt',
        'pip install -i "${PYPI_INDEX}" python-pptx pillow',
    )
    assert broken != text
    errors = _dockerfile_errors(broken)
    assert errors, "inlining packages in Dockerfile must fail the gate"


def test_hardcoded_description_list_goes_red():
    src = _RUN_TOOL.read_text(encoding="utf-8")
    broken = src.replace("跑命令 / 启服", "跑命令 / 启服 python-pptx、pillow")
    assert broken != src
    errors = _literal_errors(broken, where="run.py")
    assert errors, "hand-copying names into run.py must fail the gate"

