"""Creation-tool 文档 request/response schemas (folder-hung block body)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CreateDocRequest(BaseModel):
    folder_id: str
    title: str | None = None


class UpdateDocRequest(BaseModel):
    title: str | None = None


class DocSummary(BaseModel):
    id: str
    title: str
    folder_id: str
    folder_name: str
    version: int
    can_write: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocDetail(DocSummary):
    """A doc plus its full block body (editor load payload)."""

    body: dict[str, Any]


class DocBodyWriteRequest(BaseModel):
    body: dict[str, Any]
    # Version the edit was based on. None = unconditional overwrite.
    baseline: int | None = None


class DocWriteResult(BaseModel):
    ok: bool
    version: int
    conflict: bool = False
    doc: DocDetail | None = None
